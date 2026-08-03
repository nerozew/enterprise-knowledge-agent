import { ApiError, API_BASE_URL, readAccessToken, UNAUTHORIZED_EVENT } from './client'
import { parseAgentEvent } from './agentValidation'
import type { AgentEvent } from './agentTypes'

export type AgentStreamState = 'CONNECTING' | 'CONNECTED' | 'RECONNECTING' | 'CLOSED'

export interface AgentStreamCursor {
  lastEventId: string | null
  lastSequence: number
}

export interface AgentStreamRecovery extends AgentStreamCursor {
  terminal: boolean
}

interface RawSseEvent {
  id?: string
  event?: string
  data: string
}

export interface StreamAgentEventsOptions extends AgentStreamCursor {
  path: string
  signal: AbortSignal
  onEvent: (event: AgentEvent) => boolean | void
  onState?: (state: AgentStreamState, retryInMs?: number) => void
  onHistoryExpired: () => Promise<AgentStreamRecovery>
  fetchImpl?: typeof fetch
  retryBaseMs?: number
  retryMaxMs?: number
  maxReconnectAttempts?: number
}

class AgentStreamHttpError extends ApiError {
  readonly retryable: boolean
  readonly retryAfterMs: number | null

  constructor(message: string, options: {
    code: string
    status: number
    retryable: boolean
    retryAfterMs: number | null
  }) {
    super(message, { code: options.code, status: options.status })
    this.retryable = options.retryable
    this.retryAfterMs = options.retryAfterMs
  }
}

function joinApiPath(path: string): string {
  return `${API_BASE_URL.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

function abortError(): DOMException {
  return new DOMException('Aborted', 'AbortError')
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError())
      return
    }
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => {
      clearTimeout(timer)
      reject(abortError())
    }, { once: true })
  })
}

function retryAfterMs(response: Response): number | null {
  const value = response.headers.get('Retry-After')
  if (!value) return null
  const seconds = Number(value)
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? Math.max(0, timestamp - Date.now()) : null
}

async function fetchError(response: Response): Promise<AgentStreamHttpError> {
  let value: unknown
  let code = `http_${response.status}`
  let message = 'Agent 事件流暂时不可用，请稍后重试'
  let explicitlyRetryable = false
  try {
    value = await response.json()
  } catch {
    value = null
  }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const envelope = value as Record<string, unknown>
    const error = envelope.error
    if (error && typeof error === 'object' && !Array.isArray(error)) {
      const detail = error as Record<string, unknown>
      if (typeof detail.code === 'string' && typeof detail.message === 'string') {
        code = detail.code
        message = detail.message
        explicitlyRetryable = detail.retryable === true
      }
    }
  }
  return new AgentStreamHttpError(message, {
    code,
    status: response.status,
    retryable: response.status === 429 || (response.status >= 500 && explicitlyRetryable),
    retryAfterMs: retryAfterMs(response),
  })
}

function retryableNetworkError(error: unknown): boolean {
  return error instanceof TypeError
    || (error instanceof DOMException && error.name === 'NetworkError')
}

function parseBlock(block: string): RawSseEvent | null {
  let id: string | undefined
  let event: string | undefined
  const data: string[] = []
  block.split('\n').forEach((sourceLine) => {
    const line = sourceLine.endsWith('\r') ? sourceLine.slice(0, -1) : sourceLine
    if (!line || line.startsWith(':')) return
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    let value = separator < 0 ? '' : line.slice(separator + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'id') id = value
    else if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  })
  if (!data.length) return null
  return {
    ...(id === undefined ? {} : { id }),
    ...(event === undefined ? {} : { event }),
    data: data.join('\n'),
  }
}

function parseData(raw: RawSseEvent): AgentEvent | null {
  if (raw.event === 'heartbeat') return null
  let data: unknown
  try {
    data = JSON.parse(raw.data) as unknown
  } catch {
    throw new ApiError('Agent 事件流包含无法识别的数据，请重新加载', {
      code: 'invalid_response',
    })
  }
  return parseAgentEvent(data, {
    ...(raw.id === undefined ? {} : { id: raw.id }),
    ...(raw.event === undefined ? {} : { type: raw.event }),
  })
}

async function consumeResponse(
  response: Response,
  signal: AbortSignal,
  consume: (event: AgentEvent) => boolean,
): Promise<boolean> {
  if (!response.body) {
    throw new ApiError('浏览器未能读取 Agent 事件流，请重新加载', { code: 'invalid_response' })
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const raw = parseBlock(block)
        const event = raw ? parseData(raw) : null
        if (event && !consume(event)) return false
        boundary = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
  return !signal.aborted
}

export async function streamAgentEvents(
  options: StreamAgentEventsOptions,
): Promise<AgentStreamCursor> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
  const retryBaseMs = options.retryBaseMs ?? 600
  const retryMaxMs = options.retryMaxMs ?? 10_000
  const maxReconnectAttempts = options.maxReconnectAttempts ?? 5
  let lastEventId = options.lastEventId
  let lastSequence = options.lastSequence
  let attempt = 0

  try {
    while (!options.signal.aborted) {
      options.onState?.(attempt === 0 ? 'CONNECTING' : 'RECONNECTING')
      const token = readAccessToken()
      const headers = new Headers({ Accept: 'text/event-stream' })
      if (token) headers.set('Authorization', `Bearer ${token}`)
      if (lastEventId) headers.set('Last-Event-ID', lastEventId)

      let nextRetryAfterMs: number | null = null
      let madeProgress = false
      try {
      const response = await fetchImpl(joinApiPath(options.path), {
        method: 'GET',
        headers,
        cache: 'no-store',
        signal: options.signal,
      })
      if (response.status === 401) {
        window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
        throw await fetchError(response)
      }
      if (response.status === 410) {
        const error = await fetchError(response)
        if (error.code !== 'event_history_expired') throw error
        const recovery = await options.onHistoryExpired()
        lastEventId = recovery.lastEventId
        lastSequence = recovery.lastSequence
        if (recovery.terminal) break
        attempt = 0
        continue
      }
      if (!response.ok) throw await fetchError(response)
      options.onState?.('CONNECTED')
      const shouldReconnect = await consumeResponse(response, options.signal, (event) => {
        if (event.sequence_no <= lastSequence) return true
        madeProgress = true
        lastSequence = event.sequence_no
        lastEventId = event.id
        return options.onEvent(event) !== false
      })
      if (!shouldReconnect) break
      } catch (error) {
        if (options.signal.aborted) break
        if (error instanceof AgentStreamHttpError) {
          if (!error.retryable) throw error
          nextRetryAfterMs = error.retryAfterMs
        } else if (!retryableNetworkError(error)) {
          throw error
        }
      }

      attempt = madeProgress ? 1 : attempt + 1
      if (attempt > maxReconnectAttempts) {
        throw new ApiError('Agent 事件流多次重连失败，请手动恢复运行状态', {
          code: 'stream_reconnect_exhausted',
        })
      }
      const retryInMs = nextRetryAfterMs ?? Math.min(
        retryMaxMs,
        retryBaseMs * (2 ** Math.min(attempt - 1, 5)),
      )
      options.onState?.('RECONNECTING', retryInMs)
      await delay(retryInMs, options.signal).catch(() => undefined)
    }
  } finally {
    options.onState?.('CLOSED')
  }

  return { lastEventId, lastSequence }
}
