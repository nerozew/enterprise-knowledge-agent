import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { inspirationApi } from '@/api/inspirations'
import { ApiError } from '@/api/client'
import type { Inspiration, InspirationList } from '@/api/types'
import { useInspirationStore } from './inspirations'

vi.mock('@/api/inspirations', () => ({
  inspirationApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    restore: vi.fn(),
  },
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

function inspiration(workspaceId: string): Inspiration {
  return {
    id: `item-${workspaceId}`,
    workspace_id: workspaceId,
    title: workspaceId,
    content: workspaceId,
    summary: null,
    expanded_content: null,
    source_url: null,
    tags: [],
    version: 1,
    created_at: '2026-07-25T00:00:00Z',
    updated_at: '2026-07-25T00:00:00Z',
  }
}

function page(workspaceId: string): InspirationList {
  return { items: [inspiration(workspaceId)], total: 1, page: 1, page_size: 12 }
}

describe('Workspace 作用域灵感 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('切换空间后忽略不遵守取消信号的迟到响应', async () => {
    const first = deferred<InspirationList>()
    const second = deferred<InspirationList>()
    vi.mocked(inspirationApi.list)
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)

    const store = useInspirationStore()
    store.activateWorkspace('workspace-a')
    const firstLoad = store.load()
    store.activateWorkspace('workspace-b')
    expect(store.items).toEqual([])
    const secondLoad = store.load()

    second.resolve(page('workspace-b'))
    await secondLoad
    first.resolve(page('workspace-a'))
    await firstLoad

    expect(store.workspaceId).toBe('workspace-b')
    expect(store.items.map((item) => item.workspace_id)).toEqual(['workspace-b'])
    expect(inspirationApi.list).toHaveBeenNthCalledWith(
      2,
      'workspace-b',
      expect.any(Object),
      expect.any(AbortSignal),
    )
  })

  it('Workspace 越权被隐藏为 404 后立即清除旧内容', async () => {
    vi.mocked(inspirationApi.list).mockRejectedValue(
      new ApiError('资源不存在', { code: 'resource_not_found', status: 404 }),
    )
    const store = useInspirationStore()
    store.activateWorkspace('workspace-a')
    store.items = [inspiration('workspace-a')]
    store.total = 1

    await store.load()

    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
    expect(store.errorMessage).toBe('资源不存在')
  })

  it('创建与 PATCH 在网络错误重试时复用原操作意图键', async () => {
    const store = useInspirationStore()
    store.activateWorkspace('workspace-a')
    const item = inspiration('workspace-a')
    vi.mocked(inspirationApi.create)
      .mockRejectedValueOnce(new ApiError('网络异常', { code: 'network_error' }))
      .mockResolvedValueOnce(item)
    vi.mocked(inspirationApi.list).mockResolvedValue(page('workspace-a'))

    await expect(store.create({ content: '同一条灵感' })).rejects.toBeDefined()
    await store.create({ content: '同一条灵感' })
    expect(vi.mocked(inspirationApi.create).mock.calls[1]?.[2])
      .toBe(vi.mocked(inspirationApi.create).mock.calls[0]?.[2])

    store.items = [item]
    vi.mocked(inspirationApi.update)
      .mockRejectedValueOnce(new ApiError('请求超时', { code: 'request_timeout' }))
      .mockResolvedValueOnce({ ...item, content: '更新后', version: 2 })
    const patch = { version: 1, content: '更新后' }
    await expect(store.update(item.id, patch)).rejects.toBeDefined()
    await store.update(item.id, patch)
    expect(vi.mocked(inspirationApi.update).mock.calls[1]?.[3])
      .toBe(vi.mocked(inspirationApi.update).mock.calls[0]?.[3])
  })
})
