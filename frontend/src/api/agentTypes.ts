export type AgentRoute =
  | 'DIRECT_ANSWER'
  | 'KNOWLEDGE_QA'
  | 'INSPIRATION_SEARCH'
  | 'TODO_CREATE'

export type AgentReasonCode =
  | 'CASUAL_OR_GENERAL'
  | 'WORKSPACE_KNOWLEDGE'
  | 'INSPIRATION_LOOKUP'
  | 'ACTION_REQUEST'
  | 'AMBIGUOUS'

export type ConversationStatus = 'ACTIVE' | 'ARCHIVED'
export type AgentRunStatus =
  | 'CREATED'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
export type AgentMessageRole = 'USER' | 'ASSISTANT' | 'SYSTEM'
export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED'
export type TodoStatus = 'OPEN' | 'COMPLETED' | 'CANCELLED'

export type AgentEventType =
  | 'run.created'
  | 'route.selected'
  | 'retrieval.started'
  | 'retrieval.completed'
  | 'tool.proposed'
  | 'tool.started'
  | 'tool.completed'
  | 'tool.failed'
  | 'approval.required'
  | 'approval.resolved'
  | 'todo.created'
  | 'run.completed'
  | 'run.failed'
  | 'run.cancelled'

export interface ConversationSummary {
  id: string
  workspace_id: string
  title: string | null
  status: ConversationStatus
  version: number
  last_message_at: string | null
  created_at: string
  updated_at: string
}

export interface ConversationList {
  items: ConversationSummary[]
  page: number
  page_size: number
  total: number
}

export interface ConversationCreate {
  title?: string
}

export interface AgentRunCreate {
  conversation_version: number
  message: string
  knowledge_base_ids: string[]
  retrieval_strategy: 'AUTO' | 'BASIC'
  budget: {
    max_tokens: number
    max_cost?: number
  }
}

export interface OperationAccepted {
  operation_id: string
  resource_type: 'agent_run'
  resource_id: string
  status: 'RUNNING'
  status_url: string
  events_url: string
  retry_after_seconds: number
  created_at: string
}

export interface AgentRunSummary {
  id: string
  workspace_id: string
  conversation_id: string
  requested_by_user_id: string
  status: AgentRunStatus
  route: AgentRoute | null
  reason_code: AgentReasonCode | null
  graph_version: string
  error_code: string | null
  version: number
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

export interface AgentMessage {
  id: string
  workspace_id: string
  conversation_id: string
  run_id: string | null
  role: AgentMessageRole
  content: string
  sequence_no: number
  created_at: string
}

export interface AgentRefusal {
  code: string
  message: string
}

export interface AgentCitation {
  id: string
  document_id: string
  document_version_id: string
  document_title: string
  page: number | null
  heading_path: string[]
  quote: string
  source_span_id: string
  ordinal: number
}

export interface InspirationAgentHit {
  id: string
  title: string | null
  content: string
  summary: string | null
  tag_names: string[]
  score: number | null
}

export interface ToolProposal {
  tool_call_id: string
  tool_name: 'todo_create'
  arguments: Readonly<Record<string, unknown>>
  tool_args_hash: string
  impact_scope: string
  requires_approval: true
}

export interface ApprovalSummary {
  id: string
  workspace_id: string
  run_id: string
  tool_call_id: string
  requested_by_user_id: string
  tool_name: 'todo_create'
  arguments: Readonly<Record<string, unknown>>
  tool_args_hash: string
  status: ApprovalStatus
  version: number
  expires_at: string
  resolved_at: string | null
  created_at: string
  updated_at: string
}

export interface ApprovalDecision {
  decision: 'APPROVED' | 'REJECTED'
  version: number
  tool_args_hash: string
  comment?: string
}

interface ApprovalDecisionResultBase {
  approval_id: string
  decided_at: string
  run: AgentRunSummary
}

export type ApprovalDecisionResult = ApprovalDecisionResultBase & ({
  status: 'APPROVED'
  todo_id: string
} | {
  status: 'REJECTED'
  todo_id: null
})

export interface TodoSummary {
  id: string
  workspace_id: string
  title: string
  description: string | null
  status: TodoStatus
  due_at: string | null
  assignee_id: string
  source_run_id: string
  approval_id: string
  version: number
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface TodoList {
  items: TodoSummary[]
  page: number
  page_size: number
  total: number
}

export interface AgentRunSnapshot {
  run: AgentRunSummary
  messages: AgentMessage[]
  final_answer: string | null
  refusal: AgentRefusal | null
  citations: AgentCitation[]
  inspiration_hits: InspirationAgentHit[]
  tool_proposal: ToolProposal | null
  approval: ApprovalSummary | null
  todo: TodoSummary | null
  last_event_id: string | null
  last_sequence_no: number
}

export interface AgentEvent {
  id: string
  run_id: string
  sequence_no: number
  type: AgentEventType
  payload: Readonly<Record<string, unknown>>
  created_at: string
}

export const terminalRunStatuses: ReadonlySet<AgentRunStatus> = new Set([
  'COMPLETED',
  'FAILED',
  'CANCELLED',
])
