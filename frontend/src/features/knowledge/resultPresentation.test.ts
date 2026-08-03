import { describe, expect, it } from 'vitest'
import type { KnowledgeSearchResult } from '@/api/types'
import { buildKnowledgeResultPresentation } from './resultPresentation'

function result(refusalCode: string | null, hitCount = 1): KnowledgeSearchResult {
  return {
    query: '采购审批',
    response_mode: 'ANSWER',
    answer: refusalCode ? null : '答案',
    refusal: refusalCode ? { code: refusalCode, message: '拒绝回答' } : null,
    hits: Array.from({ length: hitCount }, (_, index) => ({
      chunk_id: `chunk-${index}`,
      document_id: 'doc-1',
      document_version_id: 'version-1',
      title: '制度',
      page: 1,
      heading_path: [],
      quote: '原文',
      source_span_id: `span-${index}`,
      score: 0.1,
      scores: { fusion: 0.1 },
      context: '',
    })),
    provider: { mode: 'LOCAL', embedding: null, reranker: null, llm: null, profile: 'v1' },
    components: [],
    timings_ms: {},
    warnings: [],
    degraded: false,
  }
}

describe('RAG 结果文案模型', () => {
  it('拒答候选不称为引用或可靠证据', () => {
    const presentation = buildKnowledgeResultPresentation(result('insufficient_evidence', 2))

    expect(presentation.title).toBe('未通过证据门禁的候选片段（2）')
    expect(presentation.candidateNotice).toContain('仅供核对')
    expect(presentation.candidateNotice).toContain('不代表问题已有可靠答案')
    expect(presentation.candidateNotice).toContain('未用于生成答案')
  })

  it('model_refused 明确门禁已通过且不推断所有展示候选都进入模型上下文', () => {
    const refused = buildKnowledgeResultPresentation(result('model_refused'))

    expect(refused.eyebrow).toBe('模型拒答')
    expect(refused.title).toBe('模型拒绝形成答案，供核对的候选片段（1）')
    expect(refused.refusalDescription).toContain('证据已通过门禁')
    expect(refused.refusalDescription).toContain('证据上下文下拒绝形成答案')
    expect(refused.candidateNotice).toContain('仅供核对检索与门禁结果')
    expect(refused.candidateNotice).toContain('不构成最终引用')
    expect(refused.title).not.toContain('未通过证据门禁')
    expect(refused.candidateNotice).not.toContain('未用于生成答案')
    expect(refused.candidateNotice).not.toContain('全部候选')
  })

  it('为成功答案保留证据来源语义', () => {
    const answered = buildKnowledgeResultPresentation(result(null))

    expect(answered.eyebrow).toBe('证据来源')
    expect(answered.title).toBe('1 条可复核片段')
  })
})
