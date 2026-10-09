"""PostgreSQL connectivity: the async connection pool and the durable
LangGraph checkpointer.

WHY THIS FILE EXISTS
    Something has to own the database connection, and it must not be the
    composition root, a service, or a graph node. This module is that owner: it
    builds pools, hands back a configured ``AsyncPostgresSaver``, and manages
    their lifetime. Nothing above it needs to know psycopg exists.

    It replaces the interim ``InMemorySaver`` recorded in ``docs/state.md`` 5.1,
    whose known limitation was that a restart loses every pending HITL gate.

LAYER
    Persistence (``backend/db/``). Depends on config and the checkpointer
    library only. It imports nothing from api, graph, agents or services, so it
    cannot create a cycle, and the graph keeps receiving its checkpointer by
    injection exactly as it does today.

WHAT IT DOES NOT OWN
    - **ProjectState and workflow semantics.** Nothing here reads, writes or
      interprets state. The saver serializes whatever LangGraph hands it, and
      HITL interrupt/resume is unchanged: the same ``thread_id`` (the
      ``project_id``, ``docs/state.md`` 3) now resolves against Postgres
      instead of a dict.
    - **The checkpoint table definitions.** They belong to
      ``langgraph-checkpoint-postgres``, which creates and migrates its own
      (``docs/database.md`` 1.2). This file never writes that DDL.
    - **The application schema.** ``migrations/0001_application_schema.sql``
      owns it. This module runs no application DDL.
    - **Approval tokens.** They stay in process memory as a SHA-256 digest
      (``docs/database.md`` 1.4) and never reach a connection opened here.
    - **The application repository's pool.** See THE SECOND POOL below.

---------------------------------------------------------------------------
WHY THE CHECKPOINTER GETS ITS OWN SCHEMA

``docs/database.md`` 1.2 says to keep LangGraph's tables in their own schema
"so ownership is visible and a migration of ours can never touch them". That
reads like tidiness. It is not — it is a containment boundary, for two
reasons that only appear once you look at the installed library and at
Supabase's defaults together:

1. **The library has no schema option.** Verified against 3.1.2:
   ``AsyncPostgresSaver`` takes a connection, a pipeline and a serializer, and
   nothing else. Its migrations say ``CREATE TABLE IF NOT EXISTS checkpoints``
   with no qualification, so the tables land wherever ``search_path`` points.
   Here that is ``public``.

2. **Supabase grants ALL on new ``public`` tables to ``anon`` and
   ``authenticated``** via ``ALTER DEFAULT PRIVILEGES``. A checkpoint row
   contains the serialized ``ProjectState`` — including the user's raw prompt,
   which the API deliberately redacts from its own responses.

Put together: let the checkpointer default into ``public`` and every signed-in
user can read every other user's checkpoints with the publishable key. That is
precisely the leak ``docs/database.md`` 1 describes. The tables also cannot be
protected by the policies in migration 0001, because they are not ours and get
no RLS.

So the pool pins ``search_path`` to a dedicated schema. The library needs no
support for it: ``search_path`` is a connection parameter, and the unqualified
``CREATE TABLE`` then resolves there. ``anon`` and ``authenticated`` are never
granted ``USAGE`` on that schema, so the data API cannot see it at all.

---------------------------------------------------------------------------
THE SECOND POOL IS DELIBERATELY ABSENT

A Postgres ``StateRepository`` will need its own pool, and it is not built
here, for two reasons:

- There is no Postgres repository yet to use it, and a pool with no consumer
  is speculative.
- **Which role it connects as is an open decision** (``docs/database.md``
  5.2 #7). Today the DSN is ``postgres``, which carries ``BYPASSRLS``, so an
  application pool built now would silently ignore every policy in migration
  0001. The checkpointer is unaffected by that decision because its tables are
  outside RLS by design.

``create_pool`` is written to serve both. When #7 is resolved the application
pool is one call with a different DSN.

Spec: docs/database.md 1.2, 1.3.4, 5.2; docs/state.md 5.0, 5.0.2, 5.1;
      docs/runtime.md 2, 3.2.1; docs/security.md 2; CLAUDE.md 9A.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from backend.config.settings import Settings

logger = logging.getLogger(__name__)

# The schema LangGraph's four tables live in. Not ours to design; ours to
# contain (docs/database.md 1.2).
CHECKPOINT_SCHEMA = "langgraph"

# Pool sizing. Small on purpose: Supabase caps concurrent connections, and this
# backend is one process serving one workflow at a time. These are constants
# rather than settings because adding environment variables would mean editing
# settings.py, and no evidence yet says these need tuning per deployment.
POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 5

# Seconds to wait for a free connection before giving up rather than hanging a
# request forever.
POOL_TIMEOUT_SECONDS = 20.0

# How eagerly psycopg promotes a statement to a server-side prepared statement.
#
# 0 means "on first use", which is what the checkpointer wants: it issues the
# same handful of statements constantly.
#
# THIS VALUE DEPENDS ON THE POOLER MODE. It is correct for Supabase's SESSION
# pooler (port 5432), which this project uses and which was verified to support
# prepared statements. Against the TRANSACTION pooler (port 6543) it must be
# ``None``, because that mode multiplexes connections and a prepared statement
# from one transaction is invisible to the next (docs/runtime.md 3.2.1).
PREPARE_THRESHOLD = 0


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database connection is requested without a DSN.

    Carries no connection detail. The DSN embeds the password, so even a
    "could not connect to <dsn>" message would be a credential in a log
    (docs/security.md 2, 6).
    """


def configure_event_loop_policy() -> None:
    """Select an event loop psycopg can actually use, on Windows.

    Async psycopg refuses to run on ``ProactorEventLoop``:

        Psycopg cannot use the 'ProactorEventLoop' to run in async mode.

    Windows selects that loop by default, and so does uvicorn, so without this
    the checkpointer fails at the first query rather than at startup
    (docs/state.md 5.0.2).

    Exposed as a function rather than executed on import: a module that
    rearranges the process's event loop merely because it was imported is the
    kind of side effect that makes an import order load-bearing. The
    composition root calls this, before the server starts.
    """
    if sys.platform != "win32":
        return
    if isinstance(
        asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
    ):
        return
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    logger.info("event loop policy set to WindowsSelectorEventLoopPolicy for psycopg")


def _require_dsn(settings: Settings) -> str:
    """The DSN, or a failure that names the variable and nothing else."""
    if settings.database_url is None:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not set, so no PostgreSQL connection can be opened."
        )
    return settings.database_url.get_secret_value()


def create_pool(
    dsn: str,
    *,
    schema: str | None = None,
    min_size: int = POOL_MIN_SIZE,
    max_size: int = POOL_MAX_SIZE,
) -> AsyncConnectionPool:
    """Build an unopened async pool.

    Returned closed so the caller owns the lifetime: ``psycopg_pool`` warns
    against opening in the constructor, and a pool that connects as a side
    effect of construction cannot be built during module import or in a test.

    Args:
        dsn: libpq URI. Treated as a credential — never logged.
        schema: pinned as the connection's entire ``search_path`` when given.
            One entry, not a prefix: a fallback to ``public`` would let an
            unqualified ``CREATE TABLE`` silently land in the application
            schema, which is the outcome this exists to prevent.
        min_size: connections opened eagerly.
        max_size: ceiling on concurrent connections.

    The three ``kwargs`` are the checkpointer's requirements, not preferences:
    ``row_factory=dict_row`` because it reads columns by name, ``autocommit``
    because ``setup()`` runs DDL that cannot sit inside a transaction block,
    and ``prepare_threshold`` as described above.
    """
    connection_kwargs: dict[str, object] = {
        "autocommit": True,
        "prepare_threshold": PREPARE_THRESHOLD,
        "row_factory": dict_row,
    }
    if schema is not None:
        connection_kwargs["options"] = f"-c search_path={schema}"

    return AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=POOL_TIMEOUT_SECONDS,
        kwargs=connection_kwargs,
        # A pooled connection to a remote pooler can be dropped while idle.
        # Without this check a request is handed a dead connection and fails
        # for a reason that has nothing to do with the request.
        check=AsyncConnectionPool.check_connection,
        open=False,
    )


async def ensure_checkpoint_schema(pool: AsyncConnectionPool) -> None:
    """Create the checkpointer's schema if it is missing.

    This is the one piece of DDL this module issues, and it is deliberately the
    smallest possible: a namespace to put the library's tables in. It creates
    no table and defines no column — doing so would be hand-writing a schema
    the library owns (``docs/database.md`` 1.2).

    ``search_path`` already points at this schema, so it must exist before
    ``AsyncPostgresSaver.setup()`` runs; otherwise the unqualified
    ``CREATE TABLE`` has nowhere to resolve to.
    """
    async with pool.connection() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{CHECKPOINT_SCHEMA}"')
    logger.info("checkpoint schema ready: %s", CHECKPOINT_SCHEMA)


async def create_checkpointer(
    pool: AsyncConnectionPool, *, setup: bool = True
) -> AsyncPostgresSaver:
    """A durable checkpointer bound to ``pool``.

    Args:
        pool: an OPEN pool whose ``search_path`` is the checkpoint schema.
        setup: run the library's own migrations. Idempotent — it keeps a
            ``checkpoint_migrations`` table and applies only what is missing —
            so it is safe on every start. Pass ``False`` where the caller has
            no DDL rights and the schema is known to be current.

    Returns a saver the graph can be compiled with. ``build_workflow`` takes
    the checkpointer as a parameter already, so nothing about the graph, its
    nodes, or interrupt/resume changes: only the store behind ``thread_id``.
    """
    checkpointer = AsyncPostgresSaver(pool)
    if setup:
        await checkpointer.setup()
        logger.info("checkpointer migrations applied in schema %s", CHECKPOINT_SCHEMA)
    return checkpointer


@asynccontextmanager
async def checkpointer_lifespan(
    settings: Settings, *, setup: bool = True
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open the pool, yield a ready checkpointer, close the pool afterwards.

    The shape the composition root wants: FastAPI's ``lifespan`` is itself an
    async context manager, so this nests into it and the pool's lifetime
    becomes exactly the application's. A pool left open after shutdown holds
    server-side connections that Supabase counts against the project's limit.

    Raises:
        DatabaseNotConfiguredError: no ``DATABASE_URL``. The caller decides
            what that means — today it means falling back to the interim
            ``InMemorySaver`` rather than refusing to start
            (``docs/state.md`` 5.1).
    """
    pool = create_pool(_require_dsn(settings), schema=CHECKPOINT_SCHEMA)
    await pool.open(wait=True, timeout=POOL_TIMEOUT_SECONDS)
    logger.info(
        "postgres pool open (min=%d max=%d schema=%s)",
        POOL_MIN_SIZE,
        POOL_MAX_SIZE,
        CHECKPOINT_SCHEMA,
    )
    try:
        await ensure_checkpoint_schema(pool)
        yield await create_checkpointer(pool, setup=setup)
    finally:
        await pool.close()
        logger.info("postgres pool closed")


async def check_connection(settings: Settings) -> str:
    """Open one connection and report the server version.

    A startup probe that answers "is the database actually reachable" without
    the caller writing psycopg code. Returns the version string so a log line
    can show what it connected to; raises whatever psycopg raises, because a
    failure here should be loud.
    """
    async with await AsyncConnection.connect(
        _require_dsn(settings), connect_timeout=10
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT version()")
            row = await cur.fetchone()
    return str(row[0]) if row else "unknown"


def create_app_pool(
    dsn: str, *, min_size: int = POOL_MIN_SIZE, max_size: int = POOL_MAX_SIZE
) -> ConnectionPool:
    """A SYNCHRONOUS pool for owner-scoped application queries.

    WHY SYNC, WHEN THE CHECKPOINTER POOL IS ASYNC
        ``StateRepository`` is a synchronous Protocol and the graph calls it
        from inside nodes. An async pool would push ``await`` through that
        seam. Sync psycopg also has no event-loop requirement, so this pool is
        untouched by the Windows loop conflict that constrains the async side.

    WHY autocommit IS OFF HERE AND ON THERE
        This pool carries the caller's identity as a transaction-local
        setting. ``SET LOCAL`` needs a transaction to be local to, so
        autocommit would either error or — worse — leave the claim set on a
        pooled connection for whoever borrows it next. The checkpointer needs
        the opposite, because its ``setup()`` runs DDL that cannot sit inside
        a transaction block.

        That difference is the reason these are two pools rather than one with
        a flag.

    The DSN here is the RESTRICTED role (``detroit_app``, NOBYPASSRLS), never
    the admin one. Passing the admin DSN would silently disable every policy.
    """
    return ConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=POOL_TIMEOUT_SECONDS,
        kwargs={"autocommit": False, "prepare_threshold": PREPARE_THRESHOLD},
        check=ConnectionPool.check_connection,
        open=False,
    )
