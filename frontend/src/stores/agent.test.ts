import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { agentApi } from '@/api/agent'
import { streamAgentEvents } from '@/api/agentSse'
import { ApiError } from '@/api/client'
import type { AgentRunSnapshot, ApprovalSummary, TodoSummary } from '@/api/agentTypes'
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

function todo(): TodoSummary {
  return {
    id: 'todo-1', workspace_id: 'workspace-a', title: '复盘', description: null,
    status: 'OPEN', due_at: null, assignee_id: 'user-1', source_run_id: 'run-1',
    approval_id: 'approval-1', version: 1, completed_at: null,
    created_at: '2026-08-03T00:00:02Z', updated_at: '2026-08-03T00:00:02Z',
  }
}

describe('Agent store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(streamAgentEvents).mockResolvedValue({ lastEventId: null, lastSequence: 0 })
    vi.mocked(agentApi.listTodos).mockResolvedValue({ items: [], page: 1, page_size: 50, total: 0 })
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

  it('Workspace 切换会同时取消旧 SSE、Run 和 Todo 请求', async () => {
    let resolveRun!: (value: AgentRunSnapshot) => void
    let resolveTodos!: (value: { items: []; page: number; page_size: number; total: number }) => void
    vi.mocked(agentApi.getRun)
      .mockResolvedValueOnce(snapshot('PENDING'))
      .mockReturnValueOnce(new Promise((next) => { resolveRun = next }))
    vi.mocked(agentApi.listTodos).mockReturnValueOnce(new Promise((next) => { resolveTodos = next }))
    vi.mocked(streamAgentEvents).mockImplementation((options) => new Promise((resolve) => {
      options.signal.addEventListener('abort', () => {
        resolve({ lastEventId: options.lastEventId, lastSequence: options.lastSequence })
      }, { once: true })
    }))
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    await store.recoverRun('run-1')
    const streamSignal = vi.mocked(streamAgentEvents).mock.calls[0]?.[0].signal
    const refreshing = store.refreshRun('run-1')
    const runSignal = vi.mocked(agentApi.getRun).mock.calls[1]?.[2]
    const loadingTodos = store.loadTodos()
    const todoSignal = vi.mocked(agentApi.listTodos).mock.calls[0]?.[1]

    store.activateWorkspace('workspace-b')
    resolveRun(snapshot('APPROVED'))
    resolveTodos({ items: [], page: 1, page_size: 50, total: 0 })
    await Promise.all([refreshing, loadingTodos])

    expect(streamSignal?.aborted).toBe(true)
    expect(runSignal?.aborted).toBe(true)
    expect(todoSignal?.aborted).toBe(true)
    expect(store.workspaceId).toBe('workspace-b')
    expect(store.snapshot).toBeNull()
    expect(store.todos).toEqual([])
  })

  it('审批提交固定 version/hash，成功后阻止前端重放', async () => {
    const pending = snapshot('PENDING')
    const resolved = snapshot('APPROVED')
    vi.mocked(agentApi.decideApproval).mockResolvedValue({
      approval_id: 'approval-1', status: 'APPROVED', decided_at: '2026-08-03T00:00:02Z',
      run: resolved.run, todo_id: 'todo-1',
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
      run: completed, todo_id: 'todo-1',
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

  it('审批响应丢失后从快照恢复终态与 Todo，并复用安全事实', async () => {
    const pending = snapshot('PENDING')
    const recovered = { ...snapshot('APPROVED'), todo: todo(), last_event_id: '9', last_sequence_no: 9 }
    vi.mocked(agentApi.decideApproval).mockRejectedValue(new ApiError('网络中断', {
      code: 'network_error',
    }))
    vi.mocked(agentApi.getRun).mockResolvedValue(recovered)
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    store.snapshot = pending

    expect(await store.decideApproval('APPROVED')).toBe(true)
    expect(store.snapshot?.approval?.status).toBe('APPROVED')
    expect(store.snapshot?.todo?.id).toBe('todo-1')
    expect(store.errorMessage).toBe('')
    expect(agentApi.getRun).toHaveBeenCalledWith('workspace-a', 'run-1', expect.any(AbortSignal))
  })

  it('审批 403 后刷新 Run，并通知界面刷新成员角色', async () => {
    const pending = snapshot('PENDING')
    vi.mocked(agentApi.decideApproval).mockRejectedValue(new ApiError('权限不足', {
      code: 'permission_denied', status: 403,
    }))
    vi.mocked(agentApi.getRun).mockResolvedValue(pending)
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    store.snapshot = pending

    expect(await store.decideApproval('APPROVED')).toBe(false)
    expect(store.membershipRefreshRevision).toBe(1)
    expect(agentApi.getRun).toHaveBeenCalledOnce()
    expect(store.errorMessage).toBe('权限不足')
  })

  it('Conversation 切换取消审批请求并丢弃迟到响应', async () => {
    let resolve!: (value: {
      approval_id: string
      status: 'APPROVED'
      decided_at: string
      run: AgentRunSnapshot['run']
      todo_id: string
    }) => void
    vi.mocked(agentApi.decideApproval).mockReturnValue(new Promise((next) => { resolve = next }))
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')
    store.snapshot = snapshot('PENDING')

    const deciding = store.decideApproval('APPROVED')
    const signal = vi.mocked(agentApi.decideApproval).mock.calls[0]?.[4]
    store.selectConversation('conversation-2')
    resolve({
      approval_id: 'approval-1', status: 'APPROVED', decided_at: '2026-08-03T00:00:02Z',
      run: snapshot('APPROVED').run, todo_id: 'todo-1',
    })

    expect(await deciding).toBe(false)
    expect(signal?.aborted).toBe(true)
    expect(store.activeConversationId).toBe('conversation-2')
    expect(store.snapshot).toBeNull()
  })

  it('approval.required 触发快照刷新，终态事件停止事件流', async () => {
    vi.useFakeTimers()
    const pending = snapshot('PENDING')
    const terminal = snapshot('APPROVED')
    vi.mocked(agentApi.getRun).mockResolvedValueOnce(pending).mockResolvedValueOnce(terminal)
    vi.mocked(streamAgentEvents).mockImplementation(async (options) => {
      options.onEvent({
        id: '4', run_id: 'run-1', sequence_no: 4, type: 'approval.required', payload: {},
        created_at: '2026-08-03T00:00:01Z',
      })
      const keepStreaming = options.onEvent({
        id: '9', run_id: 'run-1', sequence_no: 9, type: 'run.completed', payload: {},
        created_at: '2026-08-03T00:00:02Z',
      })
      expect(keepStreaming).toBe(false)
      return { lastEventId: '9', lastSequence: 9 }
    })
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')

    await store.recoverRun('run-1')
    await vi.runAllTimersAsync()

    expect(agentApi.getRun).toHaveBeenCalledTimes(2)
    expect(store.events.map((event) => event.type)).toEqual(['approval.required', 'run.completed'])
    vi.useRealTimers()
  })

  it('clean EOF 回读 Run 与成员事实后停流，不直接重连', async () => {
    const pending = snapshot('PENDING')
    vi.mocked(agentApi.getRun).mockResolvedValue(pending)
    vi.mocked(streamAgentEvents).mockImplementation(async (options) => {
      await options.onCleanClose?.({
        lastEventId: pending.last_event_id,
        lastSequence: pending.last_sequence_no,
      })
      return { lastEventId: pending.last_event_id, lastSequence: pending.last_sequence_no }
    })
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')

    await store.recoverRun('run-1')
    await vi.waitFor(() => expect(store.lastErrorCode).toBe('stream_closed'))

    expect(agentApi.getRun).toHaveBeenCalledTimes(2)
    expect(streamAgentEvents).toHaveBeenCalledOnce()
    expect(store.membershipRefreshRevision).toBe(1)
    expect(store.streamState).toBe('CLOSED')
    expect(store.errorMessage).toContain('手动恢复')
  })

  it('撤权导致 clean EOF 时快照 404 触发访问清理且重连次数为零', async () => {
    const pending = snapshot('PENDING')
    vi.mocked(agentApi.getRun)
      .mockResolvedValueOnce(pending)
      .mockRejectedValueOnce(new ApiError('资源不存在', {
        code: 'resource_not_found', status: 404,
      }))
    vi.mocked(streamAgentEvents).mockImplementation(async (options) => {
      await options.onCleanClose?.({
        lastEventId: pending.last_event_id,
        lastSequence: pending.last_sequence_no,
      })
      return { lastEventId: pending.last_event_id, lastSequence: pending.last_sequence_no }
    })
    const revoked = vi.fn()
    window.addEventListener('workspace:access-revoked', revoked)
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')

    await store.recoverRun('run-1')
    await vi.waitFor(() => expect(revoked).toHaveBeenCalledOnce())

    expect(agentApi.getRun).toHaveBeenCalledTimes(2)
    expect(streamAgentEvents).toHaveBeenCalledOnce()
    expect(store.streamState).toBe('CLOSED')
    expect(store.errorMessage).toBe('资源不存在')
    window.removeEventListener('workspace:access-revoked', revoked)
  })

  it('authorization_revoked 控制帧立即触发 Workspace 撤权清理', async () => {
    const pending = snapshot('PENDING')
    vi.mocked(agentApi.getRun).mockResolvedValueOnce(pending)
    vi.mocked(streamAgentEvents).mockImplementation(async (options) => {
      await options.onClosed?.({ runId: 'run-1', reason: 'authorization_revoked' })
      return { lastEventId: pending.last_event_id, lastSequence: pending.last_sequence_no }
    })
    const revoked = vi.fn()
    window.addEventListener('workspace:access-revoked', revoked)
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')

    await store.recoverRun('run-1')
    await vi.waitFor(() => expect(revoked).toHaveBeenCalledOnce())

    expect(agentApi.getRun).toHaveBeenCalledOnce()
    expect(streamAgentEvents).toHaveBeenCalledOnce()
    window.removeEventListener('workspace:access-revoked', revoked)
  })

  it('resource_unavailable 控制帧停止自动重连并提示手动恢复', async () => {
    const pending = snapshot('PENDING')
    vi.mocked(agentApi.getRun).mockResolvedValueOnce(pending)
    vi.mocked(streamAgentEvents).mockImplementation(async (options) => {
      await options.onClosed?.({ runId: 'run-1', reason: 'resource_unavailable' })
      return { lastEventId: pending.last_event_id, lastSequence: pending.last_sequence_no }
    })
    const store = useAgentStore()
    store.activateWorkspace('workspace-a')

    await store.recoverRun('run-1')
    await vi.waitFor(() => expect(store.lastErrorCode).toBe('stream_resource_unavailable'))

    expect(agentApi.getRun).toHaveBeenCalledOnce()
    expect(streamAgentEvents).toHaveBeenCalledOnce()
    expect(store.streamState).toBe('CLOSED')
    expect(store.errorMessage).toContain('停止自动重连')
  })
})
