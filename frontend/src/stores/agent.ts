import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { agentApi, agentEventsPath } from '@/api/agent'
import { streamAgentEvents, type AgentStreamState } from '@/api/agentSse'
import { executeIdempotentIntent } from '@/api/idempotency'
import { mapApiError } from '@/api/client'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'
import { terminalRunStatuses } from '@/api/agentTypes'
import type {
  AgentEvent,
  AgentRunSnapshot,
  ApprovalDecision,
  ConversationSummary,
  TodoSummary,
} from '@/api/agentTypes'

const MAX_VISIBLE_EVENTS = 100

export const useAgentStore = defineStore('agent', () => {
  const workspaceId = ref<string | null>(null)
  const conversations = ref<ConversationSummary[]>([])
  const activeConversationId = ref<string | null>(null)
  const activeRunId = ref<string | null>(null)
  const snapshot = ref<AgentRunSnapshot | null>(null)
  const events = ref<AgentEvent[]>([])
  const todos = ref<TodoSummary[]>([])
  const loadingConversations = ref(false)
  const loadingRun = ref(false)
  const loadingTodos = ref(false)
  const sending = ref(false)
  const decidingApproval = ref(false)
  const cancelling = ref(false)
  const errorMessage = ref('')
  const lastErrorCode = ref<string | null>(null)
  const todoErrorMessage = ref('')
  const streamState = ref<AgentStreamState>('CLOSED')
  const retryInMs = ref<number | null>(null)
  const membershipRefreshRevision = ref(0)

  let generation = 0
  let conversationsController: AbortController | null = null
  let runController: AbortController | null = null
  let todosController: AbortController | null = null
  let streamController: AbortController | null = null
  let sendController: AbortController | null = null
  let decisionController: AbortController | null = null
  let cancelController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setTimeout> | null = null

  const activeConversation = computed(
    () => conversations.value.find((item) => item.id === activeConversationId.value) ?? null,
  )
  const runIsActive = computed(() => {
    const status = snapshot.value?.run.status
    return Boolean(status && !terminalRunStatuses.has(status))
  })
  const canSend = computed(() => !sending.value && !runIsActive.value)

  function clearRefreshTimer() {
    if (refreshTimer !== null) clearTimeout(refreshTimer)
    refreshTimer = null
  }

  function abortConversationScopeRequests() {
    runController?.abort()
    todosController?.abort()
    streamController?.abort()
    sendController?.abort()
    decisionController?.abort()
    cancelController?.abort()
    runController = null
    todosController = null
    streamController = null
    sendController = null
    decisionController = null
    cancelController = null
    clearRefreshTimer()
  }

  function abortRequests() {
    conversationsController?.abort()
    conversationsController = null
    abortConversationScopeRequests()
  }

  function resetState() {
    conversations.value = []
    activeConversationId.value = null
    activeRunId.value = null
    snapshot.value = null
    events.value = []
    todos.value = []
    loadingConversations.value = false
    loadingRun.value = false
    loadingTodos.value = false
    sending.value = false
    decidingApproval.value = false
    cancelling.value = false
    errorMessage.value = ''
    lastErrorCode.value = null
    todoErrorMessage.value = ''
    streamState.value = 'CLOSED'
    retryInMs.value = null
  }

  function activateWorkspace(nextWorkspaceId: string | null) {
    if (workspaceId.value === nextWorkspaceId) return
    generation += 1
    abortRequests()
    workspaceId.value = nextWorkspaceId
    resetState()
  }

  function reportScopedError(error: unknown, targetWorkspaceId: string): string {
    const mapped = mapApiError(error)
    lastErrorCode.value = mapped.code
    if (
      mapped.status === 404
      && mapped.code === 'resource_not_found'
      && workspaceId.value === targetWorkspaceId
    ) {
      reportWorkspaceAccessRevoked(targetWorkspaceId)
    }
    return mapped.message
  }

  async function loadConversations(): Promise<void> {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) return
    conversationsController?.abort()
    const controller = new AbortController()
    conversationsController = controller
    const requestGeneration = generation
    loadingConversations.value = true
    errorMessage.value = ''
    try {
      const page = await agentApi.listConversations(targetWorkspaceId, controller.signal)
      if (controller.signal.aborted || generation !== requestGeneration || workspaceId.value !== targetWorkspaceId) return
      conversations.value = page.items
      if (
        activeConversationId.value
        && !page.items.some((item) => item.id === activeConversationId.value)
      ) {
        activeConversationId.value = null
      }
    } catch (error) {
      if (controller.signal.aborted || generation !== requestGeneration) return
      errorMessage.value = reportScopedError(error, targetWorkspaceId)
    } finally {
      if (conversationsController === controller) {
        conversationsController = null
        loadingConversations.value = false
      }
    }
  }

  async function createConversation(
    title?: string,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) throw new Error('Workspace context is not ready')
    const requestGeneration = generation
    const input = title?.trim() ? { title: title.trim() } : {}
    const created = await executeIdempotentIntent(
      `workspace:${targetWorkspaceId}:conversation.create`,
      input,
      (key) => agentApi.createConversation(targetWorkspaceId, input, key, signal),
    )
    if (signal?.aborted || generation !== requestGeneration || workspaceId.value !== targetWorkspaceId) {
      return created
    }
    conversations.value = [created, ...conversations.value.filter((item) => item.id !== created.id)]
    activeConversationId.value = created.id
    return created
  }

  function selectConversation(conversationId: string | null) {
    generation += 1
    abortConversationScopeRequests()
    activeConversationId.value = conversationId
    activeRunId.value = null
    snapshot.value = null
    events.value = []
    loadingRun.value = false
    loadingTodos.value = false
    sending.value = false
    decidingApproval.value = false
    cancelling.value = false
    errorMessage.value = ''
    lastErrorCode.value = null
    streamState.value = 'CLOSED'
    retryInMs.value = null
  }

  function acceptSnapshot(nextSnapshot: AgentRunSnapshot, targetWorkspaceId: string) {
    if (nextSnapshot.run.workspace_id !== targetWorkspaceId) {
      throw new Error('Agent snapshot workspace mismatch')
    }
    activeRunId.value = nextSnapshot.run.id
    activeConversationId.value = nextSnapshot.run.conversation_id
    snapshot.value = nextSnapshot
  }

  async function refreshRun(runId = activeRunId.value, silent = false): Promise<AgentRunSnapshot | null> {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId || !runId) return null
    runController?.abort()
    const controller = new AbortController()
    runController = controller
    const requestGeneration = generation
    if (!silent) loadingRun.value = true
    try {
      const nextSnapshot = await agentApi.getRun(targetWorkspaceId, runId, controller.signal)
      if (controller.signal.aborted || generation !== requestGeneration || workspaceId.value !== targetWorkspaceId) return null
      acceptSnapshot(nextSnapshot, targetWorkspaceId)
      errorMessage.value = ''
      lastErrorCode.value = null
      return nextSnapshot
    } catch (error) {
      if (controller.signal.aborted || generation !== requestGeneration) return null
      if (!silent) errorMessage.value = reportScopedError(error, targetWorkspaceId)
      return null
    } finally {
      if (runController === controller) {
        runController = null
        if (!silent) loadingRun.value = false
      }
    }
  }

  function scheduleRunRefresh(runId: string, immediate = false) {
    clearRefreshTimer()
    refreshTimer = setTimeout(() => {
      refreshTimer = null
      void refreshRun(runId, true)
    }, immediate ? 0 : 60)
  }

  function stopStream() {
    streamController?.abort()
    streamController = null
    streamState.value = 'CLOSED'
    retryInMs.value = null
  }

  function startStream(runId: string) {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) return
    stopStream()
    const controller = new AbortController()
    streamController = controller
    const requestGeneration = generation
    const currentSnapshot = snapshot.value?.run.id === runId ? snapshot.value : null
    void streamAgentEvents({
      path: agentEventsPath(targetWorkspaceId, runId),
      signal: controller.signal,
      lastEventId: currentSnapshot?.last_event_id ?? null,
      lastSequence: currentSnapshot?.last_sequence_no ?? 0,
      onState: (state, nextRetryInMs) => {
        if (generation !== requestGeneration || streamController !== controller) return
        streamState.value = state
        retryInMs.value = nextRetryInMs ?? null
      },
      onEvent: (event) => {
        if (generation !== requestGeneration || workspaceId.value !== targetWorkspaceId) return false
        if (event.run_id !== runId) return true
        if (!events.value.some((item) => item.sequence_no === event.sequence_no)) {
          events.value = [...events.value, event].slice(-MAX_VISIBLE_EVENTS)
        }
        const terminal = ['run.completed', 'run.failed', 'run.cancelled'].includes(event.type)
        scheduleRunRefresh(runId, terminal || event.type === 'approval.required')
        return !terminal
      },
      onHistoryExpired: async () => {
        const recovered = await refreshRun(runId, true)
        if (!recovered) {
          errorMessage.value = '事件历史已过期，且运行快照恢复失败。请点击“恢复运行状态”重试。'
          return { lastEventId: null, lastSequence: 0, terminal: true }
        }
        return {
          lastEventId: recovered.last_event_id,
          lastSequence: recovered.last_sequence_no,
          terminal: terminalRunStatuses.has(recovered.run.status),
        }
      },
      onClosed: (close) => {
        if (
          controller.signal.aborted
          || generation !== requestGeneration
          || workspaceId.value !== targetWorkspaceId
        ) return
        if (close.runId !== runId) {
          errorMessage.value = '事件流关闭帧与当前运行不匹配，请重新加载。'
          lastErrorCode.value = 'invalid_response'
          return
        }
        if (close.reason === 'authorization_revoked') {
          reportWorkspaceAccessRevoked(targetWorkspaceId)
          return
        }
        if (close.reason === 'resource_unavailable') {
          errorMessage.value = '运行事件流暂时不可用，已停止自动重连。请稍后手动恢复。'
          lastErrorCode.value = 'stream_resource_unavailable'
        }
      },
      onCleanClose: async () => {
        const recovered = await refreshRun(runId)
        if (
          controller.signal.aborted
          || generation !== requestGeneration
          || workspaceId.value !== targetWorkspaceId
        ) return
        membershipRefreshRevision.value += 1
        if (recovered && !terminalRunStatuses.has(recovered.run.status)) {
          errorMessage.value = '事件流已由服务端结束，已刷新运行与成员事实。请确认权限后手动恢复。'
          lastErrorCode.value = 'stream_closed'
        }
      },
    }).catch((error: unknown) => {
      if (controller.signal.aborted || generation !== requestGeneration) return
      errorMessage.value = reportScopedError(error, targetWorkspaceId)
    }).finally(() => {
      if (streamController === controller) {
        streamController = null
        streamState.value = 'CLOSED'
        retryInMs.value = null
      }
    })
  }

  async function recoverRun(runId: string): Promise<void> {
    const recovered = await refreshRun(runId)
    if (recovered && !terminalRunStatuses.has(recovered.run.status)) startStream(runId)
  }

  async function sendMessage(message: string): Promise<string | null> {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) throw new Error('Workspace context is not ready')
    const normalizedMessage = message.trim()
    if (!normalizedMessage || sending.value) return null
    sendController?.abort()
    const controller = new AbortController()
    sendController = controller
    const requestGeneration = generation
    sending.value = true
    errorMessage.value = ''
    lastErrorCode.value = null
    try {
      let conversation = activeConversation.value
      if (!conversation) {
        conversation = await createConversation(normalizedMessage.slice(0, 48), controller.signal)
      }
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return null
      const input = {
        conversation_version: conversation.version,
        message: normalizedMessage,
        knowledge_base_ids: [],
        retrieval_strategy: 'BASIC' as const,
        budget: { max_tokens: 2048 },
      }
      const accepted = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:conversation:${conversation.id}:run.create`,
        input,
        (key) => agentApi.createRun(
          targetWorkspaceId,
          conversation!.id,
          input,
          key,
          controller.signal,
        ),
      )
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return null
      activeRunId.value = accepted.resource_id
      snapshot.value = null
      events.value = []
      const recovered = await refreshRun(accepted.resource_id)
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return null
      if (recovered && !terminalRunStatuses.has(recovered.run.status)) startStream(accepted.resource_id)
      void loadConversations()
      return accepted.resource_id
    } catch (error) {
      if (
        !controller.signal.aborted
        && generation === requestGeneration
        && workspaceId.value === targetWorkspaceId
      ) {
        errorMessage.value = reportScopedError(error, targetWorkspaceId)
      }
      return null
    } finally {
      if (sendController === controller) {
        sendController = null
        sending.value = false
      }
    }
  }

  async function decideApproval(decision: ApprovalDecision['decision'], comment?: string): Promise<boolean> {
    const targetWorkspaceId = workspaceId.value
    const approval = snapshot.value?.approval
    if (!targetWorkspaceId || !approval || decidingApproval.value || approval.status !== 'PENDING') return false
    decisionController?.abort()
    const controller = new AbortController()
    decisionController = controller
    const requestGeneration = generation
    decidingApproval.value = true
    errorMessage.value = ''
    lastErrorCode.value = null
    const input: ApprovalDecision = {
      decision,
      version: approval.version,
      tool_args_hash: approval.tool_args_hash,
      ...(comment?.trim() ? { comment: comment.trim() } : {}),
    }
    try {
      const result = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:approval:${approval.id}:decision`,
        input,
        (key) => agentApi.decideApproval(
          targetWorkspaceId,
          approval.id,
          input,
          key,
          controller.signal,
        ),
      )
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return false
      if (result.run.workspace_id !== targetWorkspaceId || result.run.id !== approval.run_id) {
        throw new Error('Approval decision scope mismatch')
      }
      const currentSnapshot = snapshot.value
      if (currentSnapshot?.approval?.id === approval.id) {
        snapshot.value = {
          ...currentSnapshot,
          run: result.run,
          approval: {
            ...approval,
            status: result.status,
            version: approval.version + 1,
            resolved_at: result.decided_at,
            updated_at: result.decided_at,
          },
        }
      }
      await refreshRun(result.run.id)
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return false
      if (!terminalRunStatuses.has(result.run.status)) startStream(result.run.id)
      if (result.todo_id) void loadTodos()
      return true
    } catch (error) {
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return false
      const mapped = mapApiError(error)
      const message = reportScopedError(error, targetWorkspaceId)
      const shouldRecoverFacts = mapped.status === 403
        || mapped.status === 409
        || mapped.status === 429
        || (mapped.status !== undefined && mapped.status >= 500)
        || mapped.code === 'network_error'
        || mapped.code === 'request_timeout'
      if (mapped.status === 403) membershipRefreshRevision.value += 1
      if (shouldRecoverFacts) {
        const recovered = await refreshRun(approval.run_id, true)
        if (
          controller.signal.aborted
          || generation !== requestGeneration
          || workspaceId.value !== targetWorkspaceId
        ) return false
        if (
          recovered?.approval?.id === approval.id
          && recovered.approval.status !== 'PENDING'
          && terminalRunStatuses.has(recovered.run.status)
        ) {
          errorMessage.value = ''
          lastErrorCode.value = null
          if (recovered.todo) void loadTodos()
          return true
        }
      }
      errorMessage.value = message
      lastErrorCode.value = mapped.code
      return false
    } finally {
      if (decisionController === controller) {
        decisionController = null
        decidingApproval.value = false
      }
    }
  }

  async function cancelRun(): Promise<boolean> {
    const targetWorkspaceId = workspaceId.value
    const run = snapshot.value?.run
    if (!targetWorkspaceId || !run || terminalRunStatuses.has(run.status)) return false
    cancelController?.abort()
    const controller = new AbortController()
    cancelController = controller
    const requestGeneration = generation
    cancelling.value = true
    try {
      const cancelled = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:agent-run:${run.id}:cancel`,
        { version: run.version },
        (key) => agentApi.cancelRun(
          targetWorkspaceId,
          run.id,
          run.version,
          key,
          controller.signal,
        ),
      )
      if (
        controller.signal.aborted
        || generation !== requestGeneration
        || workspaceId.value !== targetWorkspaceId
      ) return false
      acceptSnapshot(cancelled, targetWorkspaceId)
      stopStream()
      return true
    } catch (error) {
      if (
        !controller.signal.aborted
        && generation === requestGeneration
        && workspaceId.value === targetWorkspaceId
      ) errorMessage.value = reportScopedError(error, targetWorkspaceId)
      return false
    } finally {
      if (cancelController === controller) {
        cancelController = null
        cancelling.value = false
      }
    }
  }

  async function loadTodos(): Promise<void> {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) return
    todosController?.abort()
    const controller = new AbortController()
    todosController = controller
    const requestGeneration = generation
    loadingTodos.value = true
    todoErrorMessage.value = ''
    try {
      const page = await agentApi.listTodos(targetWorkspaceId, controller.signal)
      if (controller.signal.aborted || generation !== requestGeneration || workspaceId.value !== targetWorkspaceId) return
      todos.value = page.items
    } catch (error) {
      if (controller.signal.aborted || generation !== requestGeneration) return
      todoErrorMessage.value = reportScopedError(error, targetWorkspaceId)
    } finally {
      if (todosController === controller) {
        todosController = null
        loadingTodos.value = false
      }
    }
  }

  return {
    workspaceId,
    conversations,
    activeConversationId,
    activeConversation,
    activeRunId,
    snapshot,
    events,
    todos,
    loadingConversations,
    loadingRun,
    loadingTodos,
    sending,
    decidingApproval,
    cancelling,
    errorMessage,
    lastErrorCode,
    todoErrorMessage,
    streamState,
    retryInMs,
    membershipRefreshRevision,
    runIsActive,
    canSend,
    activateWorkspace,
    loadConversations,
    createConversation,
    selectConversation,
    refreshRun,
    recoverRun,
    sendMessage,
    decideApproval,
    cancelRun,
    loadTodos,
    stopStream,
  }
})
