from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.agent.hitl import TodoApprovalResumeWorker, TodoWorkflowInput
from app.agent.models import AgentMessage, TodoCreateInput, ToolProposal
from app.models.agent import AgentRun, AgentRunEvent
from app.models.base import utc_now
from app.repositories.agent import AgentRepository
from app.services.agent_api import AgentEventStreamService
from app.services.workspaces import canonical_request_hash
from tests.conftest import auth_headers, register


async def personal_workspace(client: AsyncClient, token: str) -> UUID:
    response = await client.get("/api/v1/workspaces", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    return UUID(response.json()["items"][0]["id"])


def test_sse_heartbeat_has_no_business_event_id_or_sequence() -> None:
    heartbeat = AgentEventStreamService.encode_heartbeat(UUID(int=1))
    assert heartbeat.startswith("event: heartbeat\ndata: ")
    assert "\nid:" not in heartbeat
    assert '"seq"' not in heartbeat
    assert '"sequence_no"' not in heartbeat


async def create_conversation_and_run(
    client: AsyncClient, token: str, workspace_id: UUID
) -> tuple[dict, dict]:
    conversation_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "P8 API 会话"},
        headers={**auth_headers(token), "Idempotency-Key": "conversation-create-001"},
    )
    assert conversation_response.status_code == 201, conversation_response.text
    conversation = conversation_response.json()
    run_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation['id']}/runs",
        json={
            "conversation_version": conversation["version"],
            "message": "帮我整理本周工作",
            "knowledge_base_ids": [],
            "retrieval_strategy": "BASIC",
            "budget": {"max_tokens": 1024},
        },
        headers={**auth_headers(token), "Idempotency-Key": "agent-run-create-001"},
    )
    assert run_response.status_code == 202, run_response.text
    return conversation, run_response.json()


@pytest.mark.asyncio
async def test_agent_rest_snapshot_idempotency_cas_scope_and_sse_replay(
    client: AsyncClient, session_factory
) -> None:
    token, user = await register(client, "agent-api-owner@example.com")
    workspace_id = await personal_workspace(client, token)
    conversation, accepted = await create_conversation_and_run(client, token, workspace_id)
    run_id = accepted["resource_id"]

    replay = await client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "P8 API 会话"},
        headers={**auth_headers(token), "Idempotency-Key": "conversation-create-001"},
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == conversation["id"]
    conflict = await client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations",
        json={"title": "不同请求"},
        headers={**auth_headers(token), "Idempotency-Key": "conversation-create-001"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"

    snapshot_response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}",
        headers=auth_headers(token),
    )
    assert snapshot_response.status_code == 200, snapshot_response.text
    snapshot = snapshot_response.json()
    assert snapshot["run"]["status"] == "RUNNING"
    assert snapshot["messages"][0]["content"] == "帮我整理本周工作"
    assert snapshot["last_event_id"] == "1"
    assert snapshot["last_sequence_no"] == 1

    second_run = await client.post(
        f"/api/v1/workspaces/{workspace_id}/conversations/{conversation['id']}/runs",
        json={
            "conversation_version": 2,
            "message": "并发请求",
            "knowledge_base_ids": [],
            "retrieval_strategy": "BASIC",
            "budget": {"max_tokens": 1024},
        },
        headers={**auth_headers(token), "Idempotency-Key": "agent-run-create-002"},
    )
    assert second_run.status_code == 409
    assert second_run.json()["error"]["code"] == "active_run_exists"

    bad_cas = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/cancel",
        json={"version": 1},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "agent-run-cancel-bad",
            "If-Match": '"2"',
        },
    )
    assert bad_cas.status_code == 409
    assert bad_cas.json()["error"]["code"] == "version_conflict"

    cancelled = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/cancel",
        json={"version": 1, "reason": "测试 SSE 终态回放"},
        headers={
            **auth_headers(token),
            "Idempotency-Key": "agent-run-cancel-001",
            "If-Match": '"1"',
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["run"]["status"] == "CANCELLED"
    assert cancelled.json()["last_sequence_no"] == 2

    stream = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/events",
        headers={**auth_headers(token), "Last-Event-ID": "0"},
    )
    assert stream.status_code == 200, stream.text
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "id: 1\nevent: run.created\n" in stream.text
    assert "id: 2\nevent: run.cancelled\n" in stream.text
    assert '"seq":2' in stream.text
    assert "event: heartbeat" not in stream.text

    token_b, _user_b = await register(client, "agent-api-outsider@example.com")
    hidden = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}",
        headers=auth_headers(token_b),
    )
    assert hidden.status_code == 404
    unauthenticated = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}"
    )
    assert unauthenticated.status_code == 401
    assert UUID(user["id"])

    async with session_factory() as session:
        first_event = await session.scalar(
            select(AgentRunEvent).where(
                AgentRunEvent.workspace_id == workspace_id,
                AgentRunEvent.run_id == UUID(run_id),
                AgentRunEvent.sequence_no == 1,
            )
        )
        assert first_event is not None
        first_event.payload_json = {"payload_expired": True}
        await session.commit()
    expired = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}/events",
        headers={**auth_headers(token), "Last-Event-ID": "0"},
    )
    assert expired.status_code == 410, expired.text
    body = expired.json()
    assert body["error"]["code"] == "event_history_expired"
    assert body["error"]["details"]["snapshot_url"].endswith(f"/agent-runs/{run_id}")


@pytest.mark.asyncio
async def test_approval_decision_reuses_durable_hitl_and_todo_is_exactly_once(
    client: AsyncClient, session_factory
) -> None:
    token, user = await register(client, "agent-api-hitl@example.com")
    actor_id = UUID(user["id"])
    workspace_id = await personal_workspace(client, token)
    conversation, accepted = await create_conversation_and_run(client, token, workspace_id)
    conversation_id = UUID(conversation["id"])
    run_id = UUID(accepted["resource_id"])
    arguments = TodoCreateInput(title="提交 P8 验收报告")
    proposal = ToolProposal(
        tool_call_id=uuid4(),
        arguments=arguments,
        arguments_hash=canonical_request_hash(arguments.model_dump(mode="json")),
    )
    async with session_factory() as session:
        await session.execute(
            update(AgentRun)
            .where(AgentRun.workspace_id == workspace_id, AgentRun.id == run_id)
            .values(route="TODO_CREATE", reason_code="ACTION_REQUEST")
        )
        await session.commit()
    await TodoApprovalResumeWorker(session_factory).start(
        TodoWorkflowInput(
            workspace_id=workspace_id,
            actor_id=actor_id,
            conversation_id=conversation_id,
            run_id=run_id,
            proposal=proposal,
            expires_at=utc_now() + timedelta(minutes=10),
            short_term_messages=[
                AgentMessage(role="user", content="创建 P8 验收待办").model_dump()
            ],
        )
    )
    async with session_factory() as session:
        approval = await AgentRepository(session).get_approval_by_call(
            workspace_id, run_id, proposal.tool_call_id
        )
        assert approval is not None
        approval_id = approval.id

    approval_response = await client.get(
        f"/api/v1/workspaces/{workspace_id}/approvals/{approval_id}",
        headers=auth_headers(token),
    )
    assert approval_response.status_code == 200, approval_response.text
    approval_body = approval_response.json()
    assert approval_body["status"] == "PENDING"
    assert approval_body["tool_args_hash"] == proposal.arguments_hash

    decision_payload = {
        "decision": "APPROVED",
        "version": approval_body["version"],
        "tool_args_hash": proposal.arguments_hash,
    }
    decision_headers = {
        **auth_headers(token),
        "Idempotency-Key": "approval-decision-001",
        "If-Match": f'"{approval_body["version"]}"',
    }
    decided = await client.post(
        f"/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/decision",
        json=decision_payload,
        headers=decision_headers,
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "APPROVED"
    assert decided.json()["todo_id"] is not None
    replay = await client.post(
        f"/api/v1/workspaces/{workspace_id}/approvals/{approval_id}/decision",
        json=decision_payload,
        headers=decision_headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["todo_id"] == decided.json()["todo_id"]

    todos = await client.get(
        f"/api/v1/workspaces/{workspace_id}/todos",
        headers=auth_headers(token),
    )
    assert todos.status_code == 200, todos.text
    assert todos.json()["total"] == 1
    assert todos.json()["items"][0]["title"] == "提交 P8 验收报告"
