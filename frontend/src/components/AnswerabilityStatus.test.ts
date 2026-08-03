import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { KnowledgeAnswerability } from '@/api/types'
import AnswerabilityStatus from './AnswerabilityStatus.vue'

const notEvaluated: KnowledgeAnswerability = {
  status: 'NOT_EVALUATED',
  method: 'NOT_RUN',
  reason_code: 'HITS_MODE',
  precheck_code: 'lexical_coverage_passed',
  provider_called: false,
  checked_hit_ids: ['chunk-1'],
  supporting_hit_ids: [],
}

function mountStatus(
  answerability: KnowledgeAnswerability,
  responseMode: 'HITS' | 'ANSWER',
) {
  return mount(AnswerabilityStatus, {
    props: { answerability, responseMode },
    global: {
      stubs: {
        ElAlert: {
          props: ['type', 'title', 'description'],
          template: '<section :data-type="type"><h2>{{ title }}</h2><p>{{ description }}</p></section>',
        },
      },
    },
  })
}

describe('语义可回答性状态', () => {
  it('HITS NOT_EVALUATED 使用中性候选提示，不声称证据充分', () => {
    const wrapper = mountStatus(notEvaluated, 'HITS')

    expect(wrapper.text()).toContain('仅展示候选，未执行语义可回答性判断')
    expect(wrapper.text()).not.toContain('证据充分')
    expect(wrapper.text()).not.toContain('已通过直接证据支持检查')
    expect(wrapper.find('section').attributes('data-type')).toBe('info')
  })

  it('HITS 即使收到 REJECTED 组合也保持未执行语义判断的中性语义', () => {
    const wrapper = mountStatus({
      ...notEvaluated,
      status: 'REJECTED',
      method: 'HEURISTIC_GATE',
      reason_code: 'SCORE_BELOW_THRESHOLD',
    }, 'HITS')

    expect(wrapper.text()).toContain('仅展示候选，未执行语义可回答性判断')
    expect(wrapper.text()).not.toContain('当前已发布语料未提供这个问题的直接答案')
    expect(wrapper.find('section').attributes('data-type')).toBe('info')
  })

  it('ANSWER 清晰区分 SUPPORTED 与 REJECTED', () => {
    const supported = mountStatus({
      ...notEvaluated,
      status: 'SUPPORTED',
      method: 'GROUNDED_ANSWER_LLM',
      reason_code: 'DIRECT_SUPPORT',
      provider_called: true,
      supporting_hit_ids: ['chunk-1'],
    }, 'ANSWER')
    const rejected = mountStatus({
      ...notEvaluated,
      status: 'REJECTED',
      method: 'GROUNDED_ANSWER_LLM',
      reason_code: 'TOPIC_MISMATCH',
      provider_called: true,
    }, 'ANSWER')

    expect(supported.text()).toContain('已通过直接证据支持检查')
    expect(supported.find('section').attributes('data-type')).toBe('success')
    expect(rejected.text()).toContain('当前已发布语料未提供这个问题的直接答案')
    expect(rejected.text()).toContain('仅为候选片段')
    expect(rejected.find('section').attributes('data-type')).toBe('warning')
  })
})
