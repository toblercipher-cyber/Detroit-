"""Durable project state: the persistence interface, and its filesystem
implementation.

WHY THIS FILE EXISTS
    The database is decided (Supabase/PostgreSQL) but its schema is deliberately
    undecided, so no database code may be written yet. What CAN be built now is the
    narrow interface the rest of the system depends on, plus an interim filesystem
    implementation behind it.

    ``StateRepository`` is that interface. The graph and the API depend on it, never
    on a concrete store. When the schema is defined, a Postgres implementation
    satisfies the same Protocol and is swapped in without reworking the graph, the
    agents or the API contract (CLAUDE.md 9A, docs/state.md 5.0).

LAYER
    Memory / persistence (``backend/memory/``). Depends on config and schemas only.
    It imports nothing from api, graph, agents or services.

WHAT THIS FILE IS NOT
    - It is not the checkpointer. LangGraph checkpoints are what make an interrupted
      run resumable; these artifacts are the human-readable audit copy. **The
      checkpointer is authoritative for resumption; artifacts are authoritative for
      human review.** On disagreement the checkpointer wins and artifacts are
      rewritten from it (docs/state.md 5.1). The checkpointer itself is an open
      decision and is not implemented here.
    - It is not a schema for the future database. Nothing here assumes a table shape.

DURABILITY
    Every write is atomic: content goes to a temporary file in the same directory,
    is flushed and fsynced, then moved into place with ``os.replace``. A crash
    mid-write therefore leaves the previous artifact intact rather than a truncated
    file (docs/state.md 5.1).

Spec: docs/state.md 5.0, 5.1, 7; docs/architecture.md 3.8, 5.1; docs/security.md 3.4;
      CLAUDE.md 9, 9A.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from backend.schemas.state import (
    ComponentTree,
    DesignSpec,
    EvaluationReport,
    ProjectState,
    SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

# Artifact filenames, fixed by docs/state.md 5.1.
STATE_FILE = "project_state.json"
DESIGN_SPEC_FILE = "design_spec.json"
COMPONENT_TREE_FILE = "component_tree.json"
ITERATION_HISTORY_FILE = "iteration_history.json"

# Subdirectories owned by other subsystems; this module only resolves their paths.
REFERENCE_DIR = "reference"
WORKSPACE_DIR = "workspace"

# A project_id arrives from a request path, so it is untrusted until proven to match
# the server-generated format. Anchored and character-restricted, it cannot contain a
# separator, a drive letter or "..", which is what makes path traversal impossible
# rather than merely unlikely (docs/security.md 3.4, docs/state.md 5.3).
_PROJECT_ID_RE = re.compile(r"^proj_[0-9a-f]{32}$")

# Reference image ids are server-generated and never derived from a client
# filename (docs/security.md 4). Validating the shape here means a stored path can
# never be influenced by upload content, whatever the upload route does.
_IMAGE_ID_RE = re.compile(r"^img_[0-9a-f]{32}$")

# Extension per allowlisted type (docs/security.md 4). Derived from the
# magic-byte-verified type, NEVER from the client filename: the upload names
# neither the file nor its suffix.
#
# A closed table, not mimetypes.guess_extension(): that consults the machine's
# registry, so the stored filename would depend on the host, and it can return
# ".jpe" for image/jpeg. Three entries that never surprise are worth more here.
_EXTENSION_BY_TYPE: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class StateRepositoryError(RuntimeError):
    """A persistence operation failed. Messages stay free of absolute paths."""


class ProjectNotFoundError(StateRepositoryError):
    """No stored project matches the requested id."""


class InvalidProjectIdError(StateRepositoryError):
    """The project id is not a server-generated identifier.

    Raised before any path is built. Callers map this to 404 and log it, so a
    traversal attempt is never distinguishable from an unknown project
    (docs/security.md 3.4).
    """


class SchemaVersionMismatchError(StateRepositoryError):
    """A stored artifact was written by a different state schema.

    Refusing to load is deliberate: silently reinterpreting old fields under new
    meanings would corrupt approved decisions.
    """


class ReferenceImageNotFoundError(StateRepositoryError):
    """No stored reference image with this id under this project.

    Distinct from :class:`ProjectNotFoundError`: the project may be perfectly
    valid while the image is missing, and the Reference Analysis stage must be able
    to tell "nothing was uploaded" from "the project does not exist".
    """


class StateRepository(Protocol):
    """The narrow persistence interface the rest of the system depends on.

    Structural, not inherited: the future Postgres implementation satisfies this by
    shape alone, with no base class to couple the layers together.
    """

    def save(self, state: ProjectState) -> None:
        """Persist the full state and its derived artifacts."""

    def load(self, project_id: str) -> ProjectState:
        """Return stored state, or raise :class:`ProjectNotFoundError`."""

    def exists(self, project_id: str) -> bool:
        """True when a stored project with this id is present."""

    def list_project_ids(self) -> list[str]:
        """Every stored project id."""

    def delete(self, project_id: str) -> None:
        """Remove a project and everything under it."""

    def workspace_dir(self, project_id: str) -> Path:
        """Where this project's generated website is built.

        On the interface because the Build stage needs a directory to scaffold and
        compile in, and it must not know how projects are laid out on disk. The
        Postgres implementation returns a local working directory for the same
        reason: a build needs a filesystem whatever the state store is.
        """

    def save_reference_image(
        self, project_id: str, image_id: str, content: bytes, content_type: str
    ) -> None:
        """Store raw upload bytes for later vision analysis."""

    def load_reference_image(self, project_id: str, image_id: str) -> bytes:
        """Return stored bytes, or raise :class:`ReferenceImageNotFoundError`."""


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write ``content`` to ``path`` atomically.

    The byte twin of :func:`_atomic_write_text`, kept separate rather than folding
    both into one function with a mode flag: image bytes must never pass through a
    text encoder, and a shared helper is one edit away from doing exactly that.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise StateRepositoryError(
            f"Could not write artifact '{path.name}' (reason: {exc.strerror})."
        ) from None


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    The temporary file is created in the destination directory so that
    ``os.replace`` is a same-filesystem move, which is what makes it atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise StateRepositoryError(
            f"Could not write artifact '{path.name}' (reason: {exc.strerror})."
        ) from None


class JsonArtifactRepository:
    """Filesystem implementation of :class:`StateRepository`.

    Interim only. It exists so the workflow can be built and audited before the
    database schema is defined; it migrates to Supabase/PostgreSQL behind the same
    interface (docs/state.md 5.0).

    Layout, per project (docs/state.md 5.1)::

        <data_dir>/projects/<project_id>/
        ├── project_state.json      # full ProjectState, minus node_scratch
        ├── design_spec.json        # approved design system
        ├── component_tree.json     # approved component architecture
        ├── iteration_history.json  # append-only evaluation snapshots
        ├── reference/              # uploaded reference images
        └── workspace/              # the generated website
    """

    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir.expanduser().resolve()
        self._projects_root = self._root / "projects"

    # --- Path resolution -------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        """Resolve a project directory, rejecting anything outside the root.

        Two independent checks: the id must match the server-generated format, and
        the resolved path must still sit inside the projects root. The second check
        is redundant given the first — kept because a containment guarantee should
        not rest on a single regex.
        """
        if not _PROJECT_ID_RE.match(project_id):
            logger.warning("rejected malformed project id")
            raise InvalidProjectIdError("Unknown project.")

        candidate = (self._projects_root / project_id).resolve()
        if not candidate.is_relative_to(self._projects_root):
            logger.warning("rejected project path outside the data root")
            raise InvalidProjectIdError("Unknown project.")
        return candidate

    def reference_dir(self, project_id: str) -> Path:
        """Where uploaded reference images for this project are stored."""
        return self._project_dir(project_id) / REFERENCE_DIR

    def workspace_dir(self, project_id: str) -> Path:
        """Where the generated website for this project is built."""
        return self._project_dir(project_id) / WORKSPACE_DIR

    def _reference_dir_checked(self, project_id: str, image_id: str) -> Path:
        """The project's reference directory, with the image id validated.

        The id is checked before any path is built, so a malformed value never
        reaches the filesystem. Containment is then asserted again on the resolved
        path: an upload identifier is untrusted input and gets the same two-stage
        treatment as a project id (docs/security.md 3.4).
        """
        if not _IMAGE_ID_RE.match(image_id):
            logger.warning("rejected malformed reference image id")
            raise ReferenceImageNotFoundError("Unknown reference image.")
        return self.reference_dir(project_id)

    def _reference_path(
        self, project_id: str, image_id: str, content_type: str
    ) -> Path:
        """Resolve the stored path for one image: ``{image_id}.{ext}``.

        The extension comes from the magic-byte-verified type, never from the
        client filename (docs/security.md 4). An unknown type cannot produce a
        path at all, so nothing can be written under an arbitrary suffix.
        """
        base = self._reference_dir_checked(project_id, image_id)
        extension = _EXTENSION_BY_TYPE.get(content_type)
        if extension is None:
            logger.warning("rejected reference image with unsupported type")
            raise ReferenceImageNotFoundError("Unsupported reference image type.")

        candidate = (base / f"{image_id}{extension}").resolve()
        if not candidate.is_relative_to(base.resolve()):
            logger.warning("rejected reference path outside the project directory")
            raise ReferenceImageNotFoundError("Unknown reference image.")
        return candidate

    def _find_reference_path(self, project_id: str, image_id: str) -> Path:
        """Locate a stored image by id, whichever allowlisted extension it has.

        A reader knows the id but not necessarily the type, so the extension is
        discovered rather than required. The candidates are built from the closed
        extension table — not from a directory glob — so a stray file that happened
        to share the id prefix could never be selected.
        """
        base = self._reference_dir_checked(project_id, image_id)
        for extension in _EXTENSION_BY_TYPE.values():
            candidate = base / f"{image_id}{extension}"
            if candidate.is_file():
                return candidate
        raise ReferenceImageNotFoundError("Unknown reference image.")

    # --- Reference images ------------------------------------------------

    def save_reference_image(
        self, project_id: str, image_id: str, content: bytes, content_type: str
    ) -> None:
        """Store raw upload bytes beside the project's state.

        Bytes are stored verbatim and never decoded, re-encoded or parsed here.
        Size and type are enforced at the upload boundary, which is the only place
        that has seen the request; this layer's job is containment and durability
        (docs/security.md 4).

        Args:
            content_type: the **verified** media type, used only to choose the
                stored extension. Passing a declared header value here would put
                the client back in control of the filename.
        """
        path = self._reference_path(project_id, image_id, content_type)
        _atomic_write_bytes(path, content)
        logger.info(
            "saved reference image project_id=%s image_id=%s type=%s bytes=%d",
            project_id,
            image_id,
            content_type,
            len(content),
        )

    def load_reference_image(self, project_id: str, image_id: str) -> bytes:
        """Return the stored bytes for one reference image.

        Raises :class:`ReferenceImageNotFoundError` when it is absent, so a caller
        can distinguish "no reference to analyse" from a real read failure instead
        of treating every problem as an empty image.
        """
        path = self._find_reference_path(project_id, image_id)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise ReferenceImageNotFoundError("Unknown reference image.") from None
        except OSError as exc:
            raise StateRepositoryError(
                f"Could not read reference image (reason: {exc.strerror})."
            ) from None

    # --- StateRepository -------------------------------------------------

    def save(self, state: ProjectState) -> None:
        """Persist state plus the derived artifacts, each written atomically.

        ``node_scratch`` is excluded by the model itself, so transient agent working
        data cannot reach disk (docs/state.md 2).
        """
        project_dir = self._project_dir(state.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        _atomic_write_text(
            project_dir / STATE_FILE, state.model_dump_json(indent=2)
        )

        # Derived artifacts exist so a human can read one decision without parsing the
        # whole state. They are copies for review, never a second source of truth:
        # project_state.json remains authoritative (CLAUDE.md 9).
        self._write_optional(project_dir / DESIGN_SPEC_FILE, state.design_spec)
        self._write_optional(project_dir / COMPONENT_TREE_FILE, state.component_tree)

        if state.evaluation_results is not None:
            self._append_iteration(project_dir, state.evaluation_results)

        logger.info(
            "saved project state project_id=%s stage=%s iteration=%d",
            state.project_id,
            state.current_stage.value,
            state.iteration_count,
        )

    def load(self, project_id: str) -> ProjectState:
        """Read stored state, refusing artifacts from a different schema version."""
        path = self._project_dir(project_id) / STATE_FILE
        if not path.is_file():
            raise ProjectNotFoundError("Unknown project.")

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateRepositoryError(
                f"Could not read stored state (reason: {exc.strerror})."
            ) from None

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise StateRepositoryError("Stored state is not valid JSON.") from None

        stored_version = payload.get("schema_version")
        if stored_version != SCHEMA_VERSION:
            raise SchemaVersionMismatchError(
                f"Stored state uses schema version {stored_version!r}, "
                f"this build expects {SCHEMA_VERSION}."
            )

        return ProjectState.model_validate(payload)

    def exists(self, project_id: str) -> bool:
        try:
            return (self._project_dir(project_id) / STATE_FILE).is_file()
        except InvalidProjectIdError:
            return False

    def list_project_ids(self) -> list[str]:
        """Stored project ids, newest first by state-file modification time."""
        if not self._projects_root.is_dir():
            return []
        entries = [
            entry
            for entry in self._projects_root.iterdir()
            if entry.is_dir()
            and _PROJECT_ID_RE.match(entry.name)
            and (entry / STATE_FILE).is_file()
        ]
        entries.sort(key=lambda e: (e / STATE_FILE).stat().st_mtime, reverse=True)
        return [entry.name for entry in entries]

    def delete(self, project_id: str) -> None:
        """Remove the project directory and everything under it.

        Irreversible, so it is never called as a side effect of generation; only an
        explicit user request reaches here (docs/api.md 2.2).
        """
        import shutil

        project_dir = self._project_dir(project_id)
        if not project_dir.is_dir():
            raise ProjectNotFoundError("Unknown project.")
        try:
            shutil.rmtree(project_dir)
        except OSError as exc:
            raise StateRepositoryError(
                f"Could not delete project (reason: {exc.strerror})."
            ) from None
        logger.info("deleted project project_id=%s", project_id)

    # --- Internals -------------------------------------------------------

    def _write_optional(
        self, path: Path, model: DesignSpec | ComponentTree | None
    ) -> None:
        """Write a derived artifact, or remove a stale one when the field is unset."""
        if model is None:
            path.unlink(missing_ok=True)
            return
        _atomic_write_text(path, model.model_dump_json(indent=2))

    def _append_iteration(self, project_dir: Path, report: EvaluationReport) -> None:
        """Append an evaluation snapshot to the append-only iteration history.

        Rewritten whole rather than appended in place, because a partial append is
        not recoverable whereas an atomic replace is. Existing entries are preserved;
        a snapshot for an iteration already present is replaced rather than
        duplicated.
        """
        path = project_dir / ITERATION_HISTORY_FILE
        history: list[dict] = []
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    history = loaded
            except (OSError, json.JSONDecodeError):
                # A corrupt history must not block saving authoritative state.
                logger.warning("iteration history unreadable; starting a new one")
                history = []

        entry = json.loads(report.model_dump_json())
        history = [e for e in history if e.get("iteration") != report.iteration]
        history.append(entry)
        history.sort(key=lambda e: e.get("iteration", 0))
        _atomic_write_text(path, json.dumps(history, indent=2))
