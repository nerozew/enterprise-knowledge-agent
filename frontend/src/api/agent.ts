import { apiClient } from './client'
import {
  parseAgentRunSnapshot,
  parseApproval,
  parseApprovalDecisionResult,
  parseConversation,
  parseConversationList,
  parseOperationAccepted,
  parseTodo,
  parseTodoList,
} from './agentValidation'
import type {
  AgentRunCreate,
  AgentRunSnapshot,
  ApprovalDecision,
  ApprovalDecisionResult,
  ApprovalSummary,
  ConversationCreate,
  ConversationList,
  ConversationSummary,
  OperationAccepted,
  TodoList,
  TodoSummary,
} from './agentTypes'

export const agentApi = {
  async listConversations(
    workspaceId: string,
    signal?: AbortSignal,
  ): Promise<ConversationList> {
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/conversations`,
      { params: { page: 1, page_size: 50 }, ...(signal ? { signal } : {}) },
    )
    return parseConversationList(data)
  },

  async createConversation(
    workspaceId: string,
    input: ConversationCreate,
    idempotencyKey: string,
  ): Promise<ConversationSummary> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/conversations`,
      input,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return parseConversation(data)
  },

  async getConversation(
    workspaceId: string,
    conversationId: string,
    signal?: AbortSignal,
  ): Promise<ConversationSummary> {
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/conversations/${conversationId}`,
      signal ? { signal } : {},
    )
    return parseConversation(data)
  },

  async createRun(
    workspaceId: string,
    conversationId: string,
    input: AgentRunCreate,
    idempotencyKey: string,
  ): Promise<OperationAccepted> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/conversations/${conversationId}/runs`,
      input,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return parseOperationAccepted(data)
  },

  async getRun(
    workspaceId: string,
    runId: string,
    signal?: AbortSignal,
  ): Promise<AgentRunSnapshot> {
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/agent-runs/${runId}`,
      signal ? { signal } : {},
    )
    return parseAgentRunSnapshot(data)
  },

  async cancelRun(
    workspaceId: string,
    runId: string,
    version: number,
    idempotencyKey: string,
  ): Promise<AgentRunSnapshot> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/agent-runs/${runId}/cancel`,
      { version },
      {
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': `"${version}"`,
        },
      },
    )
    return parseAgentRunSnapshot(data)
  },

  async getApproval(
    workspaceId: string,
    approvalId: string,
    signal?: AbortSignal,
  ): Promise<ApprovalSummary> {
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/approvals/${approvalId}`,
      signal ? { signal } : {},
    )
    return parseApproval(data)
  },

  async decideApproval(
    workspaceId: string,
    approvalId: string,
    input: ApprovalDecision,
    idempotencyKey: string,
  ): Promise<ApprovalDecisionResult> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/approvals/${approvalId}/decision`,
      input,
      {
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': `"${input.version}"`,
        },
      },
    )
    return parseApprovalDecisionResult(data)
  },

  async listTodos(workspaceId: string, signal?: AbortSignal): Promise<TodoList> {
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/todos`,
      { params: { page: 1, page_size: 50 }, ...(signal ? { signal } : {}) },
    )
    return parseTodoList(data)
  },

  async getTodo(workspaceId: string, todoId: string, signal?: AbortSignal): Promise<TodoSummary> {
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/todos/${todoId}`,
      signal ? { signal } : {},
    )
    return parseTodo(data)
  },
}

export function agentEventsPath(workspaceId: string, runId: string): string {
  return `/workspaces/${workspaceId}/agent-runs/${runId}/events`
}

