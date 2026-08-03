import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { documentApi } from '@/api/documents'
import type { WorkspaceRole, WorkspaceSummary } from '@/api/types'
import { useWorkspaceStore } from '@/stores/workspaces'
import KnowledgeView from './KnowledgeView.vue'

vi.mock('@/api/documents', () => ({
  documentApi: {
    list: vi.fn(),
    versions: vi.fn(),
    upload: vi.fn(),
    job: vi.fn(),
    retry: vi.fn(),
    cancel: vi.fn(),
  },
}))

function workspace(role: WorkspaceRole): WorkspaceSummary {
  return {
    id: 'workspace-1',
    name: '研发空间',
    kind: 'TEAM',
    status: 'ACTIVE',
    role,
    member_status: 'ACTIVE',
    version: 1,
    is_default: false,
    created_at: '2026-07-25T00:00:00Z',
    updated_at: '2026-07-25T00:00:00Z',
  }
}

async function render(role: WorkspaceRole) {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/w/:workspaceId/knowledge',
        name: 'workspace-knowledge',
        component: KnowledgeView,
      },
      {
        path: '/w/:workspaceId/ask',
        name: 'workspace-ask',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push('/w/workspace-1/knowledge')
  const workspaces = useWorkspaceStore(pinia)
  workspaces.items = [workspace(role)]
  workspaces.select('workspace-1', 'user-1')

  const wrapper = mount(KnowledgeView, {
    global: {
      plugins: [pinia, router, ElementPlus],
      stubs: {
        AppShell: { template: '<main><slot /></main>' },
        DocumentUploadPanel: true,
        DocumentCard: true,
      },
    },
  })
  await flushPromises()
  vi.clearAllMocks()
  return wrapper
}

describe('KnowledgeView 文档筛选', () => {
  beforeEach(() => {
    vi.mocked(documentApi.list).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 12,
    })
    vi.mocked(documentApi.versions).mockResolvedValue([])
  })

  it('先筛选 FAILED 再清空时提交完整快照，不保留旧参数', async () => {
    const wrapper = await render('EDITOR')
    const selects = wrapper.findAllComponents({ name: 'ElSelect' })

    await wrapper.get('input[aria-label="搜索文档标题"]').setValue('制度')
    selects[0]!.vm.$emit('update:modelValue', 'MARKDOWN')
    selects[1]!.vm.$emit('update:modelValue', 'FAILED')
    await wrapper.vm.$nextTick()
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(documentApi.list).toHaveBeenLastCalledWith(
      'workspace-1',
      expect.objectContaining({
        q: '制度',
        source_format: 'MARKDOWN',
        version_status: 'FAILED',
        page: 1,
      }),
      expect.any(AbortSignal),
    )

    await wrapper.get('input[aria-label="搜索文档标题"]').setValue('')
    selects[0]!.vm.$emit('update:modelValue', '')
    selects[1]!.vm.$emit('update:modelValue', '')
    await wrapper.vm.$nextTick()
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(documentApi.list).toHaveBeenLastCalledWith(
      'workspace-1',
      expect.objectContaining({
        q: undefined,
        source_format: undefined,
        version_status: undefined,
        page: 1,
      }),
      expect.any(AbortSignal),
    )
    const clearedQuery = vi.mocked(documentApi.list).mock.calls.at(-1)?.[1]
    expect(clearedQuery?.q).not.toBe('制度')
    expect(clearedQuery?.source_format).not.toBe('MARKDOWN')
    expect(clearedQuery?.version_status).not.toBe('FAILED')
    wrapper.unmount()
  })

  it('VIEWER 隐藏非发布状态选项并固定查询发布版本', async () => {
    const wrapper = await render('VIEWER')

    expect(wrapper.find('[aria-label="按状态筛选"]').exists()).toBe(false)
    const optionValues = wrapper
      .findAllComponents({ name: 'ElOption' })
      .map((option) => option.props('value'))
    expect(optionValues).not.toContain('PUBLISHING')
    expect(optionValues).not.toContain('FAILED')

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(documentApi.list).toHaveBeenLastCalledWith(
      'workspace-1',
      expect.objectContaining({ version_status: 'PUBLISHED' }),
      expect.any(AbortSignal),
    )
    wrapper.unmount()
  })
})
