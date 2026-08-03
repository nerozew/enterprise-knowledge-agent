import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { agentApi } from '@/api/agent'
import { streamAgentEvents } from '@/api/agentSse'
import type { AgentRunSnapshot, ApprovalSummary } from '@/api/agentTypes'
import { useAgentStore } from './agent'

vi.mock('@/api/agent', () => ({
  agentEventsPath: vi.fn(() => '/events'),
  agentApi: {
    listConversations: vi.fn(),
    createConversation: vi.fn(),
    createRun: vi.fn(),
    getRun: vi.fn(),
    cancelRun: vi.fn(),
    decideApproval: vi.fn(),
    listTodos: vi.fn(),
  },
}))
vi.mock('@/api/agentSse', () => ({
  streamAgentEvents: vi.fn(),
}))

function approval(status: ApprovalSummary['status']): ApprovalSummary {
  return {
    id: 'approval-1', workspace_id: 'workspace-a', run_id: 'run-1', tool_call_id: 'tool-1',
    requested_by_user_id: 'user-1', tool_name: 'todo_create', arguments: { title: '复盘' },
    tool_args_hash: 'a'.repeat(64), status, version: status === 'PENDING' ? 1 : 2,
    expires_at: '2099-08-03T00:00:00Z', resolved_at: status === 'PENDING' ? null : '2026-08-03T00:00:02Z',
    created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:02Z',
  }
}

function snapshot(status: ApprovalSummary['status'] = 'PENDING'): AgentRunSnapshot {
  return {
    run: {
      id: 'run-1', workspace_id: 'workspace-a', conversation_id: 'conversation-1',
      requested_by_user_id: 'user-1', status: status === 'PENDING' ? 'WAITING_APPROVAL' : 'COMPLETED',
      route: 'TODO_CREATE', reason_code: 'ACTION_REQUEST', graph_version: 'p8-agent-mvp-v1',
      error_code: null, version: 2, started_at: '2026-08-03T00:00:00Z',
      finished_at: status === 'PENDING' ? null : '2026-08-03T00:00:02Z',
      created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:02Z',
    },
    messages: [], final_answer: null, refusal: null, citations: [], inspiration_hits: [],
    tool_proposal: {
      tool_call_id: 'tool-1', tool_name: 'todo_create', arguments: { title: '复盘' },
      tool_args_hash: 'a'.repeat(64), impact_scope: '创建一条待办', requires_approval: true,
    },
    approval: approval(status), todo: null, last_event_id: '3', last_sequence_no: 3,
  }
}

describe('Agent store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(streamAgentEvents).mockResolvedValue({ lastEventId: null, lastSequence: 0 })
  })

  it('Workspace 切换会取消旧请求、事件流并清空敏感状态', async () => {
    let resolve!: (value: { items: []; page: number; page_size: number; total: number }) => void
    vi.mocked(agentApi.listConversations).mockReturnValue(new Promise((next) => { resolve = next }))
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    const loading = store.loadConversations()
    const signal = vi.mocked(agentApi.listConversations).mock.calls[0]?.[1]
    store.snapshot = snapshot()

    store.activateWorkspace('workspace-b')
    resolve({ items: [], page: 1, page_size: 50, total: 0 })
    await loading

    expect(signal?.aborted).toBe(true)
    expect(store.workspaceId).toBe('workspace-b')
    expect(store.snapshot).toBeNull()
    expect(store.conversations).toEqual([])
    expect(store.events).toEqual([])
  })

  it('审批提交固定 version/hash，成功后阻止前端重放', async () => {
    const pending = snapshot('PENDING')
    const resolved = snapshot('APPROVED')
    vi.mocked(agentApi.decideApproval).mockResolvedValue({
      approval_id: 'approval-1', status: 'APPROVED', decided_at: '2026-08-03T00:00:02Z',
      run: resolved.run, todo_id: null,
    })
    vi.mocked(agentApi.getRun).mockResolvedValue(resolved)
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    store.snapshot = pending

    expect(await store.decideApproval('APPROVED', '确认执行')).toBe(true)
    expect(await store.decideApproval('APPROVED', '重复')).toBe(false)

    expect(agentApi.decideApproval).toHaveBeenCalledOnce()
    expect(vi.mocked(agentApi.decideApproval).mock.calls[0]?.[2]).toEqual({
      decision: 'APPROVED',
      version: 1,
      tool_args_hash: 'a'.repeat(64),
      comment: '确认执行',
    })
    expect(vi.mocked(agentApi.decideApproval).mock.calls[0]?.[3]).toEqual(expect.any(String))
    expect(store.snapshot?.approval?.status).toBe('APPROVED')
  })

  it('审批成功但快照刷新失败时仍立即锁定卡片，阻止重复决定', async () => {
    const pending = snapshot('PENDING')
    const completed = snapshot('APPROVED').run
    vi.mocked(agentApi.decideApproval).mockResolvedValue({
      approval_id: 'approval-1', status: 'APPROVED', decided_at: '2026-08-03T00:00:02Z',
      run: completed, todo_id: null,
    })
    vi.mocked(agentApi.getRun).mockRejectedValue(new Error('refresh failed'))
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    store.snapshot = pending

    expect(await store.decideApproval('APPROVED')).toBe(true)
    expect(store.snapshot?.approval?.status).toBe('APPROVED')
    expect(await store.decideApproval('APPROVED')).toBe(false)
    expect(agentApi.decideApproval).toHaveBeenCalledOnce()
  })
})
