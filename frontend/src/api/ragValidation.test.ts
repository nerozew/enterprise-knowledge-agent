import { ApiError } from './client'
import { parseSearchResult, parseUploadResult } from './ragValidation'

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
        progress: 100,
        attempt_count: 1,
        retryable: false,
        version: 2,
        created_at: '2026-07-26T00:00:00Z',
        updated_at: '2026-07-26T00:00:01Z',
      },
      reused: false,
    })

    expect(result.version.status).toBe('PUBLISHED')
    expect(result.job.progress).toBe(100)
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
})
