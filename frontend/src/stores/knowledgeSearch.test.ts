import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { knowledgeSearchApi } from '@/api/knowledgeSearch'
import type { KnowledgeSearchResult } from '@/api/types'
import { useKnowledgeSearchStore } from './knowledgeSearch'

vi.mock('@/api/knowledgeSearch', () => ({
  knowledgeSearchApi: { search: vi.fn() },
}))

function result(query: string): KnowledgeSearchResult {
  return {
    query,
    response_mode: 'HITS',
    answer: null,
    refusal: null,
    hits: [],
    provider: { mode: 'LOCAL', embedding: 'demo', reranker: null, llm: null, profile: 'hybrid-v1' },
    components: ['dense', 'bm25', 'rrf'],
    timings_ms: { total: 10 },
    warnings: [],
    degraded: false,
  }
}

describe('知识检索 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('切换 Workspace 会取消旧请求并清除来源', async () => {
    let resolve!: (value: KnowledgeSearchResult) => void
    vi.mocked(knowledgeSearchApi.search).mockReturnValue(new Promise((next) => { resolve = next }))
    const store = useKnowledgeSearchStore()
    store.activateWorkspace('a')
    store.form.query = '旧空间机密'
    const request = store.search()
    const signal = vi.mocked(knowledgeSearchApi.search).mock.calls[0]?.[2]

    store.activateWorkspace('b')
    resolve(result('旧空间机密'))
    await request

    expect(signal?.aborted).toBe(true)
    expect(store.workspaceId).toBe('b')
    expect(store.result).toBeNull()
    expect(store.form.query).toBe('')
  })

  it('ANSWER 依赖不可用时给出可行动提示', async () => {
    vi.mocked(knowledgeSearchApi.search).mockRejectedValue(
      new ApiError('LLM disabled', { code: 'llm_dependency_unavailable', status: 503 }),
    )
    const store = useKnowledgeSearchStore()
    store.activateWorkspace('a')
    store.form.query = '生成答案'
    store.form.response_mode = 'ANSWER'

    await store.search()

    expect(store.errorMessage).toContain('只返回检索结果')
  })

  it('默认省略 Query Trace 参数，显式开启后才发送 true', async () => {
    vi.mocked(knowledgeSearchApi.search).mockImplementation(
      async (_workspaceId, input) => result(input.query),
    )
    const store = useKnowledgeSearchStore()
    store.activateWorkspace('a')
    store.form.query = '默认检索'

    expect(store.form.include_query_trace).toBe(false)
    await store.search()
    expect(vi.mocked(knowledgeSearchApi.search).mock.calls[0]?.[1])
      .not.toHaveProperty('include_query_trace')

    store.form.query = '诊断检索'
    store.form.include_query_trace = true
    await store.search()

    expect(vi.mocked(knowledgeSearchApi.search).mock.calls[1]?.[1])
      .toMatchObject({ query: '诊断检索', include_query_trace: true })
  })
})
