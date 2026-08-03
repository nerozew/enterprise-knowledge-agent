from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-that-is-longer-than-32-characters"
os.environ["AI_PROVIDER_URL"] = ""
os.environ["AI_API_KEY"] = ""
os.environ["AI_MODEL"] = ""
os.environ["REDIS_URL"] = ""
os.environ["MINIO_ENDPOINT"] = ""
os.environ["MINIO_ACCESS_KEY"] = ""
os.environ["MINIO_SECRET_KEY"] = ""
os.environ["MINIO_SECURE"] = "false"
os.environ["MILVUS_URI"] = ""
os.environ["MILVUS_TOKEN"] = ""
os.environ["RAG_PROVIDER_MODE"] = "FIXTURE"
os.environ["RAG_EMBEDDING_URL"] = ""
os.environ["RAG_EMBEDDING_API_KEY"] = ""
os.environ["RAG_EMBEDDING_MODEL"] = ""
os.environ["RAG_RERANKER_URL"] = ""
os.environ["RAG_RERANKER_API_KEY"] = ""
os.environ["RAG_RERANKER_MODEL"] = ""
os.environ["RAG_RERANKER_PROFILE"] = "OPENAI_COMPATIBLE"
os.environ["RAG_RERANKER_ENDPOINT"] = ""
os.environ["RAG_LLM_URL"] = ""
os.environ["RAG_LLM_API_KEY"] = ""
os.environ["RAG_LLM_MODEL"] = ""
os.environ["RAG_LLM_PROFILE"] = "OPENAI_JSON_SCHEMA"
os.environ["RAG_SYNC_INGESTION_ENABLED"] = "true"

from app.core.database import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def register(client: AsyncClient, email: str = "user@example.com") -> tuple[str, dict]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass123!", "display_name": "测试用户"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["user"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
