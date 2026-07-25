import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authApi } from '@/api/auth'
import { workspaceApi } from '@/api/workspaces'
import { AUTH_STORAGE_KEY, UNAUTHORIZED_EVENT } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useInspirationStore } from '@/stores/inspirations'
import { useWorkspaceStore } from '@/stores/workspaces'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'
import {
  createAppRouter,
  installUnauthorizedRedirect,
  installWorkspaceAccessRecovery,
} from './index'

vi.mock('@/api/auth', () => ({
  authApi: { login: vi.fn(), register: vi.fn(), me: vi.fn() },
}))
vi.mock('@/api/workspaces', () => ({
  workspaceApi: { list: vi.fn(), create: vi.fn() },
}))

const user = {
  id: 'user-1',
  email: 'user@example.com',
  display_name: '拾光者',
  is_active: true,
  created_at: '2026-07-17T00:00:00Z',
}

describe('认证路由', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 'valid-token', user }))
    vi.mocked(authApi.me).mockResolvedValue(user)
    vi.mocked(workspaceApi.list).mockResolvedValue({
      items: [{
        id: 'personal',
        name: '个人空间',
        kind: 'PERSONAL',
        status: 'ACTIVE',
        role: 'OWNER',
        member_status: 'ACTIVE',
        version: 1,
        is_default: true,
        created_at: '2026-07-25T00:00:00Z',
        updated_at: '2026-07-25T00:00:00Z',
      }],
      total: 1,
      page: 1,
      page_size: 100,
    })
  })

  it('运行期收到 401 后清理会话并保留原地址跳转登录', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/workspace')
    await router.isReady()
    const removeListener = installUnauthorizedRedirect(router)

    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/workspace')
    expect(useAuthStore().isAuthenticated).toBe(false)
    removeListener()
  })

  it('直接访问无权 Workspace 时不保留目标 ID 并回落默认空间', async () => {
    const router = createAppRouter(createMemoryHistory())
    await router.push('/w/secret-workspace')
    await router.isReady()

    expect(router.currentRoute.value.name).toBe('workspace-home')
    expect(router.currentRoute.value.params.workspaceId).toBe('personal')
    expect(router.currentRoute.value.query.unavailable).toBe('1')
    expect(router.currentRoute.value.fullPath).not.toContain('secret-workspace')
  })

  it('运行期撤权会清缓存、强刷列表并 replace 到默认个人空间', async () => {
    const personal = {
      id: 'personal',
      name: '个人空间',
      kind: 'PERSONAL' as const,
      status: 'ACTIVE' as const,
      role: 'OWNER' as const,
      member_status: 'ACTIVE' as const,
      version: 1,
      is_default: true,
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:00:00Z',
    }
    const team = {
      ...personal,
      id: 'team',
      name: '团队空间',
      kind: 'TEAM' as const,
      role: 'EDITOR' as const,
      is_default: false,
    }
    const page = { items: [personal, team], total: 2, page: 1, page_size: 100 }
    vi.mocked(workspaceApi.list).mockResolvedValueOnce(page).mockResolvedValueOnce(page)
    const router = createAppRouter(createMemoryHistory())
    await router.push('/w/team')
    await router.isReady()
    const inspirationStore = useInspirationStore()
    inspirationStore.items = [{
      id: 'secret',
      workspace_id: 'team',
      title: null,
      content: '撤权前内容',
      summary: null,
      expanded_content: null,
      source_url: null,
      tags: [],
      version: 1,
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:00:00Z',
    }]
    const removeListener = installWorkspaceAccessRecovery(router)

    reportWorkspaceAccessRevoked('team')
    reportWorkspaceAccessRevoked('team')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(workspaceApi.list).toHaveBeenCalledTimes(2)
    expect(useWorkspaceStore().activeWorkspaceId).toBe('personal')
    expect(inspirationStore.items).toEqual([])
    expect(router.currentRoute.value.name).toBe('workspace-home')
    expect(router.currentRoute.value.params.workspaceId).toBe('personal')
    expect(router.currentRoute.value.query.revoked).toBe('1')
    removeListener()
  })
})
