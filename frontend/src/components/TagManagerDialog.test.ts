import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { tagApi } from '@/api/tags'
import TagManagerDialog from './TagManagerDialog.vue'
import { useTagStore } from '@/stores/tags'

vi.mock('@/api/tags', () => ({
  tagApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}))

describe('TagManagerDialog', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
  })

  it('renders the non-zero usage count returned by the management API', async () => {
    vi.mocked(tagApi.list).mockResolvedValue([
      { id: 'tag-1', workspace_id: 'workspace-1', name: '动漫', color: null, version: 1, usage_count: 2 },
    ])

    const pinia = createPinia()
    useTagStore(pinia).activateWorkspace('workspace-1')
    const wrapper = mount(TagManagerDialog, {
      attachTo: document.body,
      props: { modelValue: false },
      global: {
        plugins: [pinia, ElementPlus],
        stubs: { ElDialog: { template: '<div><slot /></div>' } },
      },
    })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    expect(wrapper.get('.usage-count').text()).toContain('2 条灵感')
    wrapper.unmount()
  })
})
