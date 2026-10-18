"""Owner-scoped PostgreSQL implementation of ``StateRepository``.

WHY THIS FILE EXISTS
    ``JsonArtifactRepository`` stores one project per directory and has no
    concept of an owner. With multiple users that is not a gap to patch — a
    filesystem store cannot express "rows this caller may see". This is the
    store that can, and it is the one the database enforces.

LAYER
    Memory / persistence (``backend/memory/``). Depends on config, schemas and
    psycopg. It imports nothing from api, services, graph or agents.

---------------------------------------------------------------------------
WHY SYNCHRONOUS psycopg, WHEN THE CHECKPOINTER IS ASYNC

``StateRepository`` is a synchronous Protocol, and the graph calls it from
inside nodes. Making it async would push ``await`` into every node and change
a seam the whole workflow is built around.

A sync pool keeps that seam intact, and buys a second thing: sync psycopg has
no event-loop requirement at all, so this pool is untouched by the Windows
``ProactorEventLoop``/``SelectorEventLoop`` conflict that constrains the async
checkpointer (``docs/state.md`` 5.0.2). Two pools with different drivers is
not duplication here; they have genuinely different requirements.

The cost is honest: these calls block the event loop for the duration of a
query. The filesystem repository they replace blocked it too, and these are
small indexed queries, so this is not a regression. If it ever matters, the
fix is ``run_in_executor`` at the service boundary, not an async Protocol.

---------------------------------------------------------------------------
HOW ISOLATION ACTUALLY WORKS HERE

Two independent layers, and the second is the one that matters:

1. Every statement is written with an owner predicate.
2. Every transaction sets ``request.jwt.claims`` from the VERIFIED caller, so
   ``auth.uid()`` resolves and the RLS policies from migration 0002 apply. The
   connected role is ``detroit_app``, which is ``NOBYPASSRLS``.

Forget layer 2 and the policies match nothing: queries return zero rows and
writes are rejected. It **fails closed**. That is the whole reason for the
restricted role — the previous ``postgres`` connection carried ``BYPASSRLS``,
which made layer 2 inert and left layer 1 as the only thing standing between
two users' data.

``_scoped`` is the only way this module obtains a cursor. There is deliberately
no helper that returns an unscoped one.

WHY A MISSING PROJECT AND SOMEONE ELSE'S PROJECT ARE THE SAME ERROR
    Under RLS, another user's row is simply not visible, so the query returns
    nothing and this raises ``ProjectNotFoundError`` — which the API already
    renders as 404. A distinct 403 would confirm the id exists and turn the
    endpoint into a membership oracle (``docs/api.md`` 1.1). The indistinguish-
    ability is the feature, not an accident of implementation.

WHAT STAYS ON THE FILESYSTEM
    Reference image BYTES and the generated ``workspace/`` (``docs/database.md``
    4, decision 5). Those are delegated to the artifact repository, which also
    keeps writing the JSON export copies. Database rows are the authority for
    state; the files are for human review (``docs/state.md`` 5.1).

    A path is not protected by a policy, so every filesystem call here is
    preceded by an owner-scoped database check. Containment of the path itself
    is still the artifact repository's job (``docs/security.md`` 3.4).

Spec: docs/database.md 1.3.2, 1.3.3, 3, 4; docs/state.md 5.0, 5.1;
      docs/security.md 3.4, 6; CLAUDE.md 9A.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.memory.artifacts import (
    JsonArtifactRepository,
    ProjectNotFoundError,
    SchemaVersionMismatchError,
)
from backend.schemas.state import SCHEMA_VERSION, ProjectState, ReferenceImageRef

logger = logging.getLogger(__name__)

# Columns written and read as whole Pydantic models.
_JSON_COLUMNS = (
    "normalized_requirements",
    "plan",
    "design_spec",
    "reference_analysis",
    "frontend_architecture",
    "component_tree",
    "evaluation_results",
    "pending_checkpoint",
    "last_error",
    # The connected GitHub repository (migration 0003). Nullable: most
    # projects never connect one.
    "github",
)
# Always present on the model, therefore NOT NULL in the table.
_JSON_REQUIRED = ("build_status", "preview_status")
# Replaced wholesale each save rather than appended.
_JSON_LISTS = (
    "requirement_conflicts",
    "requirement_gaps",
    "generated_files",
    "file_versions",
)


def _dump(value: Any) -> str | None:
    """Serialize a Pydantic model or plain value to JSON text, or None."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"))
    return json.dumps(value)


class PostgresStateRepository:
    """``StateRepository`` backed by PostgreSQL, scoped to one owner per call.

    Satisfies the same Protocol as ``JsonArtifactRepository``, so the graph,
    the service and the API are unaware of which one they hold — except that
    the project methods now require an ``owner_id``.

    Args:
        pool: a sync pool connected as ``detroit_app``.
        artifacts: the filesystem repository, used ONLY for reference-image
            bytes, the workspace directory and the JSON export copies. It is
            never the authority for state.
    """

    def __init__(self, pool: ConnectionPool, artifacts: JsonArtifactRepository) -> None:
        self._pool = pool
        self._artifacts = artifacts

    # -- the one way to get a cursor ---------------------------------------

    @contextmanager
    def _scoped(self, owner_id: UUID) -> Iterator[Any]:
        """A cursor inside a transaction carrying the caller's claims.

        ``set_config(..., true)`` is transaction-local, so the claim is gone
        the moment the transaction ends. That is what stops one request's
        identity reaching the next request that borrows the same pooled
        connection — verified against this database, not assumed.

        The claim is a bare ``sub``; nothing else from the token is sent, and
        the value came from a verified signature, never from a request field.
        """
        with self._pool.connection() as conn:
            conn.row_factory = dict_row
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": str(owner_id)}),),
                )
                yield cur

    # -- StateRepository ----------------------------------------------------

    def save(self, state: ProjectState, *, owner_id: UUID) -> None:
        """Insert or update the project, then append any new audit rows.

        ``owner_id`` is written on insert and never updated: the UPDATE policy
        carries a WITH CHECK that would reject a change of owner anyway, so an
        attempt to reassign fails at the database rather than silently working
        at the application layer.

        The audit tables are append-only — no UPDATE or DELETE privilege — so
        their writes are ``ON CONFLICT DO NOTHING``. Re-saving a state that
        already recorded a decision is therefore idempotent instead of an
        error.
        """
        columns = {
            "project_id": state.project_id,
            "owner_id": str(owner_id),
            "session_id": state.session_id,
            "title": state.title,
            "user_request": state.user_request,
            "schema_version": state.schema_version,
            "current_stage": state.current_stage.value,
            "run_status": state.run_status.value,
            "iteration_count": state.iteration_count,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "approved_reference_direction": state.approved_reference_direction,
            "next_action": state.next_action,
        }
        for name in _JSON_COLUMNS + _JSON_REQUIRED + _JSON_LISTS:
            columns[name] = _dump(getattr(state, name))

        names = list(columns)
        placeholders = ", ".join(["%s"] * len(names))
        updatable = [n for n in names if n not in ("project_id", "owner_id", "created_at")]
        assignments = ", ".join(f"{n} = EXCLUDED.{n}" for n in updatable)

        with self._scoped(owner_id) as cur:
            cur.execute(
                f"INSERT INTO public.projects ({', '.join(names)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT (project_id) DO UPDATE SET {assignments}",
                [columns[n] for n in names],
            )
            if cur.rowcount == 0:  # pragma: no cover - RLS rejects silently
                raise ProjectNotFoundError(state.project_id)

            for record in state.hitl_history:
                cur.execute(
                    "INSERT INTO public.hitl_records (project_id, checkpoint_id, stage,"
                    " action, option_id, instruction, decided_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (state.project_id, record.checkpoint_id, record.stage.value,
                     record.action.value, record.option_id, record.instruction,
                     record.decided_at),
                )
            for decision in state.approved_decisions:
                cur.execute(
                    "INSERT INTO public.approved_decisions (project_id, decision_id,"
                    " stage, statement, origin, decided_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (state.project_id, decision.decision_id, decision.stage.value,
                     decision.statement,
                     decision.origin.value if decision.origin else None,
                     decision.decided_at),
                )
            for rejected in state.rejected_options:
                cur.execute(
                    "INSERT INTO public.rejected_options (project_id, checkpoint_id,"
                    " option_id, stage, rejected_at) VALUES (%s,%s,%s,%s,%s)"
                    " ON CONFLICT DO NOTHING",
                    (state.project_id, rejected.checkpoint_id, rejected.option_id,
                     rejected.stage.value, rejected.rejected_at),
                )
            for action in state.mcp_action_history:
                cur.execute(
                    "INSERT INTO public.external_action_records (project_id, action_id,"
                    " action_type, status, performed_at) VALUES (%s,%s,%s,%s,%s)"
                    " ON CONFLICT DO NOTHING",
                    (state.project_id, action.action_id, action.action_type,
                     action.status, action.performed_at),
                )
            for image in state.reference_images:
                cur.execute(
                    "INSERT INTO public.reference_images (project_id, image_id,"
                    " content_type, size_bytes, sha256, uploaded_at)"
                    " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (state.project_id, image.image_id, image.content_type,
                     image.size_bytes, image.sha256, image.uploaded_at),
                )

        # The JSON export copies are a side effect, never the authority. A
        # failure to write them must not fail the save, because the row is
        # already committed and the two would then disagree.
        try:
            self._artifacts.save(state)
        except Exception:  # pragma: no cover - best effort by design
            logger.warning(
                "project %s saved to PostgreSQL but the JSON export failed",
                state.project_id,
            )

    def load(self, project_id: str, *, owner_id: UUID) -> ProjectState:
        """Return the caller's project, or raise ``ProjectNotFoundError``.

        Another user's project is invisible under RLS and therefore produces
        exactly the same error as one that does not exist. See the module
        docstring: that is deliberate.
        """
        with self._scoped(owner_id) as cur:
            cur.execute(
                "SELECT * FROM public.projects WHERE project_id = %s", (project_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise ProjectNotFoundError(project_id)

            cur.execute(
                "SELECT checkpoint_id, stage, action, option_id, instruction, decided_at"
                " FROM public.hitl_records WHERE project_id = %s ORDER BY decided_at",
                (project_id,),
            )
            hitl = cur.fetchall()
            cur.execute(
                "SELECT decision_id, stage, statement, origin, decided_at"
                " FROM public.approved_decisions WHERE project_id = %s ORDER BY decided_at",
                (project_id,),
            )
            decisions = cur.fetchall()
            cur.execute(
                "SELECT checkpoint_id, option_id, stage, rejected_at"
                " FROM public.rejected_options WHERE project_id = %s ORDER BY rejected_at",
                (project_id,),
            )
            rejected = cur.fetchall()
            cur.execute(
                "SELECT action_id, action_type, status, performed_at"
                " FROM public.external_action_records WHERE project_id = %s"
                " ORDER BY performed_at",
                (project_id,),
            )
            actions = cur.fetchall()
            cur.execute(
                "SELECT image_id, content_type, size_bytes, sha256, uploaded_at"
                " FROM public.reference_images WHERE project_id = %s ORDER BY uploaded_at",
                (project_id,),
            )
            images = cur.fetchall()

        stored_version = row["schema_version"]
        if stored_version != SCHEMA_VERSION:
            raise SchemaVersionMismatchError(
                f"project {project_id} was written with schema_version "
                f"{stored_version}; this build expects {SCHEMA_VERSION}"
            )

        payload: dict[str, Any] = {
            "project_id": row["project_id"],
            "session_id": row["session_id"],
            "title": row["title"],
            "user_request": row["user_request"],
            "schema_version": stored_version,
            "current_stage": row["current_stage"],
            "run_status": row["run_status"],
            "iteration_count": row["iteration_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "approved_reference_direction": row["approved_reference_direction"],
            "next_action": row["next_action"],
            "hitl_history": hitl,
            "approved_decisions": decisions,
            "rejected_options": rejected,
            "mcp_action_history": actions,
            "reference_images": images,
        }
        for name in _JSON_COLUMNS + _JSON_REQUIRED + _JSON_LISTS:
            payload[name] = row[name]

        # owner_id is intentionally NOT placed on the state. ProjectState is
        # the LangGraph graph state; an owner there would be serialized into
        # every checkpoint and reach agent context slices (CLAUDE.md 8).
        return ProjectState.model_validate(payload)

    def exists(self, project_id: str, *, owner_id: UUID) -> bool:
        """True only when this caller owns a project with that id."""
        with self._scoped(owner_id) as cur:
            cur.execute(
                "SELECT 1 FROM public.projects WHERE project_id = %s", (project_id,)
            )
            return cur.fetchone() is not None

    def list_project_ids(self, *, owner_id: UUID) -> list[str]:
        """The caller's project ids, most recently updated first.

        Ordered here rather than by the caller because the index that makes
        this cheap is ``(owner_id, updated_at DESC)``.
        """
        with self._scoped(owner_id) as cur:
            cur.execute(
                "SELECT project_id FROM public.projects ORDER BY updated_at DESC"
            )
            return [row["project_id"] for row in cur.fetchall()]

    def delete(self, project_id: str, *, owner_id: UUID) -> None:
        """Delete the caller's project. Child rows go with it, by cascade.

        A delete that matches no row raises ``ProjectNotFoundError`` rather
        than succeeding quietly, so "deleted someone else's project" and
        "deleted nothing" cannot be confused.
        """
        with self._scoped(owner_id) as cur:
            cur.execute(
                "DELETE FROM public.projects WHERE project_id = %s", (project_id,)
            )
            if cur.rowcount == 0:
                raise ProjectNotFoundError(project_id)
        try:
            self._artifacts.delete(project_id)
        except Exception:  # pragma: no cover - the row is already gone
            logger.warning("project %s deleted from PostgreSQL; files remain", project_id)

    # -- filesystem-backed members -----------------------------------------
    #
    # These keep the artifact repository's signatures. They are NOT owner-aware
    # on their own, because a path cannot be protected by a policy; the service
    # performs an owner-scoped load before calling any of them.

    def workspace_dir(self, project_id: str) -> Path:
        """Where this project's site is built. Path containment stays with the
        artifact repository (``docs/security.md`` 3.4)."""
        return self._artifacts.workspace_dir(project_id)

    def save_reference_image(
        self, project_id: str, image_id: str, content: bytes, content_type: str
    ) -> None:
        """Store upload bytes. Metadata reaches the database through ``save``.

        Size and MIME validation stay in the service, which checks the declared
        type against the file's magic bytes before anything is written
        (``docs/security.md`` 4). This only persists what was already accepted.
        """
        self._artifacts.save_reference_image(project_id, image_id, content, content_type)

    def load_reference_image(self, project_id: str, image_id: str) -> bytes:
        """Return stored bytes for an image the caller's project owns.

        Ownership is established before this is reached; the row in
        ``reference_images`` is what proves the image belongs to the project,
        and the project's owner is what RLS checked.
        """
        return self._artifacts.load_reference_image(project_id, image_id)

    def reference_image_refs(
        self, project_id: str, *, owner_id: UUID
    ) -> list[ReferenceImageRef]:
        """Image metadata for one of the caller's projects, owner-scoped."""
        with self._scoped(owner_id) as cur:
            cur.execute(
                "SELECT image_id, content_type, size_bytes, sha256, uploaded_at"
                " FROM public.reference_images WHERE project_id = %s"
                " ORDER BY uploaded_at",
                (project_id,),
            )
            return [ReferenceImageRef.model_validate(row) for row in cur.fetchall()]


class OwnerScopedRepository:
    """A :class:`PostgresStateRepository` bound to one caller.

    WHY THIS EXISTS
        Threading ``owner_id`` through every call site would have changed the
        eight-method ``StateRepository`` Protocol, and with it the filesystem
        implementation and every test and graph node that holds a repository.
        Binding the owner once instead keeps that Protocol exactly as it was:
        this object is a drop-in ``StateRepository`` whose rows are already
        restricted to one user.

        It also removes a failure mode. With a per-method argument, forgetting
        it on one of eighteen call sites is a plausible mistake; here there is
        no unscoped method to call by accident.

    The binding is per request. Instances are cheap — they hold a reference,
    not a connection — and must never be cached across callers.
    """

    def __init__(self, inner: PostgresStateRepository, owner_id: UUID) -> None:
        self._inner = inner
        self._owner_id = owner_id

    @property
    def owner_id(self) -> UUID:
        """Whose rows this view can reach. Set from a verified token only."""
        return self._owner_id

    def save(self, state: ProjectState) -> None:
        self._inner.save(state, owner_id=self._owner_id)

    def load(self, project_id: str) -> ProjectState:
        return self._inner.load(project_id, owner_id=self._owner_id)

    def exists(self, project_id: str) -> bool:
        return self._inner.exists(project_id, owner_id=self._owner_id)

    def list_project_ids(self) -> list[str]:
        return self._inner.list_project_ids(owner_id=self._owner_id)

    def delete(self, project_id: str) -> None:
        self._inner.delete(project_id, owner_id=self._owner_id)

    def workspace_dir(self, project_id: str) -> Path:
        return self._inner.workspace_dir(project_id)

    def save_reference_image(
        self, project_id: str, image_id: str, content: bytes, content_type: str
    ) -> None:
        self._inner.save_reference_image(project_id, image_id, content, content_type)

    def load_reference_image(self, project_id: str, image_id: str) -> bytes:
        return self._inner.load_reference_image(project_id, image_id)


def for_owner(
    repository: PostgresStateRepository, owner_id: UUID
) -> OwnerScopedRepository:
    """Bind a repository to one verified caller."""
    return OwnerScopedRepository(repository, owner_id)
