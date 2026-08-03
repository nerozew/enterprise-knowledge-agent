import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { KnowledgeAnswerability } from '@/api/types'
import AnswerabilityDiagnostics from './AnswerabilityDiagnostics.vue'

function answerability(): KnowledgeAnswerability {
  return {
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
  }
}

describe('Answerability 预检诊断', () => {
  it('展示原始分、归一化门禁分、词法覆盖率和通过状态', () => {
    const wrapper = mount(AnswerabilityDiagnostics, {
      props: { answerability: answerability() },
    })

    expect(wrapper.text()).toContain('原始排序分')
    expect(wrapper.text()).toContain('0.048')
    expect(wrapper.text()).toContain('归一化门禁分 / 阈值')
    expect(wrapper.text()).toContain('0.016 / 0.015')
    expect(wrapper.text()).toContain('词法覆盖率 / 阈值')
    expect(wrapper.text()).toContain('40.0% / 30.0%')
    expect(wrapper.text()).toContain('词法预检')
    expect(wrapper.text()).toContain('通过')
    expect(wrapper.text()).toContain('不等于答案正确率或置信度')
  })

  it('兼容旧后端缺少预检字段，并正确显示零值和未通过', async () => {
    const diagnostic = answerability()
    const wrapper = mount(AnswerabilityDiagnostics, {
      props: {
        answerability: {
          status: diagnostic.status,
          method: diagnostic.method,
          reason_code: diagnostic.reason_code,
          precheck_code: diagnostic.precheck_code,
          provider_called: diagnostic.provider_called,
          checked_hit_ids: diagnostic.checked_hit_ids,
          supporting_hit_ids: diagnostic.supporting_hit_ids,
        },
      },
    })
    expect(wrapper.find('.answerability-diagnostics').exists()).toBe(false)

    await wrapper.setProps({
      answerability: {
        ...diagnostic,
        raw_top_score: 0,
        gate_score: 0,
        lexical_coverage: 0,
        lexical_passed: false,
      },
    })
    expect(wrapper.text()).toContain('0.000')
    expect(wrapper.text()).toContain('0.0%')
    expect(wrapper.text()).toContain('未通过')
  })
})
