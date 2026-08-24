import { ApiError } from './client'
import type {
  DocumentFormat,
  DocumentStatus,
  DocumentSummary,
  DocumentUploadResult,
  DocumentVersionStatus,
  DocumentVersionSummary,
  DiagnosticWarning,
  IngestionJobStatus,
  IngestionJobSummary,
  IngestionPageProgress,
  AnswerabilityMethod,
  AnswerabilityStatus,
  KnowledgeAnswerability,
  KnowledgeHit,
  KnowledgeQueryKind,
  KnowledgeQueryTrace,
  KnowledgeScoreDetails,
  KnowledgeSearchScoring,
  KnowledgeSearchResult,
  KnowledgeSourceSpan,
  Page,
  RetrievalMode,
  ScoreAvailability,
  ScoreContribution,
  ScoreStage,
  SearchProviderSummary,
  SearchResponseMode,
  SourceBoundingBox,
  SourceSpanLocator,
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

function nonEmptyString(value: unknown, field: string): string {
  const parsed = string(value, field)
  if (!parsed.trim()) invalid(field)
  return parsed
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null
  return string(value, field)
}

function number(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) invalid(field)
  return value
}

function integer(value: unknown, field: string, minimum: number): number {
  const parsed = number(value, field)
  if (!Number.isInteger(parsed) || parsed < minimum) invalid(field)
  return parsed
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

function optionalInteger(value: unknown, field: string, minimum: number): number | undefined {
  if (value === undefined || value === null) return undefined
  return integer(value, field, minimum)
}

function optionalWarnings(value: unknown, field: string): DiagnosticWarning[] | undefined {
  if (value === undefined || value === null) return undefined
  if (!Array.isArray(value)) invalid(field)
  return value.map((entry, index) => ({ ...object(entry, `${field}.${index}`) }))
}

function boundingBox(value: unknown, field: string): SourceBoundingBox | null {
  if (value === null || value === undefined) return null
  const box = object(value, field)
  const parsed: SourceBoundingBox = {}
  Object.entries(box).forEach(([key, coordinate]) => {
    parsed[key] = number(coordinate, `${field}.${key}`)
  })
  return parsed
}

const documentStatuses = ['ACTIVE', 'DELETED'] as const satisfies readonly DocumentStatus[]
const documentFormats = ['PDF', 'DOCX', 'PPTX', 'MARKDOWN', 'TXT'] as const satisfies readonly DocumentFormat[]
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
const retrievalModes = ['DENSE', 'SPARSE', 'HYBRID'] as const satisfies readonly RetrievalMode[]
const queryKinds = ['ORIGINAL', 'REWRITE'] as const satisfies readonly KnowledgeQueryKind[]
const answerabilityStatuses = [
  'SUPPORTED',
  'REJECTED',
  'NOT_EVALUATED',
] as const satisfies readonly AnswerabilityStatus[]
const answerabilityMethods = [
  'GROUNDED_ANSWER_LLM',
  'HEURISTIC_GATE',
  'NOT_RUN',
] as const satisfies readonly AnswerabilityMethod[]
const scoreAvailabilities = [
  'AVAILABLE',
  'NOT_APPLICABLE',
  'NOT_EXPOSED',
  'NOT_RUN',
] as const satisfies readonly ScoreAvailability[]

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
  const structureSchemaVersion = optionalInteger(
    item.structure_schema_version,
    'version.structure_schema_version',
    1,
  )
  const pageCount = optionalInteger(item.page_count, 'version.page_count', 0)
  const slideCount = optionalInteger(item.slide_count, 'version.slide_count', 0)
  const ocrPageCount = optionalInteger(item.ocr_page_count, 'version.ocr_page_count', 0)
  const parseWarnings = optionalWarnings(
    item.parse_warnings ?? item.parse_warnings_json,
    'version.parse_warnings',
  )
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
    ...(structureSchemaVersion === undefined
      ? {}
      : { structure_schema_version: structureSchemaVersion }),
    ...(item.layout_profile === undefined
      ? {}
      : { layout_profile: nullableString(item.layout_profile, 'version.layout_profile') }),
    ...(item.ocr_profile === undefined
      ? {}
      : { ocr_profile: nullableString(item.ocr_profile, 'version.ocr_profile') }),
    ...(pageCount === undefined ? {} : { page_count: pageCount }),
    ...(slideCount === undefined ? {} : { slide_count: slideCount }),
    ...(ocrPageCount === undefined ? {} : { ocr_page_count: ocrPageCount }),
    ...(parseWarnings === undefined ? {} : { parse_warnings: parseWarnings }),
    created_at: string(item.created_at, 'version.created_at'),
    published_at: nullableString(item.published_at, 'version.published_at'),
  }
}

export function parseJob(value: unknown): IngestionJobSummary {
  const item = object(value, 'job')
  const errorCode = nullableString(item.error_code, 'job.error_code')
  const errorMessage = nullableString(item.error_message, 'job.error_message')
  const stageDetail = item.stage_detail === undefined
    ? undefined
    : nullableString(item.stage_detail, 'job.stage_detail')
  const pages = item.pages === undefined || item.pages === null
    ? undefined
    : parsePageProgress(item.pages)
  const warnings = optionalWarnings(item.warnings, 'job.warnings')
  return {
    id: string(item.id, 'job.id'),
    workspace_id: string(item.workspace_id, 'job.workspace_id'),
    document_id: string(item.document_id, 'job.document_id'),
    document_version_id: string(item.document_version_id, 'job.document_version_id'),
    status: enumeration(item.status, jobStatuses, 'job.status'),
    stage: string(item.stage, 'job.stage'),
    ...(stageDetail === undefined ? {} : { stage_detail: stageDetail }),
    progress: number(item.progress, 'job.progress'),
    ...(pages === undefined ? {} : { pages }),
    ...(warnings === undefined ? {} : { warnings }),
    attempt_count: number(item.attempt_count, 'job.attempt_count'),
    retryable: boolean(item.retryable, 'job.retryable'),
    ...(errorCode === null ? {} : { error_code: errorCode }),
    ...(errorMessage === null ? {} : { error_message: errorMessage }),
    version: number(item.version, 'job.version'),
    created_at: string(item.created_at, 'job.created_at'),
    updated_at: string(item.updated_at, 'job.updated_at'),
  }
}

function parsePageProgress(value: unknown): IngestionPageProgress {
  const pages = object(value, 'job.pages')
  const parsed = {
    total: integer(pages.total, 'job.pages.total', 0),
    ocr_required: integer(pages.ocr_required, 'job.pages.ocr_required', 0),
    ocr_completed: integer(pages.ocr_completed, 'job.pages.ocr_completed', 0),
  }
  if (parsed.ocr_required > parsed.total || parsed.ocr_completed > parsed.ocr_required) {
    invalid('job.pages')
  }
  return parsed
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

function parseScoreContribution(value: unknown, field: string): ScoreContribution {
  const contribution = object(value, field)
  const rank = nullableNumber(contribution.rank, `${field}.rank`)
  if (rank !== null && (!Number.isInteger(rank) || rank < 1)) invalid(`${field}.rank`)
  return {
    query_index: integer(contribution.query_index, `${field}.query_index`, 0),
    value: number(contribution.value, `${field}.value`),
    rank,
    source: nullableString(contribution.source, `${field}.source`),
  }
}

function parseScoreContributions(value: unknown, field: string): ScoreContribution[] {
  if (!Array.isArray(value)) invalid(field)
  return value.map((entry, index) => parseScoreContribution(entry, `${field}.${index}`))
}

function parseScoreStage(value: unknown, field: string): ScoreStage {
  const stage = object(value, field)
  const availability = enumeration(stage.availability, scoreAvailabilities, `${field}.availability`)
  const scoreValue = nullableNumber(stage.value, `${field}.value`)
  const rank = nullableNumber(stage.rank, `${field}.rank`)
  const rrfK = nullableNumber(stage.rrf_k, `${field}.rrf_k`)
  if (availability === 'AVAILABLE' && scoreValue === null) invalid(`${field}.value`)
  if (availability !== 'AVAILABLE' && scoreValue !== null) invalid(`${field}.value`)
  if (rank !== null && (!Number.isInteger(rank) || rank < 1)) invalid(`${field}.rank`)
  if (rrfK !== null && (!Number.isInteger(rrfK) || rrfK < 1)) invalid(`${field}.rrf_k`)
  return {
    availability,
    value: scoreValue,
    rank,
    method: nullableString(stage.method, `${field}.method`),
    rrf_k: rrfK,
    per_query: parseScoreContributions(stage.per_query, `${field}.per_query`),
    contributions: parseScoreContributions(stage.contributions, `${field}.contributions`),
  }
}

function parseScoreDetails(value: unknown): KnowledgeScoreDetails {
  const details = object(value, 'search.hit.score_details')
  return {
    dense_similarity: parseScoreStage(
      details.dense_similarity,
      'search.hit.score_details.dense_similarity',
    ),
    bm25: parseScoreStage(details.bm25, 'search.hit.score_details.bm25'),
    channel_fusion: parseScoreStage(
      details.channel_fusion,
      'search.hit.score_details.channel_fusion',
    ),
    query_fusion: parseScoreStage(
      details.query_fusion,
      'search.hit.score_details.query_fusion',
    ),
    rerank: parseScoreStage(details.rerank, 'search.hit.score_details.rerank'),
    final: parseScoreStage(details.final, 'search.hit.score_details.final'),
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
    ...(hit.source_spans === undefined
      ? {}
      : { source_spans: parseSourceSpans(hit.source_spans, 'search.hit.source_spans') }),
    score: number(hit.score, 'search.hit.score'),
    scores: {
      ...(dense === null ? {} : { dense }),
      ...(sparse === null ? {} : { sparse }),
      fusion: number(scoreData.fusion, 'search.hit.scores.fusion'),
      ...(rerank === null ? {} : { rerank }),
    },
    ...(hit.score_details === undefined
      ? {}
      : { score_details: parseScoreDetails(hit.score_details) }),
    context: string(hit.context, 'search.hit.context'),
  }
}

function parseSourceSpan(value: unknown, field: string): KnowledgeSourceSpan {
  const span = object(value, field)
  const pageNo = nullableNumber(span.page_no, `${field}.page_no`)
  const slideNo = nullableNumber(span.slide_no, `${field}.slide_no`)
  if (pageNo !== null && (!Number.isInteger(pageNo) || pageNo < 1)) invalid(`${field}.page_no`)
  if (slideNo !== null && (!Number.isInteger(slideNo) || slideNo < 1)) invalid(`${field}.slide_no`)
  return {
    source_span_id: nonEmptyString(span.source_span_id, `${field}.source_span_id`),
    page_no: pageNo,
    slide_no: slideNo,
    bbox: boundingBox(span.bbox, `${field}.bbox`),
    excerpt: string(span.excerpt, `${field}.excerpt`),
    ...(span.excerpt_sha256 === undefined
      ? {}
      : { excerpt_sha256: nonEmptyString(span.excerpt_sha256, `${field}.excerpt_sha256`) }),
  }
}

function parseSourceSpans(value: unknown, field: string): KnowledgeSourceSpan[] {
  if (!Array.isArray(value)) invalid(field)
  return value.map((entry, index) => parseSourceSpan(entry, `${field}.${index}`))
}

export function parseSourceSpanLocator(value: unknown): SourceSpanLocator {
  const item = object(value, 'source_span')
  const base = parseSourceSpan(item, 'source_span')
  const spanOrdinal = optionalInteger(item.span_ordinal, 'source_span.span_ordinal', 0)
  const blockCharStart = item.block_char_start === undefined
    ? undefined
    : nullableNumber(item.block_char_start, 'source_span.block_char_start')
  const blockCharEnd = item.block_char_end === undefined
    ? undefined
    : nullableNumber(item.block_char_end, 'source_span.block_char_end')
  return {
    ...base,
    ...(item.workspace_id === undefined
      ? {}
      : { workspace_id: string(item.workspace_id, 'source_span.workspace_id') }),
    document_id: nonEmptyString(item.document_id, 'source_span.document_id'),
    document_version_id: nonEmptyString(
      item.document_version_id,
      'source_span.document_version_id',
    ),
    ...(item.chunk_id === undefined
      ? {}
      : { chunk_id: nullableString(item.chunk_id, 'source_span.chunk_id') }),
    ...(spanOrdinal === undefined ? {} : { span_ordinal: spanOrdinal }),
    ...(item.block_ref === undefined
      ? {}
      : { block_ref: nullableString(item.block_ref, 'source_span.block_ref') }),
    ...(blockCharStart === undefined ? {} : { block_char_start: blockCharStart }),
    ...(blockCharEnd === undefined ? {} : { block_char_end: blockCharEnd }),
    excerpt_sha256: nonEmptyString(item.excerpt_sha256, 'source_span.excerpt_sha256'),
    ...(item.source_sha256 === undefined
      ? {}
      : { source_sha256: nonEmptyString(item.source_sha256, 'source_span.source_sha256') }),
  }
}

function parseFusionScoring(value: unknown, field: string) {
  const fusion = object(value, field)
  const rrfK = nullableNumber(fusion.rrf_k, `${field}.rrf_k`)
  if (rrfK !== null && (!Number.isInteger(rrfK) || rrfK < 1)) invalid(`${field}.rrf_k`)
  return {
    method: nullableString(fusion.method, `${field}.method`),
    applied: boolean(fusion.applied, `${field}.applied`),
    rrf_k: rrfK,
  }
}

function parseSearchScoring(value: unknown): KnowledgeSearchScoring {
  const scoring = object(value, 'search.scoring')
  const version = integer(scoring.version, 'search.scoring.version', 1)
  if (version !== 1) invalid('search.scoring.version')
  return {
    version,
    requested_retrieval_mode: enumeration(
      scoring.requested_retrieval_mode,
      retrievalModes,
      'search.scoring.requested_retrieval_mode',
    ),
    effective_retrieval_mode: enumeration(
      scoring.effective_retrieval_mode,
      retrievalModes,
      'search.scoring.effective_retrieval_mode',
    ),
    query_count: integer(scoring.query_count, 'search.scoring.query_count', 1),
    channel_fusion: parseFusionScoring(
      scoring.channel_fusion,
      'search.scoring.channel_fusion',
    ),
    query_fusion: parseFusionScoring(
      scoring.query_fusion,
      'search.scoring.query_fusion',
    ),
    rerank_applied: boolean(scoring.rerank_applied, 'search.scoring.rerank_applied'),
  }
}

function parseQueryTrace(value: unknown): KnowledgeQueryTrace {
  const trace = object(value, 'search.query_trace')
  if (!Array.isArray(trace.items)) invalid('search.query_trace.items')
  return {
    exposed: boolean(trace.exposed, 'search.query_trace.exposed'),
    items: trace.items.map((value, index) => {
      const item = object(value, `search.query_trace.items.${index}`)
      return {
        index: integer(item.index, `search.query_trace.items.${index}.index`, 0),
        kind: enumeration(
          item.kind,
          queryKinds,
          `search.query_trace.items.${index}.kind`,
        ),
        text: string(item.text, `search.query_trace.items.${index}.text`),
      }
    }),
    redaction_reason: nullableString(
      trace.redaction_reason,
      'search.query_trace.redaction_reason',
    ),
  }
}

function parseAnswerability(value: unknown): KnowledgeAnswerability {
  const diagnostic = object(value, 'search.answerability')
  const lexicalCoverage = diagnostic.lexical_coverage === undefined
    ? undefined
    : number(diagnostic.lexical_coverage, 'search.answerability.lexical_coverage')
  const lexicalThreshold = diagnostic.lexical_threshold === undefined
    ? undefined
    : number(diagnostic.lexical_threshold, 'search.answerability.lexical_threshold')
  const scoreThreshold = diagnostic.score_threshold === undefined
    ? undefined
    : number(diagnostic.score_threshold, 'search.answerability.score_threshold')
  if (scoreThreshold !== undefined && scoreThreshold < 0) {
    invalid('search.answerability.score_threshold')
  }
  if (lexicalCoverage !== undefined && (lexicalCoverage < 0 || lexicalCoverage > 1)) {
    invalid('search.answerability.lexical_coverage')
  }
  if (lexicalThreshold !== undefined && (lexicalThreshold < 0 || lexicalThreshold > 1)) {
    invalid('search.answerability.lexical_threshold')
  }
  return {
    status: enumeration(
      diagnostic.status,
      answerabilityStatuses,
      'search.answerability.status',
    ),
    method: enumeration(
      diagnostic.method,
      answerabilityMethods,
      'search.answerability.method',
    ),
    reason_code: nonEmptyString(
      diagnostic.reason_code,
      'search.answerability.reason_code',
    ),
    precheck_code: nonEmptyString(
      diagnostic.precheck_code,
      'search.answerability.precheck_code',
    ),
    provider_called: boolean(
      diagnostic.provider_called,
      'search.answerability.provider_called',
    ),
    checked_hit_ids: strings(
      diagnostic.checked_hit_ids,
      'search.answerability.checked_hit_ids',
    ),
    supporting_hit_ids: strings(
      diagnostic.supporting_hit_ids,
      'search.answerability.supporting_hit_ids',
    ),
    ...(diagnostic.raw_top_score === undefined
      ? {}
      : {
          raw_top_score: number(
            diagnostic.raw_top_score,
            'search.answerability.raw_top_score',
          ),
        }),
    ...(diagnostic.gate_score === undefined
      ? {}
      : {
          gate_score: number(
            diagnostic.gate_score,
            'search.answerability.gate_score',
          ),
        }),
    ...(scoreThreshold === undefined ? {} : { score_threshold: scoreThreshold }),
    ...(lexicalCoverage === undefined ? {} : { lexical_coverage: lexicalCoverage }),
    ...(lexicalThreshold === undefined ? {} : { lexical_threshold: lexicalThreshold }),
    ...(diagnostic.lexical_passed === undefined
      ? {}
      : {
          lexical_passed: boolean(
            diagnostic.lexical_passed,
            'search.answerability.lexical_passed',
          ),
        }),
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
    ...(result.scoring === undefined ? {} : { scoring: parseSearchScoring(result.scoring) }),
    ...(result.query_trace === undefined
      ? {}
      : { query_trace: parseQueryTrace(result.query_trace) }),
    ...(result.answerability === undefined
      ? {}
      : { answerability: parseAnswerability(result.answerability) }),
    components: strings(result.components, 'search.components'),
    timings_ms: timingsMs,
    warnings: strings(result.warnings, 'search.warnings'),
    degraded: boolean(result.degraded, 'search.degraded'),
  }
}
