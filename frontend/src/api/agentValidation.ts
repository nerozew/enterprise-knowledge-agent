import { ApiError } from './client'
import type {
  AgentCitation,
  AgentEvent,
  AgentEventType,
  AgentMessage,
  AgentMessageRole,
  AgentReasonCode,
  AgentRefusal,
  AgentRoute,
  AgentRunSnapshot,
  AgentRunStatus,
  AgentRunSummary,
  ApprovalDecisionResult,
  ApprovalStatus,
  ApprovalSummary,
  ConversationList,
  ConversationStatus,
  ConversationSummary,
  InspirationAgentHit,
  OperationAccepted,
  TodoList,
  TodoStatus,
  TodoSummary,
  ToolProposal,
} from './agentTypes'

type JsonObject = Record<string, unknown>

function invalid(field: string): never {
  throw new ApiError('服务器返回了无法识别的 Agent 数据，请稍后重试', {
    code: 'invalid_response',
    details: [{ field }],
  })
}

function object(value: unknown, field: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid(field)
  return value as JsonObject
}

function string(value: unknown, field: string): string {
  if (typeof value !== 'string') invalid(field)
  return value
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null
  return string(value, field)
}

function number(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) invalid(field)
  return value
}

function boolean(value: unknown, field: string): boolean {
  if (typeof value !== 'boolean') invalid(field)
  return value
}

function trueBoolean(value: unknown, field: string): true {
  if (boolean(value, field) !== true) invalid(field)
  return true
}

function integer(value: unknown, field: string, minimum = 0): number {
  const parsed = number(value, field)
  if (!Number.isInteger(parsed) || parsed < minimum) invalid(field)
  return parsed
}

function enumeration<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) invalid(field)
  return value as T
}

function strings(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) invalid(field)
  return value.map((entry, index) => string(entry, `${field}.${index}`))
}

function record(value: unknown, field: string): Readonly<Record<string, unknown>> {
  return Object.freeze({ ...object(value, field) })
}

const conversationStatuses = ['ACTIVE', 'ARCHIVED'] as const satisfies readonly ConversationStatus[]
const runStatuses = [
  'CREATED',
  'RUNNING',
  'WAITING_APPROVAL',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
] as const satisfies readonly AgentRunStatus[]
const routes = [
  'DIRECT_ANSWER',
  'KNOWLEDGE_QA',
  'INSPIRATION_SEARCH',
  'TODO_CREATE',
] as const satisfies readonly AgentRoute[]
const reasonCodes = [
  'CASUAL_OR_GENERAL',
  'WORKSPACE_KNOWLEDGE',
  'INSPIRATION_LOOKUP',
  'ACTION_REQUEST',
  'AMBIGUOUS',
] as const satisfies readonly AgentReasonCode[]
const messageRoles = ['USER', 'ASSISTANT', 'SYSTEM'] as const satisfies readonly AgentMessageRole[]
const approvalStatuses = ['PENDING', 'APPROVED', 'REJECTED', 'EXPIRED'] as const satisfies readonly ApprovalStatus[]
const todoStatuses = ['OPEN', 'COMPLETED', 'CANCELLED'] as const satisfies readonly TodoStatus[]
const eventTypes = [
  'run.created',
  'route.selected',
  'retrieval.started',
  'retrieval.completed',
  'tool.proposed',
  'tool.started',
  'tool.completed',
  'tool.failed',
  'approval.required',
  'approval.resolved',
  'todo.created',
  'run.completed',
  'run.failed',
  'run.cancelled',
] as const satisfies readonly AgentEventType[]

export function parseConversation(value: unknown): ConversationSummary {
  const item = object(value, 'conversation')
  return {
    id: string(item.id, 'conversation.id'),
    workspace_id: string(item.workspace_id, 'conversation.workspace_id'),
    title: nullableString(item.title, 'conversation.title'),
    status: enumeration(item.status, conversationStatuses, 'conversation.status'),
    version: integer(item.version, 'conversation.version', 1),
    last_message_at: nullableString(item.last_message_at, 'conversation.last_message_at'),
    created_at: string(item.created_at, 'conversation.created_at'),
    updated_at: string(item.updated_at, 'conversation.updated_at'),
  }
}

export function parseConversationList(value: unknown): ConversationList {
  const page = object(value, 'conversations')
  if (!Array.isArray(page.items)) invalid('conversations.items')
  return {
    items: page.items.map(parseConversation),
    page: integer(page.page, 'conversations.page', 1),
    page_size: integer(page.page_size, 'conversations.page_size', 1),
    total: integer(page.total, 'conversations.total'),
  }
}

export function parseOperationAccepted(value: unknown): OperationAccepted {
  const item = object(value, 'operation')
  return {
    operation_id: string(item.operation_id, 'operation.operation_id'),
    resource_type: enumeration(item.resource_type, ['agent_run'] as const, 'operation.resource_type'),
    resource_id: string(item.resource_id, 'operation.resource_id'),
    status: enumeration(item.status, ['RUNNING'] as const, 'operation.status'),
    status_url: string(item.status_url, 'operation.status_url'),
    events_url: string(item.events_url, 'operation.events_url'),
    retry_after_seconds: integer(item.retry_after_seconds, 'operation.retry_after_seconds'),
    created_at: string(item.created_at, 'operation.created_at'),
  }
}

export function parseAgentRun(value: unknown): AgentRunSummary {
  const item = object(value, 'run')
  return {
    id: string(item.id, 'run.id'),
    workspace_id: string(item.workspace_id, 'run.workspace_id'),
    conversation_id: string(item.conversation_id, 'run.conversation_id'),
    requested_by_user_id: string(item.requested_by_user_id, 'run.requested_by_user_id'),
    status: enumeration(item.status, runStatuses, 'run.status'),
    route: item.route === null || item.route === undefined
      ? null
      : enumeration(item.route, routes, 'run.route'),
    reason_code: item.reason_code === null || item.reason_code === undefined
      ? null
      : enumeration(item.reason_code, reasonCodes, 'run.reason_code'),
    graph_version: string(item.graph_version, 'run.graph_version'),
    error_code: nullableString(item.error_code, 'run.error_code'),
    version: integer(item.version, 'run.version', 1),
    started_at: nullableString(item.started_at, 'run.started_at'),
    finished_at: nullableString(item.finished_at, 'run.finished_at'),
    created_at: string(item.created_at, 'run.created_at'),
    updated_at: string(item.updated_at, 'run.updated_at'),
  }
}

function parseMessage(value: unknown, index: number): AgentMessage {
  const field = `snapshot.messages.${index}`
  const item = object(value, field)
  return {
    id: string(item.id, `${field}.id`),
    workspace_id: string(item.workspace_id, `${field}.workspace_id`),
    conversation_id: string(item.conversation_id, `${field}.conversation_id`),
    run_id: nullableString(item.run_id, `${field}.run_id`),
    role: enumeration(item.role, messageRoles, `${field}.role`),
    content: string(item.content, `${field}.content`),
    sequence_no: integer(item.sequence_no, `${field}.sequence_no`, 1),
    created_at: string(item.created_at, `${field}.created_at`),
  }
}

function parseRefusal(value: unknown): AgentRefusal | null {
  if (value === null || value === undefined) return null
  const item = object(value, 'snapshot.refusal')
  return {
    code: string(item.code, 'snapshot.refusal.code'),
    message: string(item.message, 'snapshot.refusal.message'),
  }
}

function parseCitation(value: unknown, index: number): AgentCitation {
  const field = `snapshot.citations.${index}`
  const item = object(value, field)
  return {
    id: string(item.id, `${field}.id`),
    document_id: string(item.document_id, `${field}.document_id`),
    document_version_id: string(item.document_version_id, `${field}.document_version_id`),
    document_title: string(item.document_title ?? item.title, `${field}.document_title`),
    page: item.page === null || item.page === undefined ? null : integer(item.page, `${field}.page`, 1),
    heading_path: item.heading_path === undefined ? [] : strings(item.heading_path, `${field}.heading_path`),
    quote: string(item.quote, `${field}.quote`),
    source_span_id: string(item.source_span_id, `${field}.source_span_id`),
    ordinal: integer(item.ordinal, `${field}.ordinal`, 1),
  }
}

function parseInspirationHit(value: unknown, index: number): InspirationAgentHit {
  const field = `snapshot.inspiration_hits.${index}`
  const item = object(value, field)
  return {
    id: string(item.id, `${field}.id`),
    title: nullableString(item.title, `${field}.title`),
    content: string(item.content, `${field}.content`),
    summary: nullableString(item.summary, `${field}.summary`),
    tag_names: item.tag_names === undefined ? [] : strings(item.tag_names, `${field}.tag_names`),
    score: item.score === null || item.score === undefined ? null : number(item.score, `${field}.score`),
  }
}

export function parseToolProposal(value: unknown): ToolProposal {
  const item = object(value, 'tool_proposal')
  const hash = string(item.tool_args_hash ?? item.arguments_hash, 'tool_proposal.tool_args_hash')
  if (!/^[0-9a-f]{64}$/.test(hash)) invalid('tool_proposal.tool_args_hash')
  return {
    tool_call_id: string(item.tool_call_id, 'tool_proposal.tool_call_id'),
    tool_name: enumeration(item.tool_name, ['todo_create'] as const, 'tool_proposal.tool_name'),
    arguments: record(item.arguments, 'tool_proposal.arguments'),
    tool_args_hash: hash,
    impact_scope: item.impact_scope === undefined
      ? '仅在当前 Workspace 创建 1 条待办'
      : string(item.impact_scope, 'tool_proposal.impact_scope'),
    requires_approval: trueBoolean(item.requires_approval, 'tool_proposal.requires_approval'),
  }
}

export function parseApproval(value: unknown): ApprovalSummary {
  const item = object(value, 'approval')
  const hash = string(item.tool_args_hash ?? item.arguments_hash, 'approval.tool_args_hash')
  if (!/^[0-9a-f]{64}$/.test(hash)) invalid('approval.tool_args_hash')
  return {
    id: string(item.id, 'approval.id'),
    workspace_id: string(item.workspace_id, 'approval.workspace_id'),
    run_id: string(item.run_id, 'approval.run_id'),
    tool_call_id: string(item.tool_call_id, 'approval.tool_call_id'),
    requested_by_user_id: string(item.requested_by_user_id, 'approval.requested_by_user_id'),
    tool_name: enumeration(item.tool_name, ['todo_create'] as const, 'approval.tool_name'),
    arguments: record(item.arguments, 'approval.arguments'),
    tool_args_hash: hash,
    status: enumeration(item.status, approvalStatuses, 'approval.status'),
    version: integer(item.version, 'approval.version', 1),
    expires_at: string(item.expires_at, 'approval.expires_at'),
    resolved_at: nullableString(item.resolved_at, 'approval.resolved_at'),
    created_at: string(item.created_at, 'approval.created_at'),
    updated_at: string(item.updated_at, 'approval.updated_at'),
  }
}

export function parseTodo(value: unknown): TodoSummary {
  const item = object(value, 'todo')
  return {
    id: string(item.id, 'todo.id'),
    workspace_id: string(item.workspace_id, 'todo.workspace_id'),
    title: string(item.title, 'todo.title'),
    description: nullableString(item.description, 'todo.description'),
    status: enumeration(item.status, todoStatuses, 'todo.status'),
    due_at: nullableString(item.due_at, 'todo.due_at'),
    assignee_id: string(item.assignee_id, 'todo.assignee_id'),
    source_run_id: string(item.source_run_id, 'todo.source_run_id'),
    approval_id: string(item.approval_id, 'todo.approval_id'),
    version: integer(item.version, 'todo.version', 1),
    completed_at: nullableString(item.completed_at, 'todo.completed_at'),
    created_at: string(item.created_at, 'todo.created_at'),
    updated_at: string(item.updated_at, 'todo.updated_at'),
  }
}

export function parseTodoList(value: unknown): TodoList {
  const page = object(value, 'todos')
  if (!Array.isArray(page.items)) invalid('todos.items')
  return {
    items: page.items.map(parseTodo),
    page: integer(page.page, 'todos.page', 1),
    page_size: integer(page.page_size, 'todos.page_size', 1),
    total: integer(page.total, 'todos.total'),
  }
}

export function parseApprovalDecisionResult(value: unknown): ApprovalDecisionResult {
  const item = object(value, 'approval_decision')
  return {
    approval_id: string(item.approval_id, 'approval_decision.approval_id'),
    status: enumeration(item.status, ['APPROVED', 'REJECTED'] as const, 'approval_decision.status'),
    decided_at: string(item.decided_at, 'approval_decision.decided_at'),
    run: parseAgentRun(item.run),
    todo_id: nullableString(item.todo_id, 'approval_decision.todo_id'),
  }
}

function arrayOrEmpty(value: unknown, field: string): unknown[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) invalid(field)
  return value
}

export function parseAgentRunSnapshot(value: unknown): AgentRunSnapshot {
  const item = object(value, 'snapshot')
  const runValue = item.run ?? value
  const messages = arrayOrEmpty(item.messages, 'snapshot.messages')
  const citations = arrayOrEmpty(item.citations, 'snapshot.citations')
  const inspirationHits = arrayOrEmpty(item.inspiration_hits, 'snapshot.inspiration_hits')
  const run = parseAgentRun(runValue)
  const parsedMessages = messages.map(parseMessage)
  const toolProposal = item.tool_proposal === null || item.tool_proposal === undefined
    ? null
    : parseToolProposal(item.tool_proposal)
  const approval = item.approval === null || item.approval === undefined
    ? null
    : parseApproval(item.approval)
  const todo = item.todo === null || item.todo === undefined ? null : parseTodo(item.todo)
  const lastEventId = nullableString(item.last_event_id, 'snapshot.last_event_id')
  const lastSequenceNo = item.last_sequence_no === undefined
    ? 0
    : integer(item.last_sequence_no, 'snapshot.last_sequence_no')
  if (parsedMessages.some(
    (message) => message.workspace_id !== run.workspace_id
      || message.conversation_id !== run.conversation_id
      || (message.run_id !== null && message.run_id !== run.id),
  )) invalid('snapshot.messages.scope')
  if (approval && (approval.workspace_id !== run.workspace_id || approval.run_id !== run.id)) {
    invalid('snapshot.approval.scope')
  }
  if (
    approval
    && toolProposal
    && (
      approval.tool_call_id !== toolProposal.tool_call_id
      || approval.tool_args_hash !== toolProposal.tool_args_hash
      || approval.tool_name !== toolProposal.tool_name
    )
  ) invalid('snapshot.approval.proposal_mismatch')
  if (
    todo
    && (
      todo.workspace_id !== run.workspace_id
      || todo.source_run_id !== run.id
      || (approval !== null && todo.approval_id !== approval.id)
    )
  ) invalid('snapshot.todo.scope')
  if (
    (lastEventId === null && lastSequenceNo !== 0)
    || (lastEventId !== null && (!/^\d+$/.test(lastEventId) || Number(lastEventId) !== lastSequenceNo))
  ) {
    invalid('snapshot.last_event_id')
  }
  return {
    run,
    messages: parsedMessages,
    final_answer: nullableString(item.final_answer, 'snapshot.final_answer'),
    refusal: parseRefusal(item.refusal),
    citations: citations.map(parseCitation),
    inspiration_hits: inspirationHits.map(parseInspirationHit),
    tool_proposal: toolProposal,
    approval,
    todo,
    last_event_id: lastEventId,
    last_sequence_no: lastSequenceNo,
  }
}

export function parseAgentEvent(
  value: unknown,
  fallback: { id?: string; type?: string } = {},
): AgentEvent {
  const item = object(value, 'event')
  const eventType = item.type ?? item.event_type ?? fallback.type
  const eventId = item.id ?? item.event_id ?? fallback.id
  const id = string(eventId, 'event.id')
  const sequenceNo = integer(item.sequence_no ?? item.seq, 'event.sequence_no', 1)
  if (!/^\d+$/.test(id) || Number(id) !== sequenceNo) invalid('event.id')
  return {
    id,
    run_id: string(item.run_id, 'event.run_id'),
    sequence_no: sequenceNo,
    type: enumeration(eventType, eventTypes, 'event.type'),
    payload: item.payload === undefined ? Object.freeze({}) : record(item.payload, 'event.payload'),
    created_at: string(item.created_at ?? item.timestamp, 'event.created_at'),
  }
}
