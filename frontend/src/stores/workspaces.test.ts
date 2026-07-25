import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { workspaceApi } from '@/api/workspaces'
import { ApiError } from '@/api/client'
import type { WorkspaceSummary } from '@/api/types'
import { useInspirationStore } from './inspirations'
import { useTagStore } from './tags'
import { useWorkspaceStore } from './workspaces'

vi.mock('@/api/workspaces', () => ({
  workspaceApi: {
    list: vi.fn(),
    create: vi.fn(),
  },
}))

function workspace(
  id: string,
  options: Partial<Pick<WorkspaceSummary, 'kind' | 'role' | 'is_default' | 'member_status'>> = {},
): WorkspaceSummary {
  return {
    id,
    name: id === 'personal' ? '个人空间' : '研发空间',
    kind: options.kind ?? 'TEAM',
    status: 'ACTIVE',
    role: options.role ?? 'EDITOR',
    member_status: options.member_status ?? 'ACTIVE',
    version: 1,
    is_default: options.is_default ?? false,
    created_at: '2026-07-25T00:00:00Z',
    updated_at: '2026-07-25T00:00:00Z',
  }
}

describe('Workspace store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('优先恢复当前用户仍可访问的最后 Workspace', async () => {
    localStorage.setItem('inspiration-assistant.last-workspace.user-1', 'team')
    vi.mocked(workspaceApi.list).mockResolvedValue({
      items: [
        workspace('personal', { kind: 'PERSONAL', role: 'OWNER', is_default: true }),
        workspace('team'),
      ],
      total: 2,
      page: 1,
      page_size: 100,
    })

    const store = useWorkspaceStore()
    await store.initialize('user-1')

    expect(store.activeWorkspaceId).toBe('team')
    expect(useInspirationStore().workspaceId).toBe('team')
    expect(useTagStore().workspaceId).toBe('team')
  })

  it('最后 Workspace 失效时回落 is_default 并清除旧内容缓存', async () => {
    localStorage.setItem('inspiration-assistant.last-workspace.user-1', 'revoked')
    vi.mocked(workspaceApi.list).mockResolvedValue({
      items: [
        workspace('personal', { kind: 'PERSONAL', role: 'OWNER', is_default: true }),
        workspace('revoked', { member_status: 'REMOVED' }),
      ],
      total: 2,
      page: 1,
      page_size: 100,
    })
    const inspirationStore = useInspirationStore()
    const tagStore = useTagStore()
    inspirationStore.activateWorkspace('old')
    tagStore.activateWorkspace('old')
    inspirationStore.items = [{
      id: 'old-item',
      workspace_id: 'old',
      title: null,
      content: '旧内容',
      summary: null,
      expanded_content: null,
      source_url: null,
      tags: [],
      version: 1,
      created_at: '2026-07-25T00:00:00Z',
      updated_at: '2026-07-25T00:00:00Z',
    }]
    tagStore.items = [{
      id: 'old-tag',
      workspace_id: 'old',
      name: '旧标签',
      color: null,
      version: 1,
      usage_count: 1,
    }]

    const store = useWorkspaceStore()
    await store.initialize('user-1')

    expect(store.activeWorkspaceId).toBe('personal')
    expect(store.recoveryMessage).toContain('默认个人空间')
    expect(inspirationStore.items).toEqual([])
    expect(tagStore.items).toEqual([])
    expect(localStorage.getItem('inspiration-assistant.last-workspace.user-1')).toBe('personal')
  })

  it('按用户隔离最后选择', async () => {
    vi.mocked(workspaceApi.list).mockResolvedValue({
      items: [workspace('team')],
      total: 1,
      page: 1,
      page_size: 100,
    })
    const store = useWorkspaceStore()
    await store.initialize('user-1')
    expect(localStorage.getItem('inspiration-assistant.last-workspace.user-1')).toBe('team')
    expect(localStorage.getItem('inspiration-assistant.last-workspace.user-2')).toBeNull()
  })

  it('只有 ACTIVE 成员的 OWNER、ADMIN、EDITOR 可写', async () => {
    vi.mocked(workspaceApi.list).mockResolvedValue({
      items: [workspace('team', { role: 'EDITOR' })],
      total: 1,
      page: 1,
      page_size: 100,
    })
    const store = useWorkspaceStore()
    await store.initialize('user-1')
    expect(store.canWrite).toBe(true)

    store.items = [workspace('team', { role: 'VIEWER' })]
    expect(store.canWrite).toBe(false)
    store.items = [workspace('team', { role: 'EDITOR', member_status: 'SUSPENDED' })]
    expect(store.canWrite).toBe(false)
    store.items = [{ ...workspace('team', { role: 'EDITOR' }), status: 'SUSPENDED' as never }]
    expect(store.canWrite).toBe(false)
  })

  it('Workspace 创建网络结果不确定时复用绑定当前载荷的键', async () => {
    const created = workspace('created', { role: 'OWNER' })
    vi.mocked(workspaceApi.list).mockResolvedValue({
      items: [workspace('personal', { kind: 'PERSONAL', role: 'OWNER', is_default: true })],
      total: 1,
      page: 1,
      page_size: 100,
    })
    vi.mocked(workspaceApi.create)
      .mockRejectedValueOnce(new ApiError('网络异常', { code: 'network_error' }))
      .mockResolvedValueOnce(created)
    const store = useWorkspaceStore()
    await store.initialize('user-1')

    await expect(store.create({ name: '研发空间' }, 'user-1')).rejects.toBeDefined()
    await store.create({ name: '研发空间' }, 'user-1')

    const firstKey = vi.mocked(workspaceApi.create).mock.calls[0]?.[1]
    const secondKey = vi.mocked(workspaceApi.create).mock.calls[1]?.[1]
    expect(secondKey).toBe(firstKey)
  })

  it('撤权回落后重新授权，强制刷新可重新引入并选择 TEAM', async () => {
    const personal = workspace('personal', {
      kind: 'PERSONAL',
      role: 'OWNER',
      is_default: true,
    })
    const team = workspace('team', { role: 'EDITOR' })
    const page = { items: [personal, team], total: 2, page: 1, page_size: 100 }
    vi.mocked(workspaceApi.list)
      .mockResolvedValueOnce(page)
      // 撤权恢复请求即使遇到权限列表短暂缓存，也不得重新选回 TEAM。
      .mockResolvedValueOnce(page)
      // 服务端完成重新授权后的明确强制刷新。
      .mockResolvedValueOnce(page)

    const store = useWorkspaceStore()
    await store.initialize('user-1')
    expect(store.select('team', 'user-1')).toBe(true)

    const fallback = await store.recoverAccessRevocation('team', 'user-1')
    expect(fallback?.id).toBe('personal')
    expect(store.items.map((item) => item.id)).toEqual(['personal'])
    expect(store.activeWorkspaceId).toBe('personal')

    await store.initialize('user-1', true)
    expect(store.items.map((item) => item.id)).toEqual(['personal', 'team'])
    expect(store.select('team', 'user-1')).toBe(true)
    expect(store.activeWorkspaceId).toBe('team')
  })
})
