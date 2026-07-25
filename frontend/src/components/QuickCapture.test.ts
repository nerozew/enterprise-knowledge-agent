import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import QuickCapture from './QuickCapture.vue'

describe('QuickCapture', () => {
  it('空正文不会提交', async () => {
    const wrapper = mount(QuickCapture, { props: { tags: [], busy: false }, global: { plugins: [ElementPlus] } })
    await wrapper.find('form').trigger('submit')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(wrapper.emitted('submit')).toBeUndefined()
    wrapper.unmount()
  })

  it('提交清理后的正文与可选标题', async () => {
    const wrapper = mount(QuickCapture, { props: { tags: [], busy: false }, global: { plugins: [ElementPlus] } })
    await wrapper.find('textarea').setValue('  一个新的想法  ')
    const textInputs = wrapper.findAll('input[type="text"]')
    await textInputs[0]?.setValue('  周末计划  ')
    await wrapper.find('form').trigger('submit')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({ title: '周末计划', content: '一个新的想法' })
    wrapper.unmount()
  })
})
