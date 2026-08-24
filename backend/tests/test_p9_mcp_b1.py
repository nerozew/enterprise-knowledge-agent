from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryFile
from uuid import uuid4

import pytest
from anyio import fail_after
from httpx import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp import types as mcp_types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app, get_p9_runtime
from app.mcp.p9_contract import (
    MCP_P9_PROTOCOL_VERSION,
    McpCallContext,
    McpTransport,
    assert_mcp_runtime_compatibility,
    bind_mcp_call_context,
    current_mcp_call_context,
    load_stdio_identity,
)
from app.mcp.p9_registry import (
    McpCapabilityDescriptor,
    McpCapabilityKind,
    McpCapabilityRegistry,
    McpCapabilityRisk,
)
from app.mcp.p9_server import create_p9_mcp_server, create_p9_runtime_if_enabled
from app.models.entities import MemberRole

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def p9_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret": "test-secret-that-is-longer-than-32-characters",
        "mcp_p9_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def old_initialize_request() -> mcp_types.ClientRequest:
    return mcp_types.ClientRequest(
        mcp_types.InitializeRequest(
            params=mcp_types.InitializeRequestParams(
                protocolVersion="2025-06-18",
                capabilities=mcp_types.ClientCapabilities(),
                clientInfo=mcp_types.Implementation(name="negative-test", version="1"),
            )
        )
    )


def stdio_parameters() -> StdioServerParameters:
    environment = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "JWT_SECRET": "test-secret-that-is-longer-than-32-characters",
        "MCP_P9_ENABLED": "true",
        "MCP_P9_PROTOCOL_VERSION": MCP_P9_PROTOCOL_VERSION,
        "MCP_P9_STDIO_ACTOR_ID": str(uuid4()),
        "MCP_P9_STDIO_WORKSPACE_ID": str(uuid4()),
        "MCP_P9_STDIO_CREDENTIAL": "stdio-process-secret-that-is-long-enough",
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.p9_stdio"],
        env=environment,
        cwd=BACKEND_ROOT,
    )


def legacy_stdio_parameters(*, local_token: str) -> StdioServerParameters:
    environment = {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "JWT_SECRET": "test-secret-that-is-longer-than-32-characters",
        "MCP_USER_ID": str(uuid4()),
        "MCP_LOCAL_TOKEN": local_token,
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env=environment,
        cwd=BACKEND_ROOT,
    )


def test_p9_contract_is_frozen_and_feature_flag_is_a_rollback() -> None:
    assert_mcp_runtime_compatibility()
    assert create_p9_runtime_if_enabled(p9_settings(mcp_p9_enabled=False)) is None

    runtime = create_p9_runtime_if_enabled(p9_settings())
    assert runtime is not None
    assert runtime.server.name == "enterprise-knowledge-agent"
    assert runtime.server.settings.stateless_http is True
    assert runtime.server.settings.json_response is True

    with pytest.raises(ValidationError):
        p9_settings(mcp_p9_http_mount_path="/api/v1/mcp")
    with pytest.raises(ValidationError, match="before B2 authentication"):
        p9_settings(app_env="production", mcp_p9_enabled=True)


def test_stdio_identity_is_loaded_outside_tool_schema_and_secret_is_redacted() -> None:
    actor_id = uuid4()
    workspace_id = uuid4()
    secret = "stdio-process-secret-that-is-long-enough"
    settings = p9_settings(
        mcp_p9_stdio_actor_id=actor_id,
        mcp_p9_stdio_workspace_id=workspace_id,
        mcp_p9_stdio_credential=secret,
    )

    identity = load_stdio_identity(settings)

    assert identity.actor_id == actor_id
    assert identity.workspace_id == workspace_id
    assert secret not in repr(settings)
    assert secret not in identity.model_dump_json()
    assert set(identity.model_dump()) == {"actor_id", "workspace_id"}

    with pytest.raises(ValidationError):
        p9_settings(mcp_p9_stdio_credential="too-short")


def test_call_context_is_transport_injected_and_fails_closed() -> None:
    context = McpCallContext(
        request_id="request-1",
        transport=McpTransport.STDIO,
        actor_id=uuid4(),
        workspace_id=uuid4(),
        actor_role=MemberRole.VIEWER,
        client_name="pytest",
        client_version="1",
    )

    with pytest.raises(RuntimeError, match="not bound"):
        current_mcp_call_context()
    with bind_mcp_call_context(context):
        assert current_mcp_call_context() is context
    with pytest.raises(RuntimeError, match="not bound"):
        current_mcp_call_context()


def test_registry_rejects_duplicates_and_freezes_after_install() -> None:
    registry = McpCapabilityRegistry()
    descriptor = McpCapabilityDescriptor(
        name="placeholder",
        kind=McpCapabilityKind.TOOL,
        description="B1 registry contract test only.",
        minimum_role=MemberRole.VIEWER,
        risk=McpCapabilityRisk.READ_ONLY,
    )

    def installer(server: FastMCP) -> None:
        del server
        return None

    registry.register(descriptor, installer)

    with pytest.raises(ValueError, match="duplicate"):
        registry.register(descriptor, installer)

    registry.install_into(create_p9_mcp_server(p9_settings()))
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register(
            descriptor.model_copy(update={"name": "after_freeze"}), installer
        )


async def test_real_stdio_client_initializes_without_stdout_pollution() -> None:
    actor_id = uuid4()
    workspace_id = uuid4()
    environment = {
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "JWT_SECRET": "test-secret-that-is-longer-than-32-characters",
        "MCP_P9_ENABLED": "true",
        "MCP_P9_PROTOCOL_VERSION": MCP_P9_PROTOCOL_VERSION,
        "MCP_P9_STDIO_ACTOR_ID": str(actor_id),
        "MCP_P9_STDIO_WORKSPACE_ID": str(workspace_id),
        "MCP_P9_STDIO_CREDENTIAL": "stdio-process-secret-that-is-long-enough",
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.p9_stdio"],
        env=environment,
        cwd=BACKEND_ROOT,
    )

    with TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                resources = await session.list_resources()
                prompts = await session.list_prompts()

        stderr.seek(0)
        stderr_output = stderr.read()

    assert initialized.protocolVersion == MCP_P9_PROTOCOL_VERSION
    assert initialized.serverInfo.name == "enterprise-knowledge-agent"
    assert tools.tools == []
    assert resources.resources == []
    assert prompts.prompts == []
    assert str(actor_id) in stderr_output
    assert "stdio-process-secret" not in stderr_output


async def test_real_stdio_rejects_older_initialize_protocol() -> None:
    with TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        async with stdio_client(stdio_parameters(), errlog=stderr) as (read, write):
            async with ClientSession(read, write) as session:
                with pytest.raises(McpError, match="requires 2025-11-25"):
                    await session.send_request(
                        old_initialize_request(),
                        mcp_types.InitializeResult,
                    )


async def test_real_app_factory_lifecycle_and_http_initialization() -> None:
    settings = p9_settings()
    application = create_app(settings)
    runtime = get_p9_runtime(application)
    assert runtime is not None
    workspace_id = uuid4()
    url = f"http://localhost:8000/api/v1/workspaces/{workspace_id}/mcp"

    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://localhost:8000"
        ) as http_client:
            health = await http_client.get("/health/capabilities")
            assert health.status_code == 200
            assert health.json()["mcp"] == {
                "p9_enabled": True,
                "protocol_version": MCP_P9_PROTOCOL_VERSION,
                "streamable_http": True,
                "stdio_entrypoint": "python -m app.mcp.p9_stdio",
                "business_capabilities_registered": False,
                "skeleton_only": True,
                "authenticated": False,
            }
            async with streamable_http_client(
                url,
                http_client=http_client,
                terminate_on_close=False,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    tools = await session.list_tools()
                    resources = await session.list_resources()
                    prompts = await session.list_prompts()

    assert initialized.protocolVersion == MCP_P9_PROTOCOL_VERSION
    assert initialized.serverInfo.name == "enterprise-knowledge-agent"
    assert tools.tools == []
    assert resources.resources == []
    assert prompts.prompts == []


async def test_real_app_factory_does_not_mount_when_flag_is_disabled() -> None:
    application = create_app(p9_settings(mcp_p9_enabled=False))
    assert get_p9_runtime(application) is None
    workspace_id = uuid4()

    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://localhost:8000"
        ) as http_client:
            health = await http_client.get("/health/capabilities")
            response = await http_client.post(
                f"/api/v1/workspaces/{workspace_id}/mcp",
                json={},
            )

    assert health.status_code == 200
    assert health.json()["mcp"] == {
        "p9_enabled": False,
        "protocol_version": MCP_P9_PROTOCOL_VERSION,
        "streamable_http": False,
        "stdio_entrypoint": "python -m app.mcp.p9_stdio",
        "business_capabilities_registered": False,
        "skeleton_only": True,
        "authenticated": False,
    }
    assert response.status_code == 404


async def test_real_app_rejects_older_initialize_protocol() -> None:
    settings = p9_settings()
    application = create_app(settings)
    workspace_id = uuid4()
    url = f"http://localhost:8000/api/v1/workspaces/{workspace_id}/mcp"
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://localhost:8000"
        ) as http_client:
            async with streamable_http_client(
                url,
                http_client=http_client,
                terminate_on_close=False,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    with pytest.raises(McpError, match="requires 2025-11-25"):
                        await session.send_request(
                            old_initialize_request(),
                            mcp_types.InitializeResult,
                        )


async def test_real_http_recovers_from_interleaved_invalid_initializes() -> None:
    application = create_app(p9_settings())
    workspace_id = uuid4()
    url = f"http://localhost:8000/api/v1/workspaces/{workspace_id}/mcp"

    with fail_after(15):
        async with application.router.lifespan_context(application):
            async with AsyncClient(
                transport=ASGITransport(app=application),
                base_url="http://localhost:8000",
            ) as http_client:
                async with streamable_http_client(
                    url,
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        with fail_after(3):
                            with pytest.raises(McpError, match="requires 2025-11-25"):
                                await session.send_request(
                                    old_initialize_request(),
                                    mcp_types.InitializeResult,
                                )
                        with fail_after(3):
                            first = await session.initialize()
                        with fail_after(3):
                            with pytest.raises(McpError, match="requires 2025-11-25"):
                                await session.send_request(
                                    old_initialize_request(),
                                    mcp_types.InitializeResult,
                                )
                        with fail_after(3):
                            second = await session.initialize()
                        with fail_after(3):
                            tools = await session.list_tools()

    assert first.protocolVersion == MCP_P9_PROTOCOL_VERSION
    assert second.protocolVersion == MCP_P9_PROTOCOL_VERSION
    assert tools.tools == []


async def test_legacy_stdio_real_client_lists_tools_without_protocol_pollution() -> None:
    local_token = "legacy-local-token-that-must-never-appear-in-logs"
    expected_tools = {
        "inspiration_search",
        "inspiration_get",
        "inspiration_create",
        "inspiration_update",
        "inspiration_delete",
        "inspiration_restore",
        "tag_list",
        "tag_create",
        "tag_update",
        "tag_delete",
        "ai_generate",
    }

    with TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        with fail_after(15):
            async with stdio_client(
                legacy_stdio_parameters(local_token=local_token), errlog=stderr
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    with fail_after(5):
                        initialized = await session.initialize()
                    with fail_after(5):
                        tools = await session.list_tools()

        stderr.seek(0)
        stderr_output = stderr.read()

    assert initialized.serverInfo.name == "日常灵感智能助手"
    assert expected_tools <= {tool.name for tool in tools.tools}
    # A real ClientSession parsed every stdout frame above; any non-protocol output
    # would make initialize/list_tools fail rather than being silently accepted.
    assert local_token not in stderr_output
