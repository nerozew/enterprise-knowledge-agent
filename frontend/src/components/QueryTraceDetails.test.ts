import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import QueryTraceDetails from './QueryTraceDetails.vue'

describe('Query Trace 检索诊断', () => {
  it('按 Original 与 Rewrite 编号展示真实文本且仅做文本插值', () => {
    const wrapper = mount(QueryTraceDetails, {
      props: {
        trace: {
          exposed: true,
          items: [
            { index: 0, kind: 'ORIGINAL', text: '电脑丢失怎么办？' },
            { index: 1, kind: 'REWRITE', text: '办公电脑遗失处理 <img src=x>' },
            { index: 2, kind: 'REWRITE', text: '设备遗失报告流程' },
          ],
          redaction_reason: null,
        },
      },
    })

    expect(wrapper.text()).toContain('Original')
    expect(wrapper.text()).toContain('Rewrite 1')
    expect(wrapper.text()).toContain('Rewrite 2')
    expect(wrapper.text()).toContain('办公电脑遗失处理 <img src=x>')
    expect(wrapper.text()).toContain('模型改写仅用于扩展检索，不代表答案')
    expect(wrapper.find('img').exists()).toBe(false)
  })

  it('未开放时展示遮蔽原因而不伪造查询', () => {
    const wrapper = mount(QueryTraceDetails, {
      props: {
        trace: {
          exposed: false,
          items: [],
          redaction_reason: 'not_requested',
        },
      },
    })

    expect(wrapper.text()).toContain('实际检索查询未开放：not_requested')
    expect(wrapper.text()).not.toContain('Rewrite 1')
  })
})
