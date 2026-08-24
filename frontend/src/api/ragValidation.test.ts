import { ApiError } from './client'
import { parseSearchResult, parseSourceSpanLocator, parseUploadResult } from './ragValidation'

describe('RAG wire 数据校验', () => {
  it('校验上传响应并保留发布版本与 Job', () => {
    const result = parseUploadResult({
      document: {
        id: 'doc-1',
        workspace_id: 'workspace-1',
        title: '制度',
        status: 'ACTIVE',
        version: 1,
        published_version_id: 'version-1',
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:00Z',
      },
      version: {
        id: 'version-1',
        document_id: 'doc-1',
        format: 'PDF',
        mime_type: 'application/pdf',
        file_name: 'policy.pdf',
        file_size: 1024,
        file_sha256: 'a'.repeat(64),
        status: 'PUBLISHED',
        parser_profile: 'structure-v1',
        chunk_profile: 'parent-child-v1',
        index_profile: 'hybrid-v1',
        structure_schema_version: 2,
        layout_profile: 'docling-layout-v2',
        ocr_profile: 'rapidocr-ch-en',
        page_count: 8,
        slide_count: null,
        ocr_page_count: 2,
        parse_warnings: [{ code: 'ocr_used', message: '使用 OCR', page_no: 4 }],
        created_at: '2026-07-26T00:00:00Z',
        published_at: '2026-07-26T00:00:01Z',
      },
      job: {
        id: 'job-1',
        workspace_id: 'workspace-1',
        document_id: 'doc-1',
        document_version_id: 'version-1',
        status: 'SUCCEEDED',
        stage: 'INDEXING',
        stage_detail: 'OCR',
        progress: 100,
        pages: { total: 8, ocr_required: 2, ocr_completed: 2 },
        warnings: [{ code: 'low_confidence', message: '低置信度文本已保留' }],
        attempt_count: 1,
        retryable: false,
        version: 2,
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:01Z',
      },
      reused: false,
    })

    expect(result.version.status).toBe('PUBLISHED')
    expect(result.version.ocr_page_count).toBe(2)
    expect(result.version.parse_warnings).toEqual([
      { code: 'ocr_used', message: '使用 OCR', page_no: 4 },
    ])
    expect(result.job.progress).toBe(100)
    expect(result.job.pages?.ocr_completed).toBe(2)
  })

  it('拒绝缺少固定 source span 的检索命中', () => {
    expect(() => parseSearchResult({
      query: '审批',
      response_mode: 'HITS',
      answer: null,
      refusal: null,
      hits: [{
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        document_version_id: 'version-1',
        title: '制度',
        page: 3,
        heading_path: ['审批'],
        quote: '超过十万元需审批',
        score: 0.9,
        scores: { fusion: 0.9 },
        context: '',
      }],
      provider: { mode: 'LOCAL', embedding: 'model', reranker: null, llm: null, profile: 'v1' },
      components: ['dense'],
      timings_ms: { total: 12 },
      warnings: [],
      degraded: false,
    })).toThrowError(ApiError)
  })

  it('解析真实后端无证据 200 响应的结构化拒答', () => {
    const result = parseSearchResult({
      query: '不存在的制度',
      response_mode: 'HITS',
      answer: null,
      refusal: {
        code: 'insufficient_evidence',
        message: '未检索到可用证据',
      },
      hits: [],
      provider: {
        mode: 'FIXTURE',
        embedding: 'fixture-embedding-v1',
        reranker: 'disabled',
        llm: 'disabled',
        profile: 'hybrid-v1',
      },
      components: ['DENSE', 'BM25', 'RRF'],
      timings_ms: { dense: 1.2, sparse: 0.8, total: 2.5 },
      warnings: ['fixture_provider_not_for_quality_claims'],
      degraded: false,
    })

    expect(result.refusal).toEqual({
      code: 'insufficient_evidence',
      message: '未检索到可用证据',
    })
    expect(result.hits).toEqual([])
    expect(result.timings_ms.total).toBe(2.5)
  })

  it('解析 additive scoring 与 availability，且旧响应仍兼容', () => {
    const scoreStage = (
      availability: 'AVAILABLE' | 'NOT_APPLICABLE' | 'NOT_EXPOSED' | 'NOT_RUN',
      value: number | null,
      method: string | null = null,
    ) => ({
      availability,
      value,
      rank: value === null ? null : 1,
      method,
      rrf_k: method === 'RRF' ? 60 : null,
      per_query: [],
      contributions: [],
    })
    const result = parseSearchResult({
      query: '审批',
      response_mode: 'HITS',
      answer: null,
      refusal: null,
      hits: [{
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        document_version_id: 'version-1',
        title: '制度',
        page: 3,
        heading_path: ['审批'],
        quote: '超过十万元需审批',
        source_span_id: 'span-1',
        source_spans: [{
          source_span_id: 'span-1',
          page_no: 3,
          slide_no: null,
          bbox: { left: 0.1, top: 0.2, right: 0.9, bottom: 0.4 },
          excerpt: '超过十万元需审批',
          excerpt_sha256: 'a'.repeat(64),
        }],
        score: 0.016,
        scores: { dense: 0.033, fusion: 0.016 },
        score_details: {
          dense_similarity: scoreStage('NOT_EXPOSED', null),
          bm25: scoreStage('NOT_EXPOSED', null),
          channel_fusion: scoreStage('AVAILABLE', 0.033, 'RRF'),
          query_fusion: {
            ...scoreStage('AVAILABLE', 0.016, 'RRF'),
            contributions: [{
              query_index: 0,
              value: 0.016,
              rank: 1,
              source: 'channel_fusion',
            }],
          },
          rerank: scoreStage('NOT_RUN', null),
          final: scoreStage('AVAILABLE', 0.016, 'QUERY_FUSION'),
        },
        context: '',
      }],
      provider: { mode: 'LOCAL', embedding: 'model', reranker: null, llm: null, profile: 'v1' },
      scoring: {
        version: 1,
        requested_retrieval_mode: 'HYBRID',
        effective_retrieval_mode: 'HYBRID',
        query_count: 1,
        channel_fusion: { method: 'RRF', applied: true, rrf_k: 60 },
        query_fusion: { method: 'RRF', applied: true, rrf_k: 60 },
        rerank_applied: false,
      },
      query_trace: {
        exposed: true,
        items: [
          { index: 0, kind: 'ORIGINAL', text: '审批' },
          { index: 1, kind: 'REWRITE', text: '采购审批要求' },
        ],
        redaction_reason: null,
      },
      answerability: {
        status: 'NOT_EVALUATED',
        method: 'NOT_RUN',
        reason_code: 'HITS_MODE',
        precheck_code: 'lexical_coverage_passed',
        provider_called: false,
        checked_hit_ids: ['chunk-1'],
        supporting_hit_ids: [],
        raw_top_score: 0.048395,
        gate_score: 0.016132,
        score_threshold: 0.015,
        lexical_coverage: 0.4,
        lexical_threshold: 0.3,
        lexical_passed: true,
      },
      components: ['HYBRID', 'RRF'],
      timings_ms: { total: 12 },
      warnings: [],
      degraded: false,
    })

    expect(result.scoring?.query_count).toBe(1)
    expect(result.query_trace?.items[1]).toEqual({
      index: 1,
      kind: 'REWRITE',
      text: '采购审批要求',
    })
    expect(result.answerability).toMatchObject({
      status: 'NOT_EVALUATED',
      provider_called: false,
      checked_hit_ids: ['chunk-1'],
      raw_top_score: 0.048395,
      gate_score: 0.016132,
      lexical_passed: true,
    })
    expect(result.hits[0]?.score_details?.dense_similarity.availability).toBe('NOT_EXPOSED')
    expect(result.hits[0]?.source_spans?.[0]).toMatchObject({
      page_no: 3,
      bbox: { left: 0.1, top: 0.2, right: 0.9, bottom: 0.4 },
    })
    expect(result.hits[0]?.score_details?.query_fusion.contributions[0]).toEqual({
      query_index: 0,
      value: 0.016,
      rank: 1,
      source: 'channel_fusion',
    })

    const legacy = parseSearchResult({
      query: '审批',
      response_mode: 'HITS',
      answer: null,
      refusal: null,
      hits: [],
      provider: { mode: 'LOCAL', embedding: 'model', reranker: null, llm: null, profile: 'v1' },
      components: [],
      timings_ms: {},
      warnings: [],
      degraded: false,
    })
    expect(legacy.scoring).toBeUndefined()
    expect(legacy.query_trace).toBeUndefined()
    expect(legacy.answerability).toBeUndefined()
  })

  it('解析固定版本 Source Span 定位响应', () => {
    const locator = parseSourceSpanLocator({
      workspace_id: 'workspace-1',
      document_id: 'doc-1',
      document_version_id: 'version-1',
      chunk_id: 'chunk-1',
      source_span_id: 'span-1',
      span_ordinal: 0,
      block_ref: 'page-3:block-2',
      page_no: 3,
      slide_no: null,
      bbox: { left: 0.1, top: 0.2, right: 0.9, bottom: 0.4 },
      block_char_start: 10,
      block_char_end: 22,
      excerpt: '超过十万元需审批',
      excerpt_sha256: 'a'.repeat(64),
      source_sha256: 'b'.repeat(64),
    })

    expect(locator.document_version_id).toBe('version-1')
    expect(locator.page_no).toBe(3)
    expect(locator.bbox?.left).toBe(0.1)
    expect(locator.source_sha256).toBe('b'.repeat(64))
  })

  it('拒绝未知的分数 availability 状态', () => {
    const unavailable = {
      availability: 'MISSING',
      value: null,
      rank: null,
      method: null,
      rrf_k: null,
      per_query: [],
      contributions: [],
    }
    expect(() => parseSearchResult({
      query: '审批',
      response_mode: 'HITS',
      answer: null,
      refusal: null,
      hits: [{
        chunk_id: 'chunk-1',
        document_id: 'doc-1',
        document_version_id: 'version-1',
        title: '制度',
        page: null,
        heading_path: [],
        quote: '片段',
        source_span_id: 'span-1',
        score: 0.1,
        scores: { fusion: 0.1 },
        score_details: {
          dense_similarity: unavailable,
          bm25: unavailable,
          channel_fusion: unavailable,
          query_fusion: unavailable,
          rerank: unavailable,
          final: unavailable,
        },
        context: '',
      }],
      provider: { mode: 'LOCAL', embedding: 'model', reranker: null, llm: null, profile: 'v1' },
      components: [],
      timings_ms: {},
      warnings: [],
      degraded: false,
    })).toThrowError(ApiError)
  })
})
