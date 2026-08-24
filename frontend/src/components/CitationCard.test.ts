import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { documentApi } from '@/api/documents'
import type {
  KnowledgeHit,
  KnowledgeScoreDetails,
  KnowledgeSearchScoring,
  ScoreAvailability,
  ScoreStage,
} from '@/api/types'
import CitationCard from './CitationCard.vue'

function stage(
  availability: ScoreAvailability,
  value: number | null,
  method: string | null = null,
  rank: number | null = null,
): ScoreStage {
  return {
    availability,
    value,
    rank,
    method,
    rrf_k: method === 'RRF' ? 60 : null,
    per_query: [],
    contributions: [],
  }
}

function scoreDetails(queryScore = 0.016): KnowledgeScoreDetails {
  return {
    dense_similarity: stage('NOT_EXPOSED', null),
    bm25: stage('NOT_EXPOSED', null),
    channel_fusion: stage('AVAILABLE', 0.033, 'RRF', 1),
    query_fusion: stage('AVAILABLE', queryScore, 'RRF', 1),
    rerank: stage('NOT_RUN', null),
    final: stage('AVAILABLE', queryScore, 'QUERY_FUSION', 1),
  }
}

function hit(details?: KnowledgeScoreDetails): KnowledgeHit {
  return {
    chunk_id: 'chunk-1',
    document_id: 'doc-1',
    document_version_id: 'version-123456',
    title: '采购制度',
    page: 3,
    heading_path: ['审批', '大额采购'],
    quote: '超过十万元需要总经理审批。',
    source_span_id: 'span-123456789',
    score: 0.016,
    scores: { dense: 0.033, sparse: 0.031, fusion: 0.016 },
    ...(details ? { score_details: details } : {}),
    context: '审批制度上下文',
  }
}

function scoring(queryCount: number): KnowledgeSearchScoring {
  return {
    version: 1,
    requested_retrieval_mode: 'HYBRID',
    effective_retrieval_mode: 'HYBRID',
    query_count: queryCount,
    channel_fusion: { method: 'RRF', applied: true, rrf_k: 60 },
    query_fusion: { method: 'RRF', applied: true, rrf_k: 60 },
    rerank_applied: false,
  }
}

async function mountCard(
  cardHit: KnowledgeHit,
  cardScoring?: KnowledgeSearchScoring,
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/w/:workspaceId/knowledge', name: 'workspace-knowledge', component: { template: '<div />' } }],
  })
  await router.push('/w/workspace-1/knowledge')
  return mount(CitationCard, {
    props: {
      workspaceId: 'workspace-1',
      rank: 1,
      hit: cardHit,
      ...(cardScoring ? { scoring: cardScoring } : {}),
    },
    global: {
      plugins: [router],
      stubs: {
        ElAlert: { props: ['title'], template: '<aside>{{ title }}<slot /></aside>' },
        ElButton: { template: '<button><slot /></button>' },
        ElDialog: {
          props: ['modelValue', 'title'],
          template: '<section v-if="modelValue" class="dialog-stub"><h2>{{ title }}</h2><slot /></section>',
        },
        ElTooltip: {
          props: ['content'],
          template: '<span class="tooltip-stub" :data-content="content"><slot /></span>',
        },
        ElTag: { template: '<span class="tag-stub"><slot /></span>' },
      },
      directives: { loading: () => undefined },
    },
  })
}

describe('来源与候选片段卡片', () => {
  it('显示固定版本、页码、Span、最终排序分和 RRF 语义', async () => {
    const wrapper = await mountCard(hit(scoreDetails()), scoring(1))

    expect(wrapper.text()).toContain('第 3 页')
    expect(wrapper.text()).toContain('发布版本 version-1234')
    expect(wrapper.text()).toContain('Span span-1234567')
    expect(wrapper.text()).toContain('最终排序分')
    expect(wrapper.text()).toContain('通道融合 RRF · k=60 0.033')
    expect(wrapper.text()).toContain('查询排序 RRF · k=60 0.016')
    expect(wrapper.text()).toContain('语义相似度：未提供')
    expect(wrapper.text()).toContain('重排分：未执行')
    expect(wrapper.text()).not.toContain('Dense 0.033')
    expect(wrapper.find('.primary-score').attributes('tabindex')).toBe('0')
    expect(wrapper.find('.tooltip-stub').attributes('data-content')).toContain('不是正确率或置信度')
    expect(wrapper.find('a').attributes('href')).toContain('version=version-123456')
  })

  it('多查询使用多查询融合标签', async () => {
    const wrapper = await mountCard(hit(scoreDetails()), scoring(2))

    expect(wrapper.text()).toContain('多查询融合 RRF · k=60')
    expect(wrapper.text()).not.toContain('查询排序 RRF · k=60')
  })

  it('不可拆分的通道与查询联合融合只显示一个阶段', async () => {
    const combinedDetails = scoreDetails(0.061)
    combinedDetails.channel_fusion = stage('AVAILABLE', 0.061, 'COMBINED_FUSION', 1)
    combinedDetails.query_fusion = stage('AVAILABLE', 0.061, 'COMBINED_FUSION', 1)
    combinedDetails.final = stage('AVAILABLE', 0.061, 'IDENTITY', 1)
    const combinedScoring = scoring(2)
    combinedScoring.channel_fusion = {
      method: 'COMBINED_FUSION',
      applied: true,
      rrf_k: null,
    }
    combinedScoring.query_fusion = {
      method: 'COMBINED_FUSION',
      applied: true,
      rrf_k: null,
    }

    const wrapper = await mountCard(hit(combinedDetails), combinedScoring)
    const labels = wrapper.text().match(/通道与查询联合融合/g) ?? []
    const breakdown = wrapper.find('.score-breakdown').text().replace(/\s+/g, ' ')

    expect(labels).toHaveLength(1)
    expect(breakdown).toContain('通道与查询联合融合 0.061 · #1')
  })

  it('只在被 Answerability 标记时显示支持答案', async () => {
    const supported = await mountCard(hit(scoreDetails()), scoring(1))
    await supported.setProps({ supportsAnswer: true })
    const candidate = await mountCard(hit(scoreDetails()), scoring(1))

    expect(supported.text()).toContain('支持答案')
    expect(candidate.text()).not.toContain('支持答案')
  })

  it('旧响应只显示旧版综合排序分，不展示 legacy 阶段猜测', async () => {
    const sourceSpan = vi.spyOn(documentApi, 'sourceSpan')
    const wrapper = await mountCard(hit())

    expect(wrapper.text()).toContain('旧版综合排序分')
    expect(wrapper.text()).toContain('Span span-1234567 · 旧版定位')
    expect(wrapper.text()).not.toContain('查看排序分明细')
    expect(wrapper.text()).not.toContain('Dense')
    expect(wrapper.text()).not.toContain('Fusion')
    expect(wrapper.findAll('button').some((button) => button.text().includes('Span'))).toBe(false)
    expect(sourceSpan).not.toHaveBeenCalled()
  })

  it('展示多段页/幻灯片定位并按需加载固定版本原文诊断', async () => {
    const cardHit = hit(scoreDetails())
    cardHit.page = null
    cardHit.source_span_id = 'legacy-span-not-indexed'
    cardHit.source_spans = [
      {
        source_span_id: 'span-v2-primary-123456',
        page_no: null,
        slide_no: 2,
        bbox: { left: 0.1, top: 0.2, right: 0.8, bottom: 0.4 },
        excerpt: '第二张幻灯片的原文',
        excerpt_sha256: 'a'.repeat(64),
      },
      {
        source_span_id: 'span-second-987654',
        page_no: null,
        slide_no: 3,
        bbox: null,
        excerpt: '第三张幻灯片的补充原文',
        excerpt_sha256: 'b'.repeat(64),
      },
    ]
    const sourceSpan = vi.spyOn(documentApi, 'sourceSpan').mockResolvedValue({
      workspace_id: 'workspace-1',
      document_id: 'doc-1',
      document_version_id: 'version-123456',
      chunk_id: 'chunk-1',
      source_span_id: 'span-v2-primary-123456',
      block_ref: 'slide-2:block-1',
      page_no: null,
      slide_no: 2,
      bbox: { left: 0.1, top: 0.2, right: 0.8, bottom: 0.4 },
      block_char_start: 0,
      block_char_end: 10,
      excerpt: '第二张幻灯片的原文',
      excerpt_sha256: 'a'.repeat(64),
      source_sha256: 'c'.repeat(64),
    })
    const wrapper = await mountCard(cardHit, scoring(1))

    expect(wrapper.text()).toContain('第 2 张幻灯片')
    expect(wrapper.text()).toContain('查看 2 个来源定位')
    const spanButton = wrapper.findAll('button')
      .find((button) => button.text().includes('span-v2-prim'))
    await spanButton?.trigger('click')
    await flushPromises()

    expect(sourceSpan).toHaveBeenCalledWith(
      'workspace-1',
      'doc-1',
      'version-123456',
      'span-v2-primary-123456',
      expect.any(AbortSignal),
    )
    expect(wrapper.find('.dialog-stub').text()).toContain('slide-2:block-1')
    expect(wrapper.find('.dialog-stub').text()).toContain('第二张幻灯片的原文')
    expect(wrapper.find('a').attributes('href')).toContain('slide=2')
    expect(wrapper.find('a').attributes('href')).toContain('span=span-v2-primary-123456')
    expect(wrapper.find('a').attributes('href')).not.toContain('legacy-span-not-indexed')
  })
})
