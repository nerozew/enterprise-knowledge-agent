import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { authApi } from '@/api/auth'
import { AUTH_STORAGE_KEY } from '@/api/client'
import { useAuthStore } from './auth'

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

describe('认证 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('登录成功后持久化会话', async () => {
    vi.mocked(authApi.login).mockResolvedValue({ access_token: 'jwt-token', user })
    const store = useAuthStore()
    await store.login({ email: user.email, password: 'password123' })

    expect(store.isAuthenticated).toBe(true)
    expect(JSON.parse(localStorage.getItem(AUTH_STORAGE_KEY) ?? '{}')).toMatchObject({ token: 'jwt-token' })
  })

  it('初始化时即使有本地用户快照也会向服务端验证 token', async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 'old-token', user }))
    setActivePinia(createPinia())
    const store = useAuthStore()
    vi.mocked(authApi.me).mockResolvedValue({ ...user, display_name: '新昵称' })
    expect(store.initialized).toBe(false)
    await store.initialize()

    expect(authApi.me).toHaveBeenCalledOnce()
    expect(store.initialized).toBe(true)
    expect(store.user?.display_name).toBe('新昵称')
  })

  it('持久化 token 已过期时初始化会清理会话', async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 'expired-token', user }))
    setActivePinia(createPinia())
    vi.mocked(authApi.me).mockRejectedValue(new Error('unauthorized'))
    const store = useAuthStore()

    await store.initialize()

    expect(store.initialized).toBe(true)
    expect(store.isAuthenticated).toBe(false)
    expect(store.token).toBeNull()
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()
  })

  it('退出时清理会话', () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 'jwt-token', user }))
    setActivePinia(createPinia())
    const store = useAuthStore()
    store.logout()

    expect(store.isAuthenticated).toBe(false)
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull()
  })
})
