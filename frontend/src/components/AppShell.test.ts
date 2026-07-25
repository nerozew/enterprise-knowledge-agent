import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import type { WorkspaceRole, WorkspaceSummary } from '@/api/types'
import { useWorkspaceStore } from '@/stores/workspaces'
import AppShell from './AppShell.vue'

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
      { path: '/w/:workspaceId', name: 'workspace-home', component: { template: '<div />' } },
      { path: '/w/:workspaceId/members', name: 'workspace-members', component: { template: '<div />' } },
      { path: '/w/:workspaceId/audit', name: 'workspace-audit', component: { template: '<div />' } },
      { path: '/workspace', name: 'workspace-resolve', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
  await router.push('/w/workspace-1')
  const store = useWorkspaceStore(pinia)
  store.items = [workspace(role)]
  store.select('workspace-1', 'user-1')
  return mount(AppShell, {
    global: {
      plugins: [pinia, router, ElementPlus],
      stubs: { WorkspaceSwitcher: true },
    },
  })
}

describe('AppShell Workspace 权限入口', () => {
  it.each(['OWNER', 'ADMIN'] as const)('%s 可以看到成员和审计入口', async (role) => {
    const wrapper = await render(role)
    expect(wrapper.text()).toContain('成员')
    expect(wrapper.text()).toContain('审计')
    wrapper.unmount()
  })

  it.each(['EDITOR', 'VIEWER'] as const)('%s 不显示管理入口', async (role) => {
    const wrapper = await render(role)
    expect(wrapper.find('.workspace-nav').exists()).toBe(false)
    wrapper.unmount()
  })
})
