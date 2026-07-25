import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/api/auth'
import { AUTH_STORAGE_KEY, mapApiError } from '@/api/client'
import { clearPendingIdempotencyIntents } from '@/api/idempotency'
import type { LoginInput, RegisterInput, User } from '@/api/types'
import { useWorkspaceStore } from './workspaces'

interface AuthSnapshot {
  token: string
  user: User
}

function loadSnapshot(): AuthSnapshot | null {
  const raw = localStorage.getItem(AUTH_STORAGE_KEY)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<AuthSnapshot>
    if (typeof value.token !== 'string' || !value.user || typeof value.user.id !== 'string') return null
    return value as AuthSnapshot
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const snapshot = loadSnapshot()
  const token = ref<string | null>(snapshot?.token ?? null)
  const user = ref<User | null>(snapshot?.user ?? null)
  const initialized = ref(false)
  const initializing = ref(false)
  const errorMessage = ref('')
  const isAuthenticated = computed(() => initialized.value && Boolean(token.value && user.value))
  let initializationPromise: Promise<void> | null = null

  function persist() {
    if (token.value && user.value) {
      localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: token.value, user: user.value }))
    } else {
      localStorage.removeItem(AUTH_STORAGE_KEY)
    }
  }

  function acceptSession(accessToken: string, nextUser: User) {
    if (user.value?.id && user.value.id !== nextUser.id) useWorkspaceStore().clear()
    token.value = accessToken
    user.value = nextUser
    initialized.value = true
    errorMessage.value = ''
    persist()
  }

  function clearSession() {
    token.value = null
    user.value = null
    errorMessage.value = ''
    persist()
    clearPendingIdempotencyIntents()
    useWorkspaceStore().clear()
  }

  function logout() {
    clearSession()
    initialized.value = true
  }

  async function login(input: LoginInput) {
    errorMessage.value = ''
    try {
      const response = await authApi.login(input)
      acceptSession(response.access_token, response.user)
    } catch (error) {
      errorMessage.value = mapApiError(error).message
      throw error
    }
  }

  async function register(input: RegisterInput) {
    errorMessage.value = ''
    try {
      const response = await authApi.register(input)
      acceptSession(response.access_token, response.user)
    } catch (error) {
      errorMessage.value = mapApiError(error).message
      throw error
    }
  }

  async function initialize(): Promise<void> {
    if (initialized.value) return
    if (initializationPromise) return initializationPromise

    const promise = (async () => {
      initializing.value = true
      try {
        if (!token.value) {
          user.value = null
          return
        }
        // 本地用户快照只用于减少界面闪烁，应用启动时始终向服务端验证 token。
        user.value = await authApi.me()
        persist()
      } catch {
        clearSession()
      } finally {
        initializing.value = false
        initialized.value = true
        initializationPromise = null
      }
    })()
    initializationPromise = promise
    return promise
  }

  return {
    token,
    user,
    initialized,
    initializing,
    errorMessage,
    isAuthenticated,
    login,
    register,
    initialize,
    logout,
  }
})
