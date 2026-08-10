import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './client'
import { streamAgentEvents } from './agentSse'

function response(body: string, status = 200): Response {
  return new globalThis.Response(body, {
    status,
    headers: { 'Content-Type': status === 200 ? 'text/event-stream' : 'application/json' },
  })
}

describe('Agent SSE', () => {
  it('携带认证和 Last-Event-ID，并按 seq 去重后在终态关闭', async () => {
    localStorage.setItem('inspiration-assistant.auth', JSON.stringify({ token: 'secret-token' }))
    const body = [
      'id: 5\nevent: route.selected\ndata: {"run_id":"run-1","seq":5,"timestamp":"2026-08-03T00:00:05Z","payload":{}}',
      'id: 5\nevent: route.selected\ndata: {"run_id":"run-1","seq":5,"timestamp":"2026-08-03T00:00:05Z","payload":{}}',
      'id: 6\nevent: run.completed\ndata: {"run_id":"run-1","seq":6,"timestamp":"2026-08-03T00:00:06Z","payload":{}}',
      '',
    ].join('\n\n')
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(body))
    const received: number[] = []

    const cursor = await streamAgentEvents({
      path: '/workspaces/workspace-1/agent-runs/run-1/events',
      signal: new AbortController().signal,
      lastEventId: '4',
      lastSequence: 4,
      fetchImpl,
      onHistoryExpired: vi.fn(),
      onEvent: (event) => {
        received.push(event.sequence_no)
        return event.type !== 'run.completed'
      },
    })

    const request = fetchImpl.mock.calls[0]
    const headers = new globalThis.Headers(request?.[1]?.headers)
    expect(headers.get('Authorization')).toBe('Bearer secret-token')
    expect(headers.get('Last-Event-ID')).toBe('4')
    expect(received).toEqual([5, 6])
    expect(cursor).toEqual({ lastEventId: '6', lastSequence: 6 })
  })

  it('终态事件会主动取消仍保持打开的响应流', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode([
          'id: 1\nevent: run.completed\ndata: {"run_id":"run-1","seq":1,"timestamp":"2026-08-03T00:00:01Z","payload":{}}',
          '',
        ].join('\n\n')))
      },
      cancel,
    })
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }))

    await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      onHistoryExpired: vi.fn(),
      onEvent: () => false,
    })

    expect(cancel).toHaveBeenCalledOnce()
  })

  it('clean EOF 先交给事实刷新回调并确定性停流，不自动重连', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([
      'id: 1\nevent: route.selected\ndata: {"run_id":"run-1","seq":1,"timestamp":"2026-08-03T00:00:01Z","payload":{}}',
      '',
    ].join('\n\n')))
    const onCleanClose = vi.fn()

    const cursor = await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      onHistoryExpired: vi.fn(),
      onCleanClose,
      onEvent: vi.fn(() => true),
    })

    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(onCleanClose).toHaveBeenCalledOnce()
    expect(onCleanClose).toHaveBeenCalledWith({ lastEventId: '1', lastSequence: 1 })
    expect(cursor).toEqual({ lastEventId: '1', lastSequence: 1 })
  })

  it.each([
    'authorization_revoked',
    'token_expired',
    'resource_unavailable',
  ] as const)('stream.closed(%s) 不推进游标并确定性停流', async (reason) => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([
      `event: stream.closed\ndata: {"run_id":"run-1","reason":"${reason}","retryable":false}`,
      '',
    ].join('\n\n')))
    const onClosed = vi.fn()
    const onCleanClose = vi.fn()
    const onEvent = vi.fn()

    const cursor = await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: '4',
      lastSequence: 4,
      fetchImpl,
      onHistoryExpired: vi.fn(),
      onCleanClose,
      onClosed,
      onEvent,
    })

    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(onEvent).not.toHaveBeenCalled()
    expect(onCleanClose).not.toHaveBeenCalled()
    expect(onClosed).toHaveBeenCalledWith({ runId: 'run-1', reason })
    expect(cursor).toEqual({ lastEventId: '4', lastSequence: 4 })
  })

  it('token_expired 控制帧触发统一登录态清理事件', async () => {
    const unauthorized = vi.fn()
    window.addEventListener('auth:unauthorized', unauthorized)
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([
      'event: stream.closed\ndata: {"run_id":"run-1","reason":"token_expired","retryable":false}',
      '',
    ].join('\n\n')))

    await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      onHistoryExpired: vi.fn(),
      onEvent: vi.fn(),
    })

    expect(unauthorized).toHaveBeenCalledOnce()
    expect(fetchImpl).toHaveBeenCalledOnce()
    window.removeEventListener('auth:unauthorized', unauthorized)
  })

  it('拒绝携带 id/seq 或 retryable=true 的 stream.closed 帧', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([
      'id: 5\nevent: stream.closed\ndata: {"run_id":"run-1","reason":"resource_unavailable","retryable":true,"seq":5}',
      '',
    ].join('\n\n')))

    await expect(streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: '4',
      lastSequence: 4,
      fetchImpl,
      onHistoryExpired: vi.fn(),
      onEvent: vi.fn(),
    })).rejects.toMatchObject({ code: 'invalid_response' })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('历史过期时使用快照游标恢复，终态不再重连', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(JSON.stringify({
      error: { code: 'event_history_expired', message: '事件历史已过期', details: [] },
    }), 410))
    const recover = vi.fn().mockResolvedValue({
      lastEventId: '19',
      lastSequence: 19,
      terminal: true,
    })

    const cursor = await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: '2',
      lastSequence: 2,
      fetchImpl,
      onHistoryExpired: recover,
      onEvent: vi.fn(),
    })

    expect(recover).toHaveBeenCalledOnce()
    expect(fetchImpl).toHaveBeenCalledOnce()
    expect(cursor).toEqual({ lastEventId: '19', lastSequence: 19 })
  })

  it.each([401, 403, 404, 409, 422])('确定性 HTTP %s 不重连', async (status) => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(JSON.stringify({
      error: { code: `error_${status}`, message: '请求被确定性拒绝', details: [] },
    }), status))

    await expect(streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      retryBaseMs: 0,
      onHistoryExpired: vi.fn(),
      onEvent: vi.fn(),
    })).rejects.toBeInstanceOf(ApiError)

    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it.each([
    { status: 429, retryable: false },
    { status: 503, retryable: true },
  ])('对 HTTP $status 可重试响应重连', async ({ status, retryable }) => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(JSON.stringify({
        error: { code: 'temporarily_unavailable', message: '稍后重试', details: [], retryable },
      }), status))
      .mockResolvedValueOnce(response([
        'id: 1\nevent: run.completed\ndata: {"run_id":"run-1","seq":1,"timestamp":"2026-08-03T00:00:01Z","payload":{}}',
        '',
      ].join('\n\n')))

    const cursor = await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      retryBaseMs: 0,
      onHistoryExpired: vi.fn(),
      onEvent: (event) => event.type !== 'run.completed',
    })

    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(cursor).toEqual({ lastEventId: '1', lastSequence: 1 })
  })

  it('未显式标记 retryable 的 5xx 不重连', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response(JSON.stringify({
      error: { code: 'internal_error', message: '内部错误', details: [] },
    }), 503))

    await expect(streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      retryBaseMs: 0,
      onHistoryExpired: vi.fn(),
      onEvent: vi.fn(),
    })).rejects.toMatchObject({ code: 'internal_error', status: 503 })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('网络错误仅做有界重连，耗尽后要求手动恢复', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('network down'))

    await expect(streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: null,
      lastSequence: 0,
      fetchImpl,
      retryBaseMs: 0,
      maxReconnectAttempts: 2,
      onHistoryExpired: vi.fn(),
      onEvent: vi.fn(),
    })).rejects.toMatchObject({ code: 'stream_reconnect_exhausted' })
    expect(fetchImpl).toHaveBeenCalledTimes(3)
  })

  it('检测到事件序号缺口时从快照重建游标', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response([
      'id: 7\nevent: route.selected\ndata: {"run_id":"run-1","seq":7,"timestamp":"2026-08-03T00:00:07Z","payload":{}}',
      '',
    ].join('\n\n')))
    const recover = vi.fn().mockResolvedValue({
      lastEventId: '9', lastSequence: 9, terminal: true,
    })
    const onEvent = vi.fn()

    const cursor = await streamAgentEvents({
      path: '/events',
      signal: new AbortController().signal,
      lastEventId: '5',
      lastSequence: 5,
      fetchImpl,
      onHistoryExpired: recover,
      onEvent,
    })

    expect(onEvent).not.toHaveBeenCalled()
    expect(recover).toHaveBeenCalledOnce()
    expect(cursor).toEqual({ lastEventId: '9', lastSequence: 9 })
  })
})
