"""MCP client: connect, discover real capabilities, classify them, filter per role.

``docs/mcp.md`` 4 forbids hard-coding or assuming MCP capabilities, so this module
never names a tool. It asks the server what it has, validates the answer, and
classifies each tool from the protocol's own annotations.

Discovery only. Executing a tool belongs behind the approval gate
(``docs/mcp.md`` 5) and is deliberately not implemented here.

Spec: docs/mcp.md 3, 4, 6; docs/api.md 2.7, 5; docs/security.md 6.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import SecretStr

from backend.schemas.enums import AgentRole

logger = logging.getLogger(__name__)

CapabilityClass = Literal["read", "write", "destructive"]

# Seconds. Discovery is one request/response; a longer budget would let an
# unresponsive server stall the stage that asked.
DEFAULT_TIMEOUT_SECONDS = 30.0

# The transport and session timeouts are set to the overall deadline times this,
# so the outer wait_for is always the one that fires and the error message is
# deterministic. They remain as a backstop, not as the primary bound.
_INNER_TIMEOUT_HEADROOM = 1.5

# Which capability classes each role may ever see (docs/mcp.md 3). Roles absent
# from this table get nothing. The GitHub Operator's set is narrowed again later
# by the approved plan -- this is the outer bound, not the final grant.
_ROLE_CAPABILITIES: dict[AgentRole, frozenset[CapabilityClass]] = {
    AgentRole.CODE_REVIEWER: frozenset({"read"}),
    AgentRole.VISUAL_UX_CRITIC: frozenset({"read"}),
    AgentRole.ACCESSIBILITY_REVIEWER: frozenset({"read"}),
    AgentRole.ANTI_SLOP_REVIEWER: frozenset({"read"}),
    AgentRole.GITHUB_OPERATOR: frozenset({"read", "write", "destructive"}),
}


class McpError(RuntimeError):
    """Base for MCP client failures."""


class McpUnavailableError(McpError):
    """The server could not be reached, or refused the connection."""


class McpCapabilityError(McpError):
    """A required capability is absent or its schema is unusable.

    Maps to ``capability_unavailable`` / 501: the caller surfaces it rather than
    substituting another tool (docs/mcp.md 4).
    """


@dataclass(frozen=True)
class DiscoveredTool:
    """One tool the server actually advertises.

    ``capability`` is derived from the server's own annotations, never from the
    tool's name -- names differ between servers and versions.
    """

    name: str
    capability: CapabilityClass
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def requires_approval(self) -> bool:
        """Anything that is not read-only passes through the approval gate."""
        return self.capability != "read"


@dataclass(frozen=True)
class DiscoveryResult:
    """What one server advertised, at one moment."""

    server_url: str
    server_name: str | None
    protocol_version: str | None
    tools: dict[str, DiscoveredTool] = field(default_factory=dict)

    def by_capability(self, *classes: CapabilityClass) -> dict[str, DiscoveredTool]:
        return {n: t for n, t in self.tools.items() if t.capability in classes}

    def require(self, name: str) -> DiscoveredTool:
        """The named tool, or raise. Used to check a plan before executing it."""
        tool = self.tools.get(name)
        if tool is None:
            raise McpCapabilityError(f"The server does not provide '{name}'.")
        return tool


def classify_tool(annotations: Any) -> CapabilityClass:
    """Classify from the protocol's annotations, defaulting to the safer answer.

    An unannotated tool is treated as a **write**, never a read: calling an
    unknown tool without approval is the failure that matters, and a tool
    wrongly sent through the approval gate costs only a confirmation.
    """
    read_only = getattr(annotations, "read_only_hint", None)
    destructive = getattr(annotations, "destructive_hint", None)

    if destructive is True:
        return "destructive"
    if read_only is True:
        return "read"
    return "write"


def validate_tool(tool: Any) -> DiscoveredTool:
    """Check one advertised tool is usable, and classify it.

    Validates the shape the protocol promises rather than any expected content:
    a usable tool needs a name and an object input schema, because model-produced
    arguments are checked against that schema before any call (docs/mcp.md 4).
    """
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise McpCapabilityError("The server advertised a tool with no name.")

    schema = getattr(tool, "input_schema", None)
    if not isinstance(schema, dict):
        raise McpCapabilityError(f"Tool '{name}' has no usable input schema.")
    if schema.get("type") not in (None, "object"):
        raise McpCapabilityError(
            f"Tool '{name}' takes {schema.get('type')!r}, not an object."
        )

    return DiscoveredTool(
        name=name,
        capability=classify_tool(getattr(tool, "annotations", None)),
        description=getattr(tool, "description", None),
        input_schema=schema,
    )


def tools_for_role(
    discovery: DiscoveryResult, role: AgentRole
) -> dict[str, DiscoveredTool]:
    """The subset a role may be offered, filtered before the agent sees it.

    An agent cannot call a tool it was never shown, so this filter -- not the
    agent's own restraint -- is what enforces least privilege (docs/mcp.md 3).
    """
    allowed = _ROLE_CAPABILITIES.get(role, frozenset())
    if not allowed:
        return {}
    return discovery.by_capability(*allowed)


class McpClient:
    """Connects to one MCP server and reports what it can actually do.

    The token is held as a ``SecretStr`` and used only to build the auth header.
    It is never logged, never returned, and never reaches AgentContext
    (docs/mcp.md 6).
    """

    def __init__(
        self,
        server_url: str,
        token: SecretStr | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """
        Args:
            server_url: the MCP endpoint. Injected, so no lower layer reads config.
            token: bearer credential, or None for an unauthenticated server.
            timeout_seconds: bound on the discovery request.
        """
        if not server_url.strip():
            raise ValueError("server_url is required")
        self._url = server_url
        self._token = token
        self._timeout = timeout_seconds

    def __repr__(self) -> str:
        """Never render the token, including in a traceback or a log record."""
        return f"McpClient(server_url={self._url!r}, token={'set' if self._token else 'none'})"

    def _headers(self) -> dict[str, str]:
        """Auth header, built at the point of use so the secret is not stored."""
        if self._token is None:
            return {}
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    async def _connect_and_list(self) -> tuple[Any, Any]:
        """One connect/initialize/list round trip.

        Separate from :meth:`discover` so the whole thing sits inside a single
        ``wait_for``. Transport-level timeouts alone are not enough: a server
        that accepts the socket and never answers hangs the handshake, not a
        request.
        """
        try:
            import httpx2
            from mcp import ClientSession
            from mcp.client.streamable_http import (
                create_mcp_http_client,
                streamable_http_client,
            )
        except ImportError:  # pragma: no cover - depends on the environment
            raise McpUnavailableError(
                "The MCP client library is not installed."
            ) from None

        # The inner timeouts get headroom so the outer wait_for always wins the
        # race and the caller gets one deterministic message. Set equal, they
        # fired together and a timeout reported as "could not be reached" about
        # half the time. They stay as a backstop for a stall wait_for cannot see.
        inner = self._timeout * _INNER_TIMEOUT_HEADROOM

        try:
            http_client = create_mcp_http_client(
                headers=self._headers(), timeout=httpx2.Timeout(inner)
            )
            async with streamable_http_client(
                self._url, http_client=http_client
            ) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(
                    read_stream, write_stream, read_timeout_seconds=inner
                ) as session:
                    return await session.initialize(), await session.list_tools()
        except (McpError, asyncio.CancelledError):
            raise
        except Exception as exc:
            # The message may carry the URL, headers or a provider payload, so
            # only the type is reported outward (docs/security.md 6).
            logger.warning("mcp discovery failed type=%s", type(exc).__name__)
            raise McpUnavailableError(
                "The MCP server could not be reached."
            ) from None

    async def discover(self) -> DiscoveryResult:
        """Connect, list the server's real tools, validate and classify them.

        This is the sequence ``docs/mcp.md`` 4 requires before first use. It
        performs no mutation: ``list_tools`` is a read of the server's manifest.

        The whole call is bounded by ``timeout_seconds``.

        Raises:
            McpUnavailableError: the server could not be reached, timed out, or
                could not be initialized.
            McpCapabilityError: it advertised a tool this client cannot use.
        """
        try:
            init, listing = await asyncio.wait_for(
                self._connect_and_list(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            logger.warning("mcp discovery timed out after %ss", self._timeout)
            raise McpUnavailableError(
                "The MCP server did not respond in time."
            ) from None

        tools = {t.name: t for t in (validate_tool(x) for x in listing.tools)}
        server_info = getattr(init, "server_info", None)

        logger.info(
            "mcp discovery ok tools=%d read=%d write=%d destructive=%d",
            len(tools),
            sum(1 for t in tools.values() if t.capability == "read"),
            sum(1 for t in tools.values() if t.capability == "write"),
            sum(1 for t in tools.values() if t.capability == "destructive"),
        )
        return DiscoveryResult(
            server_url=self._url,
            server_name=getattr(server_info, "name", None),
            protocol_version=getattr(init, "protocol_version", None),
            tools=tools,
        )
