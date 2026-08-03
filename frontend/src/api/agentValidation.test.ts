import { describe, expect, it } from 'vitest'
import { ApiError } from './client'
import { parseAgentEvent, parseAgentRunSnapshot } from './agentValidation'

function run(status = 'WAITING_APPROVAL') {
  return {
    id: 'run-1',
    workspace_id: 'workspace-1',
    conversation_id: 'conversation-1',
    requested_by_user_id: 'user-1',
    status,
    route: 'TODO_CREATE',
    reason_code: 'ACTION_REQUEST',
    graph_version: 'p8-agent-mvp-v1',
    error_code: null,
    version: 2,
    started_at: '2026-08-03T00:00:00Z',
    finished_at: null,
    created_at: '2026-08-03T00:00:00Z',
    updated_at: '2026-08-03T00:00:01Z',
  }
}

describe('Agent wire schema 校验', () => {
  it('只接受带固定哈希、版本和 Workspace 的审批快照', () => {
    const snapshot = parseAgentRunSnapshot({
      run: run(),
      messages: [],
      final_answer: null,
      refusal: null,
      citations: [],
      inspiration_hits: [],
      tool_proposal: {
        tool_call_id: 'tool-1',
        tool_name: 'todo_create',
        arguments: { title: '完成复盘' },
        tool_args_hash: 'a'.repeat(64),
        impact_scope: '当前 Workspace 创建 1 条待办',
        requires_approval: true,
      },
      approval: {
        id: 'approval-1',
        workspace_id: 'workspace-1',
        run_id: 'run-1',
        tool_call_id: 'tool-1',
        requested_by_user_id: 'user-1',
        tool_name: 'todo_create',
        arguments: { title: '完成复盘' },
        tool_args_hash: 'a'.repeat(64),
        status: 'PENDING',
        version: 1,
        expires_at: '2099-08-03T00:00:00Z',
        resolved_at: null,
        created_at: '2026-08-03T00:00:01Z',
        updated_at: '2026-08-03T00:00:01Z',
      },
      todo: null,
      last_event_id: '4',
      last_sequence_no: 4,
    })

    expect(snapshot.approval?.tool_args_hash).toBe('a'.repeat(64))
    expect(snapshot.tool_proposal?.arguments).toEqual({ title: '完成复盘' })
    expect(Object.isFrozen(snapshot.tool_proposal?.arguments)).toBe(true)
  })

  it('拒绝未知状态和不合法参数哈希', () => {
    expect(() => parseAgentRunSnapshot({ run: run('MAGIC') })).toThrow(ApiError)
    expect(() => parseAgentRunSnapshot({
      run: run(),
      tool_proposal: {
        tool_call_id: 'tool-1',
        tool_name: 'todo_create',
        arguments: {},
        tool_args_hash: 'not-a-sha256',
        requires_approval: true,
      },
    })).toThrow(ApiError)
  })

  it('将 SSE 的 seq/timestamp envelope 校验为统一事件', () => {
    expect(parseAgentEvent({
      run_id: 'run-1',
      seq: 7,
      timestamp: '2026-08-03T00:00:07Z',
      payload: { route: 'KNOWLEDGE_QA' },
    }, { id: '7', type: 'route.selected' })).toEqual({
      id: '7',
      run_id: 'run-1',
      sequence_no: 7,
      type: 'route.selected',
      payload: { route: 'KNOWLEDGE_QA' },
      created_at: '2026-08-03T00:00:07Z',
    })
  })
})
