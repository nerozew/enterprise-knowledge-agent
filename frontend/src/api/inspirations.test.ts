import { apiClient } from './client'
import { inspirationApi } from './inspirations'
import type { Inspiration } from './types'

describe('Workspace 灵感 API', () => {
  it('PATCH 使用 Workspace 路径并携带幂等键与 If-Match', async () => {
    const response: Inspiration = {
      id: 'inspiration-1',
      workspace_id: 'workspace-1',
      title: null,
      content: '更新后',
      summary: null,
      expanded_content: null,
      source_url: null,
      tags: [],
      version: 2,
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:01:00Z',
    }
    const patch = vi.spyOn(apiClient, 'patch').mockResolvedValue({ data: response })

    await inspirationApi.update(
      'workspace-1',
      'inspiration-1',
      { version: 1, content: '更新后' },
      'intent-key',
    )

    expect(patch).toHaveBeenCalledWith(
      '/workspaces/workspace-1/inspirations/inspiration-1',
      { version: 1, content: '更新后' },
      { headers: { 'Idempotency-Key': 'intent-key', 'If-Match': '"1"' } },
    )
  })
})
