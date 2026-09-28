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
    The 16 environment variables defined in ``docs/runtime.md`` 3.2, their types,
    their defaults, and their validation.

WHAT DOES NOT BELONG HERE
    - The per-role Claude temperature / max-token policy table (``docs/runtime.md``
      4.1). That is a static policy table, not environment configuration, and belongs
      in its own module under ``backend/config/``.
    - Database configuration. The database is Supabase/PostgreSQL but its schema is
      undecided by design, so no connection settings exist yet.
    - Any Impeccable enable/disable flag. Impeccable is a mandatory gate, not a
      feature toggle; a switch that could silently disable it must never exist.

Spec: docs/runtime.md 3.2, 3.4, 6; docs/security.md 2, 9; skills.md 25, 26.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The literal placeholder shipped in .env.example. Rejected explicitly so that
# copying the example to .env without editing fails at startup with a clear message,
# rather than surfacing later as a confusing provider authentication error.
PLACEHOLDER_API_KEY = "replace-with-your-anthropic-api-key"

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
    allowed_origins: list[str] = Field(
        default=["http://localhost:5173"],
        min_length=1,
        description="Explicit CORS allowlist. Comma-separated in the environment.",
    )

    # --- Builder runtime data (docs/state.md 5.1, docs/architecture.md 5.1) ---

    builder_data_dir: Path = Field(
        default=Path("./var"),
        description="Root for checkpoints, state artifacts, uploads, workspaces.",
    )

    # --- Reference image uploads (docs/api.md 2.4, docs/security.md 4) ---

    max_upload_bytes: int = Field(default=10_485_760, gt=0)
    allowed_upload_types: list[str] = Field(
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

    # --- Derived state and startup checks ---

    @property
    def is_loopback_bound(self) -> bool:
        """True when the API is unreachable from outside this machine."""
        return self.api_host in LOOPBACK_HOSTS

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
