import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { tagApi } from '@/api/tags'
import type { ManagedTag } from '@/api/types'
import { useTagStore } from './tags'

vi.mock('@/api/tags', () => ({
  tagApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() },
}))

const managedTag: ManagedTag = {
  id: 'tag-1',
  name: '动漫',
  color: '#5A7D68',
  version: 1,
  usage_count: 3,
}

describe('tag store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('preserves the API usage count when a PATCH response replaces a managed tag', async () => {
    const response: ManagedTag = { ...managedTag, name: '新动漫', version: 2 }
    vi.mocked(tagApi.update).mockResolvedValue(response)
    const store = useTagStore()
    store.items = [{ ...managedTag }]

    await store.update(managedTag.id, { version: 1, name: response.name })

    expect(store.items[0]).toEqual(response)
  })

  it('starts a newly created tag at zero usages', async () => {
    const response: ManagedTag = { id: 'tag-2', name: '健身', color: null, version: 1, usage_count: 0 }
    vi.mocked(tagApi.create).mockResolvedValue(response)
    const store = useTagStore()

    await store.create({ name: response.name, color: null })

    expect(store.items).toEqual([response])
  })
})
