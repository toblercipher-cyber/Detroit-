"""Typed environment configuration for the builder backend.

WHY THIS FILE EXISTS
    This is the single place the backend reads environment variables. Every other
    module receives configuration through ``get_settings()`` and must never touch
    ``os.environ`` directly. Centralising it here is what makes the secret-handling
    rules enforceable in one place instead of scattered across the codebase.

LAYER
    Runtime / configuration (``backend/config/``). It is the lowest layer: it imports
    nothing from the API, graph, agents or services, so it can never create a circular
    import.

WHAT BELONGS HERE
    The environment variables defined in ``docs/runtime.md`` 3.2, plus the database
    group that ``docs/database.md`` unblocked, with their types, defaults and
    validation.

WHAT DOES NOT BELONG HERE
    - The per-role Claude temperature / max-token policy table (``docs/runtime.md``
      4.1). That is a static policy table, not environment configuration, and belongs
      in its own module under ``backend/config/``.
    - The connection pool, the engine, or the LangGraph checkpointer itself. This
      module declares and validates the DSN; opening a connection with it belongs to
      the persistence layer under ``backend/db/``. Configuration that dialled out to
      a network service on import would make this module untestable and would give
      it two jobs.
    - Any SQL. Application tables are ``docs/database.md`` 3; LangGraph's checkpoint
      tables are LangGraph's own and are never written by hand (``docs/database.md``
      1.2).
    - Any Impeccable enable/disable flag. Impeccable is a mandatory gate, not a
      feature toggle; a switch that could silently disable it must never exist.

WHY THE DATABASE GROUP EXISTS NOW
    ``CLAUDE.md`` 9A withheld every database setting "until the schema is defined
    and approved". ``docs/database.md`` is that definition and records it as
    approved, and the user has provisioned the Supabase project. So the gate is
    satisfied for *configuration*; it is not satisfied for tables, migrations or
    auth, none of which appear here.

    These five variables are also already present in ``.env``. Because
    ``extra="forbid"`` below is deliberate, an undeclared key is a hard startup
    failure rather than a silent default — so declaring them is what lets the
    backend boot at all.

Spec: docs/runtime.md 3.2, 3.4, 6; docs/database.md 1, 5, 6; docs/security.md 2, 9;
      docs/state.md 5.0, 5.1; skills.md 25, 26.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from typing import Annotated

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import NoDecode
from pydantic_settings import BaseSettings, SettingsConfigDict

# The literal placeholder shipped in .env.example. Rejected explicitly so that
# copying the example to .env without editing fails at startup with a clear message,
# rather than surfacing later as a confusing provider authentication error.
PLACEHOLDER_API_KEY = "replace-with-your-anthropic-api-key"

# Supabase hands out a connection string with the password left as a literal
# bracketed placeholder. Pasting it unedited is the single most common way to get a
# DSN that parses but cannot connect, so it is rejected by name at startup instead
# of failing later as an opaque authentication error. Compared case-insensitively.
PLACEHOLDER_DB_PASSWORDS = ("[your-password]", "[yourpassword]", "[password]")

# Schemes the persistence layer can actually open. psycopg v3 and
# langgraph-checkpoint-postgres both take a plain libpq URI; a SQLAlchemy-style
# "postgresql+asyncpg://" would be accepted by neither, so it is refused here where
# the message can say why rather than surfacing as a driver error.
VALID_DB_SCHEMES = ("postgresql://", "postgres://")

# Hosts that keep the API unreachable from outside this machine. Required while there
# is no authentication (docs/security.md 9).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ConfigurationError(RuntimeError):
    """Raised when environment configuration is missing or invalid.

    Carries variable *names* only, never values, so that a startup failure cannot
    leak a secret into a log or a terminal (docs/runtime.md 6, docs/security.md 2).
    """


class AppEnv(str, Enum):
    """Deployment environment. Invalid values fail validation, never fall back."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


class LogLevel(str, Enum):
    """Logging verbosity. Invalid values fail validation, never fall back."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def _split_csv(value: Any) -> Any:
    """Parse a comma-separated environment value into a list.

    pydantic-settings parses ``list`` fields as JSON by default, which would reject
    ordinary values such as ``http://localhost:5173``. Non-string input is passed
    through untouched so defaults and direct construction still work.
    """
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    """The backend's validated configuration.

    Construct via ``get_settings()`` rather than directly, so the object is built and
    validated exactly once per process (docs/runtime.md 3.4).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # An unrecognised key in .env is an error, not something to ignore. A typo
        # such as MAX_AUTO_ITERATION would otherwise be silently dropped and the
        # documented default used instead — exactly the silent fallback that
        # docs/runtime.md 6 forbids.
        extra="forbid",
    )

    # --- Anthropic Claude: the only provider in v1 (CLAUDE.md 7, skills.md 10) ---

    anthropic_api_key: SecretStr = Field(
        ...,
        description="Provider credential. SECRET: SecretStr redacts it in repr/str.",
    )
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        min_length=1,
        description="Default model id for every agent role.",
    )
    anthropic_model_heavy: str = Field(
        default="claude-opus-5",
        min_length=1,
        description="Optional model for heavy-reasoning roles; opt-in via config.",
    )

    # --- Application ---

    app_env: AppEnv = AppEnv.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO

    # --- API server (docs/api.md 1.1, docs/security.md 9) ---

    api_host: str = Field(
        default="127.0.0.1",
        min_length=1,
        description="Must stay loopback while there is no authentication.",
    )
    api_port: int = Field(default=8000, ge=1, le=65535)
    # NoDecode is load-bearing, not decoration. pydantic-settings treats a
    # list field as "complex" and JSON-decodes it AT THE SOURCE, before any
    # validator runs — so `ALLOWED_ORIGINS=http://localhost:5173` in .env died
    # with "Expecting value: line 1 column 1" and took the whole backend down
    # with it. NoDecode hands the raw string to `_split_csv` below, which is
    # what was always meant to parse it. .env.example documents exactly that
    # form, so without this the documented configuration cannot start.
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:5173"],
        min_length=1,
        description="Explicit CORS allowlist. Comma-separated in the environment.",
    )

    # --- Builder runtime data (docs/state.md 5.1, docs/architecture.md 5.1) ---

    builder_data_dir: Path = Field(
        default=Path("./var"),
        description="Root for checkpoints, state artifacts, uploads, workspaces.",
    )

    # --- Database: Supabase-hosted PostgreSQL (docs/database.md) ---
    #
    # OPTIONAL ON PURPOSE. The durable store is prepared, not switched on: with no
    # DSN the backend keeps running on the filesystem repository and the
    # InMemorySaver that docs/state.md 5.1 records as interim. Making this required
    # would turn a half-finished migration into a backend that refuses to boot, and
    # the persistence layer that consumes it does not exist yet.

    database_url: SecretStr | None = Field(
        default=None,
        description=(
            "libpq URI for the Supabase PostgreSQL instance, used by both the "
            "durable StateRepository and the LangGraph checkpointer. SECRET: the "
            "password is embedded in the DSN, so this is a credential, not a "
            "location."
        ),
    )

    database_url_app: SecretStr | None = Field(
        default=None,
        description=(
            "libpq URI for the RESTRICTED application role (detroit_app, "
            "NOBYPASSRLS). Used for every query against the six public tables, "
            "with the caller's verified claims set per transaction. SECRET."
        ),
    )

    # The remaining four are present in .env because the Supabase project ships
    # them. v1 uses NONE of them, and that is a decision rather than an omission:
    #
    #   SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY  address the PostgREST, Auth and
    #       Storage APIs. docs/database.md 1.3 defers auth entirely and 4 keeps
    #       reference-image bytes on the filesystem, so no Supabase client exists.
    #   SUPABASE_SERVICE_ROLE_KEY                bypasses row-level security
    #       completely. Nothing reads it today, and nothing should start to without
    #       an explicit decision — docs/security.md 7 is least privilege, and this
    #       key is the opposite of that.
    #   SUPABASE_DB_PASSWORD                     is already inside DATABASE_URL.
    #       Kept so .env stays the shape Supabase generated; never read separately,
    #       because two copies of one credential is two chances to leak it.
    #
    # They are declared so startup succeeds under extra="forbid", and typed as
    # SecretStr so an accidental log or traceback cannot print them.

    supabase_url: str | None = Field(
        default=None,
        description="Supabase project API URL. Not a secret, and unused in v1.",
    )
    supabase_publishable_key: SecretStr | None = Field(
        default=None, description="Anon/publishable key. Unused in v1."
    )
    supabase_service_role_key: SecretStr | None = Field(
        default=None,
        description="SECRET: full-access key that bypasses RLS. Unused in v1.",
    )
    supabase_db_password: SecretStr | None = Field(
        default=None,
        description="SECRET: already embedded in DATABASE_URL. Unused separately.",
    )

    # --- GitHub integration (docs/mcp.md 2.1, docs/security.md 2) ---
    #
    # Two independent routes to GitHub, and the gateway picks between them by
    # DISCOVERED capability rather than by preference:
    #
    #   MCP_GITHUB_SERVER_URL -> an MCP server that advertises repository tools
    #   GITHUB_TOKEN          -> the REST adapter, used directly
    #
    # Either alone is enough to be "configured". Neither present means the
    # integration reports unavailable -- it never pretends to be connected
    # (docs/mcp.md 1).

    mcp_github_server_url: str | None = Field(
        default=None,
        description=(
            "Endpoint of an MCP server exposing GitHub tools. Not a secret: it "
            "is a location. Its credential travels in GITHUB_TOKEN."
        ),
    )

    github_token: SecretStr | None = Field(
        default=None,
        description=(
            "SECRET: GitHub credential (PAT or installation token). Used as the "
            "bearer for the MCP server AND by the REST adapter. Never logged, "
            "never returned by an API route, never placed in agent context "
            "(docs/security.md 2, docs/mcp.md 6)."
        ),
    )

    github_api_base_url: str = Field(
        default="https://api.github.com",
        description=(
            "Overridable for GitHub Enterprise. Not a secret. Validated as "
            "https-only below, because a plaintext base URL would put the "
            "token on the wire in clear."
        ),
    )

    # Test-only knobs for backend/tests/suites/github_live_check.py. Declared
    # here ONLY because `extra="forbid"` above makes any undeclared key in
    # .env a hard startup failure — and .env.example documents these, so a
    # developer who copies it would otherwise get a backend that refuses to
    # boot with a confusing error. Nothing in the application reads them.
    github_live_tests: str | None = Field(
        default=None, description="Test-only. Set to 1 to enable live GitHub tests."
    )
    github_test_repo: str | None = Field(
        default=None, description="Test-only. owner/name used by the live suite."
    )

    # --- Preview capability tokens (docs/api.md 2.5, docs/security.md 9) ---

    preview_url_secret: SecretStr | None = Field(
        default=None,
        description=(
            "SECRET: HS256 signing key for short-lived preview URLs. A browser "
            "loading a preview in a tab or an iframe cannot send an "
            "Authorization header, so the capability travels in the URL and "
            "must be unforgeable. REQUIRED whenever DATABASE_URL_APP is set: "
            "in multi-user mode a guessable or absent key means cross-user "
            "reads of generated sites. In single-user development main.py "
            "generates a process-local key instead and warns."
        ),
    )

    # --- Reference image uploads (docs/api.md 2.4, docs/security.md 4) ---

    max_upload_bytes: int = Field(default=10_485_760, gt=0)
    # Same source-level decode problem as allowed_origins above.
    allowed_upload_types: Annotated[list[str], NoDecode] = Field(
        default=["image/png", "image/jpeg", "image/webp"],
        min_length=1,
        description="Declared type must also match magic bytes at upload time.",
    )

    # --- Workflow (docs/workflow.md 4, docs/state.md 5.3) ---

    session_ttl_minutes: int = Field(default=1440, ge=1)
    max_auto_iterations: int = Field(
        default=3,
        ge=1,
        description="Automatic correction limit; the workflow stops at this value.",
    )

    # --- Generated-project build (docs/architecture.md 5.3) ---

    preview_build_timeout_seconds: int = Field(default=180, ge=1)

    # --- Optional features ---

    enable_prompt_caching: bool = False
    mcp_github_enabled: bool = False

    # --- Validators ---

    _split_origins = field_validator("allowed_origins", mode="before")(_split_csv)
    _split_upload_types = field_validator("allowed_upload_types", mode="before")(
        _split_csv
    )

    @field_validator("github_api_base_url", "mcp_github_server_url")
    @classmethod
    def _require_https(cls, value: str | None) -> str | None:
        """Refuse a plaintext endpoint for anything the token is sent to.

        The GitHub credential travels as a bearer header to both of these. Over
        http it would cross the network in clear, so a misconfiguration here is
        credential disclosure, not a connectivity problem. localhost is allowed
        because a developer MCP server commonly runs there and never leaves the
        machine.
        """
        if value is None or not value.strip():
            return value
        url = value.strip().rstrip("/")
        if url.startswith("https://"):
            return url
        if url.startswith("http://") and (
            "//localhost" in url or "//127.0.0.1" in url or "//[::1]" in url
        ):
            return url
        raise ValueError(
            "must use https (http is allowed only for localhost): the GitHub "
            "token is sent to this URL as a bearer credential"
        )

    @field_validator("anthropic_api_key")
    @classmethod
    def _reject_placeholder_key(cls, value: SecretStr) -> SecretStr:
        """Reject an empty or unedited placeholder credential.

        The comparison result is a boolean; the value itself is never echoed.
        """
        secret = value.get_secret_value().strip()
        if not secret or secret == PLACEHOLDER_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is unset or still the .env.example placeholder"
            )
        return value

    @field_validator("database_url", "database_url_app")
    @classmethod
    def _check_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject a DSN that cannot work, while never echoing it.

        Three failures are caught here because each one otherwise surfaces much
        later as something that does not name its cause:

        1. A wrong scheme. ``postgresql+asyncpg://`` is SQLAlchemy syntax; psycopg
           and the LangGraph checkpointer both take a plain libpq URI and would
           fail on it inside a driver.
        2. The unedited Supabase password placeholder, which produces a DSN that
           parses perfectly and then cannot authenticate.
        3. A quoted value. ``DATABASE_URL="postgresql://..."`` leaves the quotes in
           the string when the shell is not involved, and the result is a hostname
           with a quote in it.

        Every message names the variable and the problem. None of them includes any
        part of the value, because the password is inside it (docs/security.md 2, 6).
        """
        if value is None:
            return None

        dsn = value.get_secret_value().strip()
        if not dsn:
            # An empty assignment in .env means "not configured", not "misconfigured".
            return None

        if dsn[0] in "\"'" or dsn[-1] in "\"'":
            raise ValueError(
                "DATABASE_URL is wrapped in quotes; remove them so the value is the "
                "bare URI"
            )

        if not dsn.lower().startswith(VALID_DB_SCHEMES):
            raise ValueError(
                "DATABASE_URL must begin with 'postgresql://' or 'postgres://' "
                "(a '+driver' suffix is SQLAlchemy syntax and is not supported)"
            )

        lowered = dsn.lower()
        if any(ph in lowered for ph in PLACEHOLDER_DB_PASSWORDS):
            raise ValueError(
                "DATABASE_URL still contains the Supabase password placeholder; "
                "substitute the real password and percent-encode any reserved "
                "characters in it"
            )

        return value

    # --- Derived state and startup checks ---

    @property
    def is_loopback_bound(self) -> bool:
        """True when the API is unreachable from outside this machine."""
        return self.api_host in LOOPBACK_HOSTS

    @property
    def is_app_role_configured(self) -> bool:
        """True when the restricted application role has a DSN.

        Separate from :attr:`is_database_configured` because the two pools fail
        independently: the checkpointer can be durable while owner-scoped
        storage is not yet wired, which is exactly the state during migration.
        """
        return self.database_url_app is not None

    @property
    def is_github_configured(self) -> bool:
        """True when SOME route to GitHub exists.

        Either an MCP server or a direct token is enough. False means the
        integration reports unavailable rather than offering buttons that
        cannot work (docs/mcp.md 1).
        """
        return bool(self.mcp_github_server_url) or self.github_token is not None

    @property
    def is_github_mcp_configured(self) -> bool:
        """True when an MCP server URL is set. Discovery decides the rest."""
        return bool(self.mcp_github_server_url)

    @property
    def is_github_rest_configured(self) -> bool:
        """True when a token is present for the direct REST adapter."""
        return self.github_token is not None

    @property
    def is_database_configured(self) -> bool:
        """True when a DSN is present for the durable PostgreSQL store.

        The one predicate the composition root should branch on when choosing
        between the durable store and the interim filesystem + InMemorySaver pair,
        so that choice is made in exactly one place rather than re-derived from a
        ``None`` check at each call site (docs/state.md 5.0).
        """
        return self.database_url is not None

    def ensure_data_dir(self) -> Path:
        """Create the builder data directory if needed and confirm it is writable.

        Called explicitly at application startup rather than during validation, so
        that importing this module has no filesystem side effects and stays testable
        (docs/runtime.md 6).
        """
        path = self.builder_data_dir.expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-probe"
            probe.touch()
            probe.unlink()
        except OSError as exc:
            raise ConfigurationError(
                "BUILDER_DATA_DIR is not creatable or not writable "
                f"(reason: {exc.strerror})."
            ) from None
        return path

    def startup_warnings(self) -> list[str]:
        """Non-fatal configuration warnings for the caller to log at startup.

        Returned rather than logged here so this module stays free of logging setup
        and remains a pure configuration boundary.
        """
        warnings: list[str] = []
        if not self.is_loopback_bound:
            warnings.append(
                f"API_HOST is '{self.api_host}', which is not loopback. There is no "
                "authentication in v1: knowing a project_id grants read and mutate "
                "access. Do not expose this server publicly (docs/security.md 9)."
            )
        if self.app_env is AppEnv.PRODUCTION:
            warnings.append(
                "APP_ENV is 'production' but v1 has no authentication layer "
                "(docs/api.md 1.1)."
            )
        if not self.is_database_configured:
            # Surfaced loudly because the consequence is easy to mistake for a bug:
            # a gate opens, the process restarts, and the run is unanswerable. That
            # is the known limitation docs/state.md 5.1 records, not a fault.
            warnings.append(
                "DATABASE_URL is not set. Running on the interim filesystem "
                "repository and the in-process checkpointer: every pending HITL "
                "gate is lost on restart (docs/state.md 5.1)."
            )
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build and validate the settings object once per process.

    Raises:
        ConfigurationError: with the offending variable *names* only. The underlying
            ValidationError is deliberately not chained (``from None``) because its
            representation can include submitted input values, which for
            ANTHROPIC_API_KEY would mean a secret in a traceback
            (docs/runtime.md 6, docs/security.md 2, 6).
    """
    try:
        return Settings()
    except ValidationError as exc:
        names = sorted(
            {
                str(error["loc"][0]).upper()
                for error in exc.errors()
                if error.get("loc")
            }
        )
        raise ConfigurationError(
            "Invalid backend configuration. Check these environment variables: "
            + (", ".join(names) or "(unknown)")
            + ". See .env.example and docs/runtime.md 3.2."
        ) from None
