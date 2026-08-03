import { describe, expect, it, vi } from 'vitest'
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
})
