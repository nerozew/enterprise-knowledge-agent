import { mapApiError } from './client'

export function createIdempotencyKey(): string {
  return typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

interface PendingIntent {
  fingerprint: string
  key: string
}

const pendingIntents = new Map<string, PendingIntent>()

function canonicalize(value: unknown, inArray = false): string {
  if (value === null || value === undefined) return inArray ? 'null' : ''
  if (typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('幂等载荷不能包含非有限数字')
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalize(item, true)).join(',')}]`
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const entries = Object.keys(record)
      .filter((key) => record[key] !== undefined)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(record[key])}`)
    return `{${entries.join(',')}}`
  }
  throw new TypeError('幂等载荷包含不支持的数据类型')
}

function acquireIntent(scope: string, payload: unknown): PendingIntent {
  const fingerprint = canonicalize(payload)
  const existing = pendingIntents.get(scope)
  if (existing?.fingerprint === fingerprint) return existing
  const next = { fingerprint, key: createIdempotencyKey() }
  pendingIntents.set(scope, next)
  return next
}

function releaseIntent(scope: string, key: string) {
  if (pendingIntents.get(scope)?.key === key) pendingIntents.delete(scope)
}

export async function executeIdempotentIntent<T>(
  scope: string,
  payload: unknown,
  request: (idempotencyKey: string) => Promise<T>,
): Promise<T> {
  const intent = acquireIntent(scope, payload)
  try {
    const result = await request(intent.key)
    releaseIntent(scope, intent.key)
    return result
  } catch (error) {
    // 409 是服务端明确拒绝的终态；网络错误、超时和 5xx 结果不确定，保留原键供用户重试。
    if (mapApiError(error).status === 409) releaseIntent(scope, intent.key)
    throw error
  }
}

export function clearPendingIdempotencyIntents() {
  pendingIntents.clear()
}
