import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type { ApiErrorPayload } from './types'

export const AUTH_STORAGE_KEY = 'inspiration-assistant.auth'
export const UNAUTHORIZED_EVENT = 'auth:unauthorized'
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export class ApiError extends Error {
  readonly code: string
  readonly status: number | undefined
  readonly details: unknown[]
  readonly requestId: string | undefined

  constructor(message: string, options: {
    code: string
    status?: number | undefined
    details?: unknown[] | undefined
    requestId?: string | undefined
  }) {
    super(message)
    this.name = 'ApiError'
    this.code = options.code
    this.status = options.status
    this.details = options.details ?? []
    this.requestId = options.requestId
  }
}

interface StoredAuth {
  token?: unknown
}

export function readAccessToken(): string | null {
  const raw = localStorage.getItem(AUTH_STORAGE_KEY)
  if (!raw) return null
  try {
    const stored = JSON.parse(raw) as StoredAuth
    return typeof stored.token === 'string' ? stored.token : null
  } catch {
    localStorage.removeItem(AUTH_STORAGE_KEY)
    return null
  }
}

function serializeParams(params: Record<string, unknown>): string {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, String(item)))
      return
    }
    search.append(key, String(value))
  })
  return search.toString()
}

function isApiErrorPayload(value: unknown): value is ApiErrorPayload {
  if (!value || typeof value !== 'object') return false
  const payload = value as Record<string, unknown>
  const error = payload.error
  return Boolean(error && typeof error === 'object' && typeof (error as Record<string, unknown>).message === 'string')
}

export function mapApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<unknown>
    const status = axiosError.response?.status
    const data = axiosError.response?.data
    if (isApiErrorPayload(data)) {
      return new ApiError(data.error.message, {
        code: data.error.code,
        status,
        details: data.error.details,
        requestId: data.request_id,
      })
    }
    if (axiosError.code === 'ECONNABORTED') {
      return new ApiError('请求超时，请稍后重试', { code: 'request_timeout', status })
    }
    if (!axiosError.response) {
      return new ApiError('无法连接服务器，请检查网络后重试', { code: 'network_error' })
    }
    return new ApiError('服务暂时不可用，请稍后重试', { code: `http_${status ?? 'unknown'}`, status })
  }
  return new ApiError('发生未知错误，请稍后重试', { code: 'unknown_error' })
}

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: Number(import.meta.env.VITE_REQUEST_TIMEOUT_MS || 15_000),
  // headers: { 'Content-Type': 'application/json' },
  paramsSerializer: { serialize: serializeParams },
})

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = readAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const mapped = mapApiError(error)
    if (mapped.status === 401) window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
    return Promise.reject(mapped)
  },
)
