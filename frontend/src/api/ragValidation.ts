import { ApiError } from './client'
import type {
  DocumentFormat,
  DocumentStatus,
  DocumentSummary,
  DocumentUploadResult,
  DocumentVersionStatus,
  DocumentVersionSummary,
  IngestionJobStatus,
  IngestionJobSummary,
  KnowledgeHit,
  KnowledgeSearchResult,
  Page,
  SearchProviderSummary,
  SearchResponseMode,
} from './types'

type JsonObject = Record<string, unknown>

function invalid(field: string): never {
  throw new ApiError('服务器返回了无法识别的知识库数据，请稍后重试', {
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

function nullableNumber(value: unknown, field: string): number | null {
  if (value === null || value === undefined) return null
  return number(value, field)
}

function boolean(value: unknown, field: string): boolean {
  if (typeof value !== 'boolean') invalid(field)
  return value
}

function enumeration<T extends string>(value: unknown, values: readonly T[], field: string): T {
  if (typeof value !== 'string' || !values.includes(value as T)) invalid(field)
  return value as T
}

function strings(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) invalid(field)
  return value.map((entry, index) => string(entry, `${field}.${index}`))
}

const documentStatuses = ['ACTIVE', 'DELETED'] as const satisfies readonly DocumentStatus[]
const documentFormats = ['PDF', 'DOCX', 'MARKDOWN', 'TXT'] as const satisfies readonly DocumentFormat[]
const versionStatuses = ['PUBLISHING', 'PUBLISHED', 'SUPERSEDED', 'FAILED'] as const satisfies readonly DocumentVersionStatus[]
const jobStatuses = [
  'QUEUED',
  'PARSING',
  'CHUNKING',
  'STORING',
  'INDEXING',
  'SUCCEEDED',
  'RETRY_WAIT',
  'COMPENSATING',
  'FAILED',
  'CANCELLED',
] as const satisfies readonly IngestionJobStatus[]
const responseModes = ['HITS', 'ANSWER'] as const satisfies readonly SearchResponseMode[]

export function parseDocument(value: unknown): DocumentSummary {
  const item = object(value, 'document')
  return {
    id: string(item.id, 'document.id'),
    workspace_id: string(item.workspace_id, 'document.workspace_id'),
    title: string(item.title, 'document.title'),
    status: enumeration(item.status, documentStatuses, 'document.status'),
    version: number(item.version, 'document.version'),
    published_version_id: nullableString(item.published_version_id, 'document.published_version_id'),
    created_at: string(item.created_at, 'document.created_at'),
    updated_at: string(item.updated_at, 'document.updated_at'),
  }
}

export function parseDocumentVersion(value: unknown): DocumentVersionSummary {
  const item = object(value, 'version')
  return {
    id: string(item.id, 'version.id'),
    document_id: string(item.document_id, 'version.document_id'),
    format: enumeration(item.format, documentFormats, 'version.format'),
    mime_type: string(item.mime_type, 'version.mime_type'),
    file_name: string(item.file_name, 'version.file_name'),
    file_size: number(item.file_size, 'version.file_size'),
    file_sha256: string(item.file_sha256, 'version.file_sha256'),
    status: enumeration(item.status, versionStatuses, 'version.status'),
    parser_profile: string(item.parser_profile, 'version.parser_profile'),
    chunk_profile: string(item.chunk_profile, 'version.chunk_profile'),
    index_profile: string(item.index_profile, 'version.index_profile'),
    created_at: string(item.created_at, 'version.created_at'),
    published_at: nullableString(item.published_at, 'version.published_at'),
  }
}

export function parseJob(value: unknown): IngestionJobSummary {
  const item = object(value, 'job')
  const errorCode = nullableString(item.error_code, 'job.error_code')
  const errorMessage = nullableString(item.error_message, 'job.error_message')
  return {
    id: string(item.id, 'job.id'),
    workspace_id: string(item.workspace_id, 'job.workspace_id'),
    document_id: string(item.document_id, 'job.document_id'),
    document_version_id: string(item.document_version_id, 'job.document_version_id'),
    status: enumeration(item.status, jobStatuses, 'job.status'),
    stage: string(item.stage, 'job.stage'),
    progress: number(item.progress, 'job.progress'),
    attempt_count: number(item.attempt_count, 'job.attempt_count'),
    retryable: boolean(item.retryable, 'job.retryable'),
    ...(errorCode === null ? {} : { error_code: errorCode }),
    ...(errorMessage === null ? {} : { error_message: errorMessage }),
    version: number(item.version, 'job.version'),
    created_at: string(item.created_at, 'job.created_at'),
    updated_at: string(item.updated_at, 'job.updated_at'),
  }
}

export function parseDocumentPage(value: unknown): Page<DocumentSummary> {
  const page = object(value, 'documents')
  if (!Array.isArray(page.items)) invalid('documents.items')
  return {
    items: page.items.map(parseDocument),
    page: number(page.page, 'documents.page'),
    page_size: number(page.page_size, 'documents.page_size'),
    total: number(page.total, 'documents.total'),
  }
}

export function parseVersions(value: unknown): DocumentVersionSummary[] {
  if (!Array.isArray(value)) invalid('versions')
  return value.map(parseDocumentVersion)
}

export function parseUploadResult(value: unknown): DocumentUploadResult {
  const result = object(value, 'upload')
  return {
    document: parseDocument(result.document),
    version: parseDocumentVersion(result.version),
    job: parseJob(result.job),
    reused: boolean(result.reused, 'upload.reused'),
  }
}

function parseProvider(value: unknown): SearchProviderSummary {
  const provider = object(value, 'search.provider')
  return {
    mode: enumeration(provider.mode, ['REMOTE', 'LOCAL', 'FIXTURE', 'DISABLED'] as const, 'search.provider.mode'),
    embedding: nullableString(provider.embedding, 'search.provider.embedding'),
    reranker: nullableString(provider.reranker, 'search.provider.reranker'),
    llm: nullableString(provider.llm, 'search.provider.llm'),
    profile: string(provider.profile, 'search.provider.profile'),
  }
}

function parseHit(value: unknown): KnowledgeHit {
  const hit = object(value, 'search.hit')
  const scoreData = object(hit.scores, 'search.hit.scores')
  const dense = nullableNumber(scoreData.dense, 'search.hit.scores.dense')
  const sparse = nullableNumber(scoreData.sparse, 'search.hit.scores.sparse')
  const rerank = nullableNumber(scoreData.rerank, 'search.hit.scores.rerank')
  return {
    chunk_id: string(hit.chunk_id, 'search.hit.chunk_id'),
    document_id: string(hit.document_id, 'search.hit.document_id'),
    document_version_id: string(hit.document_version_id, 'search.hit.document_version_id'),
    title: string(hit.title, 'search.hit.title'),
    page: nullableNumber(hit.page, 'search.hit.page'),
    heading_path: strings(hit.heading_path, 'search.hit.heading_path'),
    quote: string(hit.quote, 'search.hit.quote'),
    source_span_id: string(hit.source_span_id, 'search.hit.source_span_id'),
    score: number(hit.score, 'search.hit.score'),
    scores: {
      ...(dense === null ? {} : { dense }),
      ...(sparse === null ? {} : { sparse }),
      fusion: number(scoreData.fusion, 'search.hit.scores.fusion'),
      ...(rerank === null ? {} : { rerank }),
    },
    context: string(hit.context, 'search.hit.context'),
  }
}

export function parseSearchResult(value: unknown): KnowledgeSearchResult {
  const result = object(value, 'search')
  if (!Array.isArray(result.hits)) invalid('search.hits')
  if (!Array.isArray(result.components)) invalid('search.components')
  if (!Array.isArray(result.warnings)) invalid('search.warnings')
  const timings = object(result.timings_ms, 'search.timings_ms')
  const timingsMs: Record<string, number> = {}
  Object.entries(timings).forEach(([key, value]) => {
    timingsMs[key] = number(value, `search.timings_ms.${key}`)
  })
  const refusal = result.refusal === null || result.refusal === undefined
    ? null
    : object(result.refusal, 'search.refusal')
  return {
    query: string(result.query, 'search.query'),
    response_mode: enumeration(result.response_mode, responseModes, 'search.response_mode'),
    answer: nullableString(result.answer, 'search.answer'),
    refusal: refusal === null
      ? null
      : {
          code: string(refusal.code, 'search.refusal.code'),
          message: string(refusal.message, 'search.refusal.message'),
        },
    hits: result.hits.map(parseHit),
    provider: parseProvider(result.provider),
    components: strings(result.components, 'search.components'),
    timings_ms: timingsMs,
    warnings: strings(result.warnings, 'search.warnings'),
    degraded: boolean(result.degraded, 'search.degraded'),
  }
}
