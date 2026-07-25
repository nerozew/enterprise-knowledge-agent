import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authApi } from '@/api/auth'
import { AUTH_STORAGE_KEY, UNAUTHORIZED_EVENT } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { createAppRouter, installUnauthorizedRedirect } from './index'

vi.mock('@/api/auth', () => ({
  authApi: { login: vi.fn(), register: vi.fn(), me: vi.fn() },
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
})
