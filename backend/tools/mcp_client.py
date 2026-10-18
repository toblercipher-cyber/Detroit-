"""MCP client: connect, discover real capabilities, classify them, filter per role.

``docs/mcp.md`` 4 forbids hard-coding or assuming MCP capabilities, so this module
never names a tool. It asks the server what it has, validates the answer, and
classifies each tool from the protocol's own annotations.

NOTE ON THE ACTIVE PATH: ``call_tool`` is NOT used by the GitHub push feature,
which is deterministic and goes through ``backend/tools/github_rest.py``. Its
only consumer is the GitHub Operator agent, which is dormant until an MCP
server is configured and is retained because ``CLAUDE.md`` 5 lists that role as
finalized. ``discover`` IS used on the live path: ``GitHubGateway.probe_mcp``
calls it so the status route can report what a configured server really
advertises instead of assuming.

Discovery AND execution. ``call_tool`` runs a tool the server actually
advertised, after checking three things that are not interchangeable: the tool
was discovered, the calling role may see its capability class, and the
arguments match the schema the server published.

Execution does NOT imply approval. A write or destructive tool must already
have passed the approval gate in ``backend/services/external_actions.py``
before it reaches here; this module refuses to be the place that decides
(``docs/mcp.md`` 5). ``call_tool`` takes an explicit ``approved`` flag and
raises if a non-read tool arrives without it.

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


class McpApprovalError(McpError):
    """A write or destructive tool was called without approval.

    A programming error rather than a user error: the approval gate runs
    earlier, so reaching here means a caller skipped it. Loud, never silent.
    """


class McpToolError(McpError):
    """The tool ran and the server reported failure.

    Distinct from :class:`McpUnavailableError`, which means the call never
    happened. "Did not run" and "ran and failed" need different handling: only
    the first is worth retrying.
    """


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


@dataclass(frozen=True)
class ToolResult:
    """What one tool call returned.

    ``content`` is the server's text output. It is UNTRUSTED: a repository
    file or a PR comment can contain text addressed to the model, and it is
    data, never instructions (``docs/security.md`` 5). Callers parse it; no
    caller should route it back into a prompt as a directive.
    """

    tool: str
    content: str
    structured: dict[str, Any] | None = None


def _text_content(raw: Any) -> str:
    """Flatten an MCP tool result's content blocks into text.

    The protocol returns a list of typed blocks. Only text blocks are read;
    an image or an embedded resource is skipped rather than coerced, because
    nothing downstream of this is prepared to handle one.
    """
    blocks = getattr(raw, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return '\n'.join(parts)


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


def validate_arguments(tool: DiscoveredTool, arguments: dict[str, Any]) -> None:
    """Check model- or client-produced arguments against the server's schema.

    ``docs/mcp.md`` 4: "Model-produced tool arguments are untrusted and must be
    validated against the discovered schema before invocation." This is that
    check, and it runs against the schema the SERVER published -- never against
    an expectation written here, which would be the hard-coding 4 forbids.

    Deliberately a shape check, not a full JSON Schema implementation:
      - every ``required`` property must be present
      - no property outside ``properties`` may be sent when the schema closes
        the set with ``additionalProperties: false``
      - declared primitive types must match

    A schema this cannot interpret is ACCEPTED rather than rejected. The server
    validates too, and refusing everything we do not fully understand would
    make the client unusable against any server with a rich schema. The
    controls that must not be skipped -- discovery, role filtering, approval --
    are enforced elsewhere and do not depend on this.

    Raises:
        McpCapabilityError: the arguments cannot be sent as they are.
    """
    schema = tool.input_schema or {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return  # nothing declared to check against

    required = schema.get("required") or []
    if isinstance(required, list):
        missing = [r for r in required if isinstance(r, str) and r not in arguments]
        if missing:
            raise McpCapabilityError(
                f"Tool '{tool.name}' requires {', '.join(sorted(missing))}."
            )

    if schema.get("additionalProperties") is False:
        unknown = [k for k in arguments if k not in properties]
        if unknown:
            raise McpCapabilityError(
                f"Tool '{tool.name}' does not accept {', '.join(sorted(unknown))}."
            )

    _JSON_TYPES: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list,),
        "object": (dict,),
    }
    for key, value in arguments.items():
        declared = properties.get(key)
        if not isinstance(declared, dict):
            continue
        expected = declared.get("type")
        if not isinstance(expected, str):
            continue  # union types and $ref are left to the server
        allowed = _JSON_TYPES.get(expected)
        if allowed is None:
            continue
        # bool is a subclass of int; an integer field must not accept True.
        if expected in ("integer", "number") and isinstance(value, bool):
            raise McpCapabilityError(
                f"Tool '{tool.name}' expects {expected} for '{key}'."
            )
        if value is not None and not isinstance(value, allowed):
            raise McpCapabilityError(
                f"Tool '{tool.name}' expects {expected} for '{key}'."
            )


def role_may_call(role: AgentRole, tool: DiscoveredTool) -> bool:
    """Whether this role is permitted this tool's capability class.

    The same table that filters the OFFERED list (:func:`tools_for_role`),
    applied again at the moment of the call. Filtering a list is a UI
    convenience; this is the enforcement, because a caller can pass any name.
    """
    return tool.capability in _ROLE_CAPABILITIES.get(role, frozenset())


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

    async def _connect_and_call(
        self, name: str, arguments: dict[str, Any]
    ) -> Any:
        """One connect/initialize/call round trip.

        A separate connection per call rather than a pooled session: these
        calls are rare, human-approved, and a long-lived session would have to
        survive token refresh and server restarts for no benefit here.
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
                    await session.initialize()
                    return await session.call_tool(name, arguments)
        except (McpError, asyncio.CancelledError):
            raise
        except Exception as exc:
            # Never the message: it can carry the URL, the auth header or a
            # provider payload (docs/security.md 6).
            logger.warning(
                "mcp call failed tool=%s type=%s", name, type(exc).__name__
            )
            raise McpUnavailableError(
                "The MCP server could not be reached."
            ) from None

    async def call_tool(
        self,
        discovery: DiscoveryResult,
        name: str,
        arguments: dict[str, Any],
        *,
        role: AgentRole,
        approved: bool = False,
    ) -> ToolResult:
        """Execute one discovered tool.

        FOUR CHECKS, IN THIS ORDER, AND NONE IS REDUNDANT
            1. **Discovered.** The tool must be in ``discovery``. A name that
               was never advertised is never sent, so a caller cannot invent
               one (``docs/mcp.md`` 4).
            2. **Role.** The role must be allowed this capability class. The
               offered list is filtered too, but a caller can pass any string,
               so the check is repeated where it binds.
            3. **Approved.** Anything that is not read-only requires
               ``approved=True``, which only the approval gate should produce.
               This module never decides approval; it refuses without it.
            4. **Arguments.** Validated against the server's own published
               schema before anything is sent.

        Args:
            discovery: the result of :meth:`discover`. Passed in rather than
                re-fetched so one discovery can back a whole approved plan,
                and so the tool set cannot change between check and call.
            name: the tool to run.
            arguments: untrusted; validated against the discovered schema.
            role: the agent role on whose behalf this runs.
            approved: set only by a caller holding a spent approval token.

        Raises:
            McpCapabilityError: not discovered, role not permitted, or bad
                arguments (-> 501).
            McpApprovalError: a write/destructive tool without approval (-> 403).
            McpUnavailableError: unreachable or timed out (-> 503).
            McpToolError: the server ran it and reported failure (-> 502).
        """
        tool = discovery.require(name)  # 1

        if not role_may_call(role, tool):  # 2
            logger.warning(
                "mcp call refused: role=%s may not call a %s tool",
                role.value,
                tool.capability,
            )
            raise McpCapabilityError(
                f"Role '{role.value}' may not call a {tool.capability} tool."
            )

        if tool.requires_approval and not approved:  # 3
            logger.warning(
                "mcp call refused: %s tool '%s' without approval",
                tool.capability,
                name,
            )
            raise McpApprovalError(
                f"Tool '{name}' is {tool.capability} and requires an approval."
            )

        validate_arguments(tool, arguments)  # 4

        try:
            raw = await asyncio.wait_for(
                self._connect_and_call(name, arguments), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            logger.warning("mcp call timed out tool=%s after %ss", name, self._timeout)
            raise McpUnavailableError(
                "The MCP server did not respond in time."
            ) from None

        is_error = bool(getattr(raw, "is_error", False))
        content = _text_content(raw)
        structured = getattr(raw, "structured_content", None)

        # Outcome and tool name only. Tool OUTPUT is untrusted content and is
        # never logged (docs/security.md 5, 10).
        logger.info(
            "mcp call tool=%s capability=%s ok=%s",
            name,
            tool.capability,
            not is_error,
        )

        if is_error:
            raise McpToolError(f"Tool '{name}' reported a failure.")

        return ToolResult(
            tool=name,
            content=content,
            structured=structured if isinstance(structured, dict) else None,
        )

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
