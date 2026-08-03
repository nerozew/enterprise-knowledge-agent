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

export type DocumentStatus = 'ACTIVE' | 'DELETED'
export type DocumentFormat = 'PDF' | 'DOCX' | 'MARKDOWN' | 'TXT'
export type DocumentVersionStatus = 'PUBLISHING' | 'PUBLISHED' | 'SUPERSEDED' | 'FAILED'
export type IngestionJobStatus =
  | 'QUEUED'
  | 'PARSING'
  | 'CHUNKING'
  | 'STORING'
  | 'INDEXING'
  | 'SUCCEEDED'
  | 'RETRY_WAIT'
  | 'COMPENSATING'
  | 'FAILED'
  | 'CANCELLED'
export type DuplicatePolicy = 'REUSE_EXISTING' | 'CREATE_NEW'

export interface DocumentSummary {
  id: string
  workspace_id: string
  title: string
  status: DocumentStatus
  version: number
  published_version_id: string | null
  created_at: string
  updated_at: string
}

export interface DocumentVersionSummary {
  id: string
  document_id: string
  format: DocumentFormat
  mime_type: string
  file_name: string
  file_size: number
  file_sha256: string
  status: DocumentVersionStatus
  parser_profile: string
  chunk_profile: string
  index_profile: string
  created_at: string
  published_at: string | null
}

export interface IngestionJobSummary {
  id: string
  workspace_id: string
  document_id: string
  document_version_id: string
  status: IngestionJobStatus
  stage: string
  progress: number
  attempt_count: number
  retryable: boolean
  error_code?: string
  error_message?: string
  version: number
  created_at: string
  updated_at: string
}

export interface DocumentUploadResult {
  document: DocumentSummary
  version: DocumentVersionSummary
  job: IngestionJobSummary
  reused: boolean
}

export interface DocumentQuery {
  q?: string | undefined
  source_format?: DocumentFormat | undefined
  status?: DocumentStatus | undefined
  version_status?: DocumentVersionStatus | undefined
  page?: number
  page_size?: number
}

export interface DocumentUploadInput {
  file: File
  title?: string
  duplicate_policy: DuplicatePolicy
}

export type RetrievalMode = 'DENSE' | 'SPARSE' | 'HYBRID'
export type RetrievalFusion = 'RRF' | 'WEIGHTED'
export type ComponentMode = 'OFF' | 'AUTO' | 'ON'
export type SearchResponseMode = 'HITS' | 'ANSWER'

export interface KnowledgeSearchFilters {
  document_ids: string[]
  source_formats: DocumentFormat[]
  published_after: string | null
  published_before: string | null
}

export interface KnowledgeSearchInput {
  query: string
  strategy: 'BASIC'
  retrieval_mode: RetrievalMode
  top_k: number
  candidate_k: number
  fusion: RetrievalFusion
  rerank: ComponentMode
  rewrite: ComponentMode
  multi_query: boolean
  expand_context: boolean
  response_mode: SearchResponseMode
  include_query_trace?: boolean
  filters: KnowledgeSearchFilters
}

export interface KnowledgeHitScores {
  dense?: number
  sparse?: number
  fusion: number
  rerank?: number
}

export type ScoreAvailability =
  | 'AVAILABLE'
  | 'NOT_APPLICABLE'
  | 'NOT_EXPOSED'
  | 'NOT_RUN'

export interface ScoreContribution {
  query_index: number
  value: number
  rank: number | null
  source: string | null
}

export interface ScoreStage {
  availability: ScoreAvailability
  value: number | null
  rank: number | null
  method: string | null
  rrf_k: number | null
  per_query: ScoreContribution[]
  contributions: ScoreContribution[]
}

export interface KnowledgeScoreDetails {
  dense_similarity: ScoreStage
  bm25: ScoreStage
  channel_fusion: ScoreStage
  query_fusion: ScoreStage
  rerank: ScoreStage
  final: ScoreStage
}

export interface KnowledgeHit {
  chunk_id: string
  document_id: string
  document_version_id: string
  title: string
  page: number | null
  heading_path: string[]
  quote: string
  source_span_id: string
  score: number
  scores: KnowledgeHitScores
  score_details?: KnowledgeScoreDetails
  context: string
}

export interface SearchProviderSummary {
  mode: 'REMOTE' | 'LOCAL' | 'FIXTURE' | 'DISABLED'
  embedding: string | null
  reranker: string | null
  llm: string | null
  profile: string
}

export interface KnowledgeRefusal {
  code: string
  message: string
}

export interface FusionScoringSummary {
  method: string | null
  applied: boolean
  rrf_k: number | null
}

export interface KnowledgeSearchScoring {
  version: 1
  requested_retrieval_mode: RetrievalMode
  effective_retrieval_mode: RetrievalMode
  query_count: number
  channel_fusion: FusionScoringSummary
  query_fusion: FusionScoringSummary
  rerank_applied: boolean
}

export type KnowledgeQueryKind = 'ORIGINAL' | 'REWRITE'

export interface KnowledgeQueryTraceItem {
  index: number
  kind: KnowledgeQueryKind
  text: string
}

export interface KnowledgeQueryTrace {
  exposed: boolean
  items: KnowledgeQueryTraceItem[]
  redaction_reason: string | null
}

export type AnswerabilityStatus = 'SUPPORTED' | 'REJECTED' | 'NOT_EVALUATED'
export type AnswerabilityMethod =
  | 'GROUNDED_ANSWER_LLM'
  | 'HEURISTIC_GATE'
  | 'NOT_RUN'

export interface KnowledgeAnswerability {
  status: AnswerabilityStatus
  method: AnswerabilityMethod
  reason_code: string
  precheck_code: string
  provider_called: boolean
  checked_hit_ids: string[]
  supporting_hit_ids: string[]
  raw_top_score?: number
  gate_score?: number
  score_threshold?: number
  lexical_coverage?: number
  lexical_threshold?: number
  lexical_passed?: boolean
}

export interface KnowledgeSearchResult {
  query: string
  response_mode: SearchResponseMode
  answer: string | null
  refusal: KnowledgeRefusal | null
  hits: KnowledgeHit[]
  provider: SearchProviderSummary
  scoring?: KnowledgeSearchScoring
  query_trace?: KnowledgeQueryTrace
  answerability?: KnowledgeAnswerability
  components: string[]
  timings_ms: Record<string, number>
  warnings: string[]
  degraded: boolean
}
