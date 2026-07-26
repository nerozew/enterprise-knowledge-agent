import { apiClient } from './client'
import { knowledgeSearchApi } from './knowledgeSearch'
import type { KnowledgeSearchInput } from './types'

describe('知识检索 API 契约', () => {
  it('只发送冻结的格式与发布时间过滤字段', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        query: '审批',
        response_mode: 'HITS',
        answer: null,
        refusal: { code: 'insufficient_evidence', message: '未检索到可用证据' },
        hits: [],
        provider: {
          mode: 'FIXTURE',
          embedding: 'fixture',
          reranker: 'disabled',
          llm: 'disabled',
          profile: 'hybrid-v1',
        },
        components: ['DENSE', 'BM25', 'RRF'],
        timings_ms: { total: 1 },
        warnings: [],
        degraded: false,
      },
    })
    const input: KnowledgeSearchInput = {
      query: '审批',
      strategy: 'BASIC',
      retrieval_mode: 'HYBRID',
      top_k: 5,
      candidate_k: 50,
      fusion: 'RRF',
      rerank: 'AUTO',
      rewrite: 'OFF',
      multi_query: false,
      expand_context: true,
      response_mode: 'HITS',
      filters: {
        document_ids: ['doc-1'],
        source_formats: ['PDF'],
        published_after: '2026-01-01T00:00:00Z',
        published_before: '2026-06-30T23:59:59Z',
      },
    }

    await knowledgeSearchApi.search('workspace-1', input)

    expect(post).toHaveBeenCalledWith(
      '/workspaces/workspace-1/search',
      expect.objectContaining({
        filters: {
          document_ids: ['doc-1'],
          source_formats: ['PDF'],
          published_after: '2026-01-01T00:00:00Z',
          published_before: '2026-06-30T23:59:59Z',
        },
      }),
      {},
    )
    const sent = post.mock.calls[0]?.[1] as Record<string, unknown>
    expect(JSON.stringify(sent)).not.toContain('"formats"')
    expect(JSON.stringify(sent)).not.toContain('"published_from"')
    expect(JSON.stringify(sent)).not.toContain('"published_to"')
  })
})
