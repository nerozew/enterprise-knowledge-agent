import { AxiosError } from 'axios'
import { describe, expect, it, vi } from 'vitest'
import { apiClient, AUTH_STORAGE_KEY, mapApiError, UNAUTHORIZED_EVENT } from './client'

describe('API 客户端', () => {
  it('将统一错误响应映射为可理解的 ApiError', () => {
    const error = new AxiosError('Conflict', 'ERR_BAD_RESPONSE', undefined, undefined, {
      status: 409,
      statusText: 'Conflict',
      headers: {},
      config: { headers: {} },
      data: {
        error: { code: 'version_conflict', message: '内容已经更新', details: [] },
        request_id: 'request-1',
      },
    })

    const mapped = mapApiError(error)
    expect(mapped.status).toBe(409)
    expect(mapped.code).toBe('version_conflict')
    expect(mapped.message).toBe('内容已经更新')
    expect(mapped.requestId).toBe('request-1')
  })

  it('为请求注入持久化 token', async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 'test-token', user: { id: 'u1' } }))
    let authorization: unknown
    const originalAdapter = apiClient.defaults.adapter
    apiClient.defaults.adapter = async (config) => {
      authorization = config.headers.Authorization
      return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
    }

    await apiClient.get('/test')
    apiClient.defaults.adapter = originalAdapter
    expect(authorization).toBe('Bearer test-token')
  })

  it('401 时广播会话失效事件', async () => {
    const listener = vi.fn()
    window.addEventListener(UNAUTHORIZED_EVENT, listener)
    const originalAdapter = apiClient.defaults.adapter
    apiClient.defaults.adapter = async (config) => {
      throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, {
        status: 401,
        statusText: 'Unauthorized',
        headers: {},
        config,
        data: { error: { code: 'unauthorized', message: '请重新登录', details: [] }, request_id: 'r2' },
      })
    }

    await expect(apiClient.get('/private')).rejects.toMatchObject({ status: 401 })
    apiClient.defaults.adapter = originalAdapter
    expect(listener).toHaveBeenCalledOnce()
    window.removeEventListener(UNAUTHORIZED_EVENT, listener)
  })
})
