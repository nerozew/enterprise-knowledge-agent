import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import type { KnowledgeSearchResult } from '@/api/types'
import { useKnowledgeSearchStore } from '@/stores/knowledgeSearch'
import { useWorkspaceStore } from '@/stores/workspaces'
import KnowledgeAskView from './KnowledgeAskView.vue'

function refusedResult(code: string): KnowledgeSearchResult {
  return {
    query: '采购审批',
    response_mode: 'ANSWER',
    answer: null,
    refusal: { code, message: '没有生成答案' },
    hits: [{
      chunk_id: 'chunk-1',
      document_id: 'doc-1',
      document_version_id: 'version-1',
      title: '采购制度',
      page: 2,
      heading_path: ['审批'],
      quote: '候选原文',
      source_span_id: 'span-1',
      score: 0.016,
      scores: { fusion: 0.016 },
      context: '',
    }],
    provider: { mode: 'LOCAL', embedding: null, reranker: null, llm: null, profile: 'hybrid-v1' },
    components: ['HYBRID', 'RRF'],
    timings_ms: { total: 10 },
    warnings: [],
    degraded: false,
  }
}

function supportedResult(): KnowledgeSearchResult {
  const result = refusedResult('model_refused')
  return {
    ...result,
    answer: '办公电脑遗失后应立即报告信息安全部门。',
    refusal: null,
    query_trace: {
      exposed: true,
      items: [
        { index: 0, kind: 'ORIGINAL', text: '电脑丢失怎么办？' },
        { index: 1, kind: 'REWRITE', text: '办公电脑遗失处理' },
      ],
      redaction_reason: null,
    },
    answerability: {
      status: 'SUPPORTED',
      method: 'GROUNDED_ANSWER_LLM',
      reason_code: 'DIRECT_SUPPORT',
      precheck_code: 'lexical_coverage_below_threshold',
      provider_called: true,
      checked_hit_ids: ['chunk-1'],
      supporting_hit_ids: ['chunk-1'],
      raw_top_score: 0.048395,
      gate_score: 0.016132,
      score_threshold: 0.015,
      lexical_coverage: 0.2,
      lexical_threshold: 0.3,
      lexical_passed: false,
    },
  }
}

function mountView(result: KnowledgeSearchResult) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workspaces = useWorkspaceStore()
  const search = useKnowledgeSearchStore()
  workspaces.activeWorkspaceId = 'workspace-1'
  search.activateWorkspace('workspace-1')
  search.form.include_query_trace = Boolean(result.query_trace)
  search.result = result

  return mount(KnowledgeAskView, {
    global: {
      plugins: [pinia],
      stubs: {
        AppShell: { template: '<main><slot /></main>' },
        CitationCard: {
          props: ['supportsAnswer'],
          template: '<article>候选卡片<span v-if="supportsAnswer">支持答案</span></article>',
        },
        RetrievalSettings: { template: '<div />' },
        ElAlert: {
          props: ['title', 'description'],
          template: '<div class="alert-stub"><strong>{{ title }}</strong><p>{{ description }}</p><slot /></div>',
        },
        ElButton: { template: '<button><slot /></button>' },
        ElEmpty: { props: ['description'], template: '<div>{{ description }}</div>' },
        ElInput: { template: '<textarea />' },
        ElSkeleton: { template: '<div />' },
        ElTag: { template: '<span><slot /></span>' },
      },
    },
  })
}

describe('知识问答拒答候选', () => {
  it('拒答时把 Hits 明确标为未通过门禁的核对候选', () => {
    const wrapper = mountView(refusedResult('insufficient_evidence'))

    expect(wrapper.text()).toContain('未通过证据门禁的候选片段（1）')
    expect(wrapper.text()).toContain('仅供核对检索过程')
    expect(wrapper.text()).toContain('不代表问题已有可靠答案')
    expect(wrapper.text()).toContain('未用于生成答案')
    expect(wrapper.text()).not.toContain('证据来源')
  })

  it('model_refused 不被误写成仅证据不足', () => {
    const wrapper = mountView(refusedResult('model_refused'))

    expect(wrapper.text()).toContain('模型拒绝形成答案，供核对的候选片段（1）')
    expect(wrapper.text()).toContain('证据已通过门禁')
    expect(wrapper.text()).toContain('答案模型在证据上下文下拒绝形成答案')
    expect(wrapper.text()).toContain('仅供核对检索与门禁结果')
    expect(wrapper.text()).toContain('不构成最终引用')
    expect(wrapper.text()).not.toContain('未通过证据门禁')
    expect(wrapper.text()).not.toContain('未用于生成答案')
  })

  it('SUPPORTED 显示状态、支持卡标识和实际 Query Trace', () => {
    const wrapper = mountView(supportedResult())

    expect(wrapper.text()).toContain('已通过直接证据支持检查')
    expect(wrapper.text()).toContain('支持答案')
    expect(wrapper.text()).toContain('Original')
    expect(wrapper.text()).toContain('Rewrite 1')
    expect(wrapper.text()).toContain('办公电脑遗失处理')
    expect(wrapper.text()).toContain('模型改写仅用于扩展检索，不代表答案')
    expect(wrapper.text()).toContain('归一化门禁分 / 阈值')
    expect(wrapper.text()).toContain('0.016 / 0.015')
    expect(wrapper.text()).toContain('20.0% / 30.0%')
    expect(wrapper.text()).toContain('不等于答案正确率或置信度')
  })

  it('HITS 即使伴随 refusal 也不把 Answerability 写成已证明无答案', () => {
    const result = refusedResult('insufficient_evidence')
    result.response_mode = 'HITS'
    result.answerability = {
      status: 'REJECTED',
      method: 'HEURISTIC_GATE',
      reason_code: 'SCORE_BELOW_THRESHOLD',
      precheck_code: 'score_below_threshold',
      provider_called: false,
      checked_hit_ids: ['chunk-1'],
      supporting_hit_ids: [],
      raw_top_score: 0.01,
      gate_score: 0.01,
      score_threshold: 0.015,
      lexical_coverage: 0.1,
      lexical_threshold: 0.3,
      lexical_passed: false,
    }
    const wrapper = mountView(result)

    expect(wrapper.text()).toContain('仅展示候选，未执行语义可回答性判断')
    expect(wrapper.text()).not.toContain('当前已发布语料未提供这个问题的直接答案')
  })
})
