import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { auditLogApi } from '@/api/auditLogs'
import { useWorkspaceStore } from '@/stores/workspaces'
import WorkspaceAuditView from './WorkspaceAuditView.vue'

vi.mock('@/api/auditLogs', () => ({
  auditLogApi: { list: vi.fn() },
}))

const entry = {
  id: 'audit-1',
  action: 'membership.role_changed',
  actor: { user_id: 'user-1', display_name: '管理员', role: 'OWNER' as const },
  target_type: 'workspace_member',
  target_id: 'user-2',
  result: 'SUCCESS' as const,
  metadata: { from_role: 'EDITOR', to_role: 'ADMIN' },
  request_id: 'request-1',
  created_at: '2026-07-25T10:00:00Z',
}

describe('Workspace 审计页', () => {
  afterEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
  })

  it('按游标加载更多并去重既有记录', async () => {
    vi.mocked(auditLogApi.list)
      .mockResolvedValueOnce({ items: [entry], next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({
        items: [
          entry,
          { ...entry, id: 'audit-2', action: 'membership.suspended', request_id: 'request-2' },
        ],
        next_cursor: null,
      })
    const pinia = createPinia()
    const workspaces = useWorkspaceStore(pinia)
    workspaces.items = [{
      id: 'workspace-1',
      name: '研发空间',
      kind: 'TEAM',
      status: 'ACTIVE',
      role: 'OWNER',
      member_status: 'ACTIVE',
      version: 1,
      is_default: false,
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:00:00Z',
    }]
    workspaces.select('workspace-1', 'user-1')

    const wrapper = mount(WorkspaceAuditView, {
      attachTo: document.body,
      global: {
        plugins: [pinia, ElementPlus],
        stubs: { AppShell: { template: '<div><slot /></div>' } },
      },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('变更成员角色')

    await wrapper.get('.load-more button').trigger('click')
    await flushPromises()

    expect(auditLogApi.list).toHaveBeenLastCalledWith(
      'workspace-1',
      expect.objectContaining({ cursor: 'cursor-2', page_size: 50 }),
      expect.any(AbortSignal),
    )
    expect(wrapper.findAll('.audit-timeline li')).toHaveLength(2)
    wrapper.unmount()
  })
})
