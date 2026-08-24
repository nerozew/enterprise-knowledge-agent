from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import engine
from app.core.errors import install_error_handlers
from app.core.middleware import RequestIdMiddleware
from app.mcp.p9_server import P9McpRuntime, create_p9_runtime_if_enabled
from app.services.knowledge import close_knowledge_adapters, knowledge_capabilities


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Create the real API application with injectable startup configuration."""

    resolved_settings = app_settings or get_settings()
    p9_runtime = create_p9_runtime_if_enabled(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            async with AsyncExitStack() as stack:
                if p9_runtime is not None:
                    await stack.enter_async_context(p9_runtime.server.session_manager.run())
                yield
        finally:
            await close_knowledge_adapters()
            await engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        debug=resolved_settings.debug,
        lifespan=lifespan,
    )
    application.state.p9_mcp_runtime = p9_runtime
    application.dependency_overrides[get_settings] = lambda: resolved_settings
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "Last-Event-ID",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID", "Deprecation", "Link"],
    )
    install_error_handlers(application)
    application.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    if p9_runtime is not None:
        application.mount(
            resolved_settings.mcp_p9_http_mount_path,
            p9_runtime.http_app,
            name="p9-mcp-streamable-http",
        )

    @application.get("/health/live", tags=["健康检查"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready", tags=["健康检查"])
    async def health_ready(request: Request) -> JSONResponse:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                if resolved_settings.app_env.casefold() not in {"test", "testing"}:
                    await connection.execute(text("SELECT 1 FROM agent_run_dispatches LIMIT 1"))
            return JSONResponse({"status": "ready"})
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "error": {
                        "code": "database_unavailable",
                        "message": "数据库暂不可用",
                        "details": None,
                    },
                    "request_id": getattr(request.state, "request_id", None),
                },
            )

    @application.get("/health/capabilities", tags=["健康检查"])
    async def health_capabilities() -> dict:
        return {
            "status": "ok",
            "database": {"configured": True, "critical": True},
            "agent_worker": {
                "deployment": "separate_process",
                "poll_seconds": resolved_settings.agent_dispatch_poll_seconds,
                "lease_seconds": resolved_settings.agent_dispatch_lease_seconds,
                "renew_seconds": resolved_settings.agent_dispatch_renew_seconds,
                "max_attempts": resolved_settings.agent_dispatch_max_attempts,
                "redis_wakeup_configured": bool(resolved_settings.redis_url),
                "redis_wakeup_enabled": False,
                "mysql_polling_fallback": True,
            },
            "mcp": {
                "p9_enabled": resolved_settings.mcp_p9_enabled,
                "protocol_version": resolved_settings.mcp_p9_protocol_version,
                "streamable_http": p9_runtime is not None,
                "stdio_entrypoint": "python -m app.mcp.p9_stdio",
                "business_capabilities_registered": False,
                "skeleton_only": True,
                "authenticated": False,
            },
            "optional": await knowledge_capabilities(resolved_settings),
        }

    return application


def get_p9_runtime(application: FastAPI) -> P9McpRuntime | None:
    """Return the factory-owned runtime for integration tests and lifecycle hooks."""

    runtime: P9McpRuntime | None = application.state.p9_mcp_runtime
    return runtime


settings = get_settings()
app = create_app(settings)
