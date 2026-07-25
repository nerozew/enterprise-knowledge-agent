export interface ApiErrorPayload {
  error: {
    code: string
    message: string
    details: unknown[]
    retryable?: boolean
  }
  request_id: string
  trace_id?: string
}

export type WorkspaceKind = 'PERSONAL' | 'TEAM'
export type WorkspaceStatus = 'ACTIVE'
export type WorkspaceRole = 'OWNER' | 'ADMIN' | 'EDITOR' | 'VIEWER'
export type MemberStatus = 'INVITED' | 'ACTIVE' | 'SUSPENDED' | 'REMOVED'

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface User {
  id: string
  email: string
  display_name: string
  is_active: boolean
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type?: string
  user: User
}

export interface LoginInput {
  email: string
  password: string
}

export interface RegisterInput extends LoginInput {
  display_name: string
}

export interface Tag {
  id: string
  workspace_id: string
  name: string
  color: string | null
  version: number
  created_at?: string
  updated_at?: string
}

export interface ManagedTag extends Tag {
  usage_count: number
}

export interface Inspiration {
  id: string
  workspace_id: string
  title: string | null
  content: string
  summary: string | null
  expanded_content: string | null
  source_url: string | null
  tags: Tag[]
  version: number
  created_at: string
  updated_at: string
}

export interface InspirationList {
  items: Inspiration[]
  total: number
  page: number
  page_size: number
}

export type TagMatch = 'any' | 'all'
export type InspirationSort = 'updated_desc' | 'created_desc'

export interface InspirationQuery {
  q?: string
  tag_ids?: string[]
  tag_match?: TagMatch
  date_from?: string
  date_to?: string
  sort?: InspirationSort
  page?: number
  page_size?: number
}

export interface InspirationCreate {
  title?: string | null
  content: string
  source_url?: string | null
  tag_ids?: string[]
}

export interface InspirationUpdate {
  version: number
  title?: string | null
  content?: string
  source_url?: string | null
  summary?: string | null
  expanded_content?: string | null
  tag_ids?: string[]
}

export interface TagCreate {
  name: string
  color?: string | null
}

export interface TagUpdate {
  version: number
  name?: string
  color?: string | null
}

export type AiOperation = 'summary' | 'expand'

export interface AiGeneration {
  generation_id: string
  workspace_id: string
  operation: AiOperation
  mode: 'provider' | 'fallback'
  result: string
  warning?: string
  created_at: string
}

export interface AiGenerationInput {
  operation: AiOperation
  instruction?: string
}

export interface WorkspaceSummary {
  id: string
  name: string
  kind: WorkspaceKind
  status: WorkspaceStatus
  role: WorkspaceRole
  member_status: MemberStatus
  version: number
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface WorkspaceCreate {
  name: string
}

export interface WorkspaceUpdate {
  name: string
  version: number
}

export interface MemberSummary {
  workspace_id: string
  user_id: string
  email: string
  display_name: string
  role: WorkspaceRole
  status: MemberStatus
  version: number
  joined_at: string | null
  updated_at: string
}

export interface MemberQuery {
  status?: MemberStatus
  role?: WorkspaceRole
  page?: number
  page_size?: number
}

export interface MemberCreate {
  email: string
  role: WorkspaceRole
}

export interface MemberUpdate {
  role?: WorkspaceRole
  status?: Extract<MemberStatus, 'ACTIVE' | 'SUSPENDED'>
  version: number
}

export interface AuditActor {
  user_id: string | null
  display_name: string
  role: WorkspaceRole | null
}

export interface AuditLog {
  id: string
  action: string
  actor: AuditActor
  target_type: string
  target_id: string | null
  result: 'SUCCESS'
  metadata: Record<string, unknown>
  request_id: string
  created_at: string
}

export interface AuditLogQuery {
  action?: string
  actor_user_id?: string
  target_type?: string
  target_id?: string
  date_from?: string
  date_to?: string
  cursor?: string
  page_size?: number
}

export interface AuditLogPage {
  items: AuditLog[]
  next_cursor: string | null
}
