from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from enum import StrEnum
from importlib.metadata import version as package_version
from typing import Literal
from uuid import UUID

from mcp import types as mcp_types
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.models.entities import MemberRole

MCP_P9_PROTOCOL_VERSION = "2025-11-25"
MCP_P9_SDK_VERSION = "1.28.1"


class McpTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class McpCallContext(BaseModel):
    """Trusted per-call identity injected by an MCP transport adapter.

    This model is never part of a Tool input schema. B2 is responsible for
    resolving ``actor_role`` from the MySQL membership fact on every call.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1, max_length=128)
    transport: McpTransport
    actor_id: UUID
    workspace_id: UUID
    actor_role: MemberRole
    client_name: str | None = Field(default=None, max_length=120)
    client_version: str | None = Field(default=None, max_length=64)
    protocol_version: Literal["2025-11-25"] = "2025-11-25"


class McpStdioIdentity(BaseModel):
    """Process identity loaded from the stdio launch environment.

    B1 validates that a launch credential exists, but does not authenticate it.
    Authentication and membership rechecks are explicitly deferred to B2.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: UUID
    workspace_id: UUID


_current_context: ContextVar[McpCallContext | None] = ContextVar(
    "p9_mcp_call_context", default=None
)


def assert_mcp_runtime_compatibility() -> None:
    """Fail fast when the installed SDK or negotiated protocol can drift."""

    installed = package_version("mcp")
    if installed != MCP_P9_SDK_VERSION:
        raise RuntimeError(
            f"P9 MCP requires mcp=={MCP_P9_SDK_VERSION}; installed version is {installed}"
        )
    if (
        mcp_types.LATEST_PROTOCOL_VERSION != MCP_P9_PROTOCOL_VERSION
        or MCP_P9_PROTOCOL_VERSION not in SUPPORTED_PROTOCOL_VERSIONS
    ):
        raise RuntimeError(
            f"P9 MCP requires protocol {MCP_P9_PROTOCOL_VERSION}; "
            f"SDK latest is {mcp_types.LATEST_PROTOCOL_VERSION}"
        )


def load_stdio_identity(settings: Settings) -> McpStdioIdentity:
    """Load P9 stdio identity without exposing the credential to MCP schemas."""

    if not settings.mcp_p9_enabled:
        raise RuntimeError("P9 MCP is disabled")
    if settings.mcp_p9_stdio_actor_id is None:
        raise RuntimeError("MCP_P9_STDIO_ACTOR_ID is required for stdio")
    if settings.mcp_p9_stdio_workspace_id is None:
        raise RuntimeError("MCP_P9_STDIO_WORKSPACE_ID is required for stdio")
    if settings.mcp_p9_stdio_credential is None:
        raise RuntimeError("MCP_P9_STDIO_CREDENTIAL is required for stdio")

    return McpStdioIdentity(
        actor_id=settings.mcp_p9_stdio_actor_id,
        workspace_id=settings.mcp_p9_stdio_workspace_id,
    )


def current_mcp_call_context() -> McpCallContext:
    """Return the transport-injected context or fail closed."""

    context = _current_context.get()
    if context is None:
        raise RuntimeError("MCP call context is not bound")
    return context


@contextmanager
def bind_mcp_call_context(context: McpCallContext) -> Iterator[None]:
    """Bind a trusted context for one capability invocation."""

    token: Token[McpCallContext | None] = _current_context.set(context)
    try:
        yield
    finally:
        _current_context.reset(token)
