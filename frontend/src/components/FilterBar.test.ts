import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { afterEach, describe, expect, it, vi } from 'vitest'
import FilterBar from './FilterBar.vue'

describe('FilterBar', () => {
  afterEach(() => vi.useRealTimers())

  it('发送防抖关键词、标签和匹配方式', async () => {
    vi.useFakeTimers()
    const wrapper = mount(FilterBar, {
      props: {
        query: { page: 2, page_size: 12 },
        tags: [{ id: 'tag-1', name: '工作', color: '#409EFF', version: 1 }],
      },
      global: { plugins: [ElementPlus] },
    })

    await wrapper.find('input[aria-label="搜索灵感"]').setValue('产品想法')
    vi.advanceTimersByTime(350)
    await wrapper.vm.$nextTick()

    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    selects[0]?.vm.$emit('update:modelValue', ['tag-1'])
    selects[1]?.vm.$emit('update:modelValue', 'all')
    await wrapper.vm.$nextTick()

    const changes = wrapper.emitted('change')?.map((args) => args[0])
    expect(changes).toContainEqual({ q: '产品想法', page: 1 })
    expect(changes).toContainEqual({ tag_ids: ['tag-1'], page: 1 })
    expect(changes).toContainEqual({ tag_match: 'all', page: 1 })
    wrapper.unmount()
  })
})
