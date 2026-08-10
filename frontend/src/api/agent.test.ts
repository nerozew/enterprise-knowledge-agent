import { describe, expect, it, vi } from 'vitest'
import { apiClient } from './client'
import { agentApi, agentEventsPath } from './agent'

describe('Agent REST 契约', () => {
  it('创建 Run 使用 Workspace 会话路径、冻结 body 和幂等键', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        operation_id: 'operation-1', resource_type: 'agent_run', resource_id: 'run-1',
        status: 'RUNNING', status_url: '/api/v1/workspaces/workspace-1/agent-runs/run-1',
        events_url: '/api/v1/workspaces/workspace-1/agent-runs/run-1/events',
        retry_after_seconds: 1, created_at: '2026-08-03T00:00:00Z',
      },
    })
    const input = {
      conversation_version: 2,
      message: '创建一条复盘待办',
      knowledge_base_ids: [],
      retrieval_strategy: 'BASIC' as const,
      budget: { max_tokens: 2048 },
    }
    const controller = new AbortController()

    await agentApi.createRun(
      'workspace-1',
      'conversation-1',
      input,
      'run-key',
      controller.signal,
    )

    expect(post).toHaveBeenCalledWith(
      '/workspaces/workspace-1/conversations/conversation-1/runs',
      input,
      { headers: { 'Idempotency-Key': 'run-key' }, signal: controller.signal },
    )
    expect(agentEventsPath('workspace-1', 'run-1'))
      .toBe('/workspaces/workspace-1/agent-runs/run-1/events')
  })

  it('审批决定同时发送 version、参数哈希、幂等键和 If-Match', async () => {
    const run = {
      id: 'run-1', workspace_id: 'workspace-1', conversation_id: 'conversation-1',
      requested_by_user_id: 'user-1', status: 'COMPLETED', route: 'TODO_CREATE',
      reason_code: 'ACTION_REQUEST', graph_version: 'p8-agent-mvp-v1', error_code: null,
      version: 4, started_at: '2026-08-03T00:00:00Z', finished_at: '2026-08-03T00:00:02Z',
      created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:02Z',
    }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        approval_id: 'approval-1', status: 'APPROVED', decided_at: '2026-08-03T00:00:02Z',
        run, todo_id: 'todo-1',
      },
    })
    const input = {
      decision: 'APPROVED' as const,
      version: 3,
      tool_args_hash: 'a'.repeat(64),
      comment: '确认执行',
    }
    const controller = new AbortController()

    await agentApi.decideApproval(
      'workspace-1',
      'approval-1',
      input,
      'approval-key',
      controller.signal,
    )

    expect(post).toHaveBeenCalledWith(
      '/workspaces/workspace-1/approvals/approval-1/decision',
      input,
      {
        headers: {
          'Idempotency-Key': 'approval-key',
          'If-Match': '"3"',
        },
        signal: controller.signal,
      },
    )
  })
})
