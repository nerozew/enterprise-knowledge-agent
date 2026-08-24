from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, cast

from anyio import EndOfStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage
from starlette.applications import Starlette

from app.core.config import Settings, get_settings
from app.mcp.p9_contract import (
    MCP_P9_PROTOCOL_VERSION,
    assert_mcp_runtime_compatibility,
)
from app.mcp.p9_registry import McpCapabilityRegistry, empty_p9_registry


@dataclass(frozen=True, slots=True)
class P9McpRuntime:
    """Feature-flagged P9 server and its mountable HTTP application."""

    server: FastMCP
    http_app: Starlette


class _FrozenProtocolReceiveStream:
    """Filter unsupported initialize messages before ``ServerSession`` sees them."""

    def __init__(
        self,
        source: MemoryObjectReceiveStream[SessionMessage | Exception],
        response_stream: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        self._source = source
        self._response_stream = response_stream

    async def __aenter__(self) -> Self:
        await self._source.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._source.__aexit__(exc_type, exc_value, traceback)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> SessionMessage | Exception:
        while True:
            try:
                message = await self._source.receive()
            except EndOfStream as exc:
                raise StopAsyncIteration from exc

            if isinstance(message, Exception):
                return message
            root = message.message.root
            if not (
                isinstance(root, mcp_types.JSONRPCRequest)
                and root.method == "initialize"
            ):
                return message

            params = root.params or {}
            requested_version = str(params.get("protocolVersion", ""))
            if requested_version == MCP_P9_PROTOCOL_VERSION:
                return message

            error = mcp_types.JSONRPCError(
                jsonrpc="2.0",
                id=root.id,
                error=mcp_types.ErrorData(
                    code=mcp_types.INVALID_PARAMS,
                    message=(
                        "Unsupported MCP protocol version; "
                        f"P9 requires {MCP_P9_PROTOCOL_VERSION}"
                    ),
                ),
            )
            await self._response_stream.send(
                SessionMessage(mcp_types.JSONRPCMessage(error))
            )


def _install_frozen_protocol_guard(server: FastMCP[Any]) -> None:
    """Reject initialize requests outside the P9 frozen protocol.

    MCP SDK 1.28.1 normally negotiates every protocol version it supports. P9
    deliberately exposes only 2025-11-25, so the guard runs before the SDK
    ``ServerSession`` on both stdio and Streamable HTTP transports.
    """

    low_level_server = server._mcp_server  # noqa: SLF001 - SDK is exactly pinned
    original_run = low_level_server.run

    async def strict_run(
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
        write_stream: MemoryObjectSendStream[SessionMessage],
        initialization_options: InitializationOptions,
        raise_exceptions: bool = False,
        stateless: bool = False,
    ) -> None:
        guarded_reader = _FrozenProtocolReceiveStream(read_stream, write_stream)
        await original_run(
            cast(
                MemoryObjectReceiveStream[SessionMessage | Exception], guarded_reader
            ),
            write_stream,
            initialization_options,
            raise_exceptions=raise_exceptions,
            stateless=stateless,
        )

    # FastMCP has no public session-factory/protocol allowlist hook in 1.28.1.
    # The pinned SDK plus protocol-level tests make this narrow adapter explicit.
    setattr(low_level_server, "run", strict_run)  # noqa: B010


def create_p9_mcp_server(
    settings: Settings | None = None,
    registry: McpCapabilityRegistry | None = None,
) -> FastMCP:
    """Create the isolated P9 MCP server with no B1 business capabilities."""

    resolved_settings = settings or get_settings()
    assert_mcp_runtime_compatibility()
    if resolved_settings.mcp_p9_protocol_version != "2025-11-25":  # pragma: no cover
        raise RuntimeError("P9 MCP protocol version is not frozen to 2025-11-25")

    server = FastMCP(
        name="enterprise-knowledge-agent",
        instructions="P9 MCP 能力边界。B1 只提供协议骨架，不注册任何业务 Tool。",
        json_response=True,
        stateless_http=True,
        streamable_http_path="/mcp",
        log_level="INFO",
    )
    _install_frozen_protocol_guard(server)
    (registry or empty_p9_registry()).install_into(server)
    return server


def create_p9_streamable_http_app(server: FastMCP | None = None) -> Starlette:
    """Build the stateless JSON-response ASGI app for FastAPI mounting."""

    return (server or create_p9_mcp_server()).streamable_http_app()


def create_p9_runtime_if_enabled(settings: Settings) -> P9McpRuntime | None:
    """Build the isolated runtime only when the rollback flag is enabled."""

    if not settings.mcp_p9_enabled:
        return None
    server = create_p9_mcp_server(settings)
    return P9McpRuntime(server=server, http_app=server.streamable_http_app())
