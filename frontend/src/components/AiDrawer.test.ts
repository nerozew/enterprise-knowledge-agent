import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import { inspirationApi } from '@/api/inspirations'
import AiDrawer from './AiDrawer.vue'

vi.mock('@/api/inspirations', () => ({
  inspirationApi: { generate: vi.fn() },
}))

const inspiration = {
  id: 'inspiration-1',
  title: '新的产品点子',
  content: '把零散的想法整理成能够行动的计划。',
  summary: null,
  expanded_content: null,
  source_url: null,
  tags: [],
  version: 3,
  created_at: '2026-07-17T00:00:00Z',
  updated_at: '2026-07-17T00:00:00Z',
}

describe('AiDrawer', () => {
  it('严格渲染 AI 响应并只在点击保存后发出 PATCH 载荷', async () => {
    vi.mocked(inspirationApi.generate).mockResolvedValue({
      generation_id: 'generation-1',
      operation: 'summary',
      mode: 'fallback',
      result: '将零散想法整理为行动计划。',
      warning: '未配置外部模型，当前为本地降级结果',
      created_at: '2026-07-17T00:01:00Z',
    })
    const wrapper = mount(AiDrawer, {
      attachTo: document.body,
      props: { modelValue: true, inspiration, saving: false },
      global: { plugins: [ElementPlus] },
    })
    await flushPromises()

    const generateButton = Array.from(document.body.querySelectorAll('button')).find((button) => button.textContent?.includes('开始生成'))
    generateButton?.click()
    await flushPromises()

    expect(document.body.textContent).toContain('将零散想法整理为行动计划。')
    expect(document.body.textContent).toContain('本地降级')
    expect(wrapper.emitted('save')).toBeUndefined()

    const saveButton = Array.from(document.body.querySelectorAll('button')).find((button) => button.textContent?.includes('保存到灵感'))
    saveButton?.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('save')?.[0]).toEqual([
      'inspiration-1',
      { version: 3, summary: '将零散想法整理为行动计划。' },
    ])
    wrapper.unmount()
  })
})
