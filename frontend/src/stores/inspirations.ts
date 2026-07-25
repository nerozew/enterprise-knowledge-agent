import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { inspirationApi } from '@/api/inspirations'
import { mapApiError } from '@/api/client'
import type { Inspiration, InspirationCreate, InspirationQuery, InspirationUpdate } from '@/api/types'

const defaultQuery: Required<Pick<InspirationQuery, 'tag_match' | 'sort' | 'page' | 'page_size'>> = {
  tag_match: 'any',
  sort: 'updated_desc',
  page: 1,
  page_size: 12,
}

export const useInspirationStore = defineStore('inspirations', () => {
  const items = ref<Inspiration[]>([])
  const total = ref(0)
  const loading = ref(false)
  const mutating = ref(false)
  const errorMessage = ref('')
  const query = ref<InspirationQuery>({ ...defaultQuery })
  let activeController: AbortController | null = null

  const hasItems = computed(() => items.value.length > 0)

  async function load(overrides: InspirationQuery = {}) {
    query.value = { ...query.value, ...overrides }
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    loading.value = true
    errorMessage.value = ''
    try {
      const result = await inspirationApi.list(query.value, controller.signal)
      items.value = result.items
      total.value = result.total
      query.value.page = result.page
      query.value.page_size = result.page_size
    } catch (error) {
      if (controller.signal.aborted) return
      errorMessage.value = mapApiError(error).message
    } finally {
      if (activeController === controller) loading.value = false
    }
  }

  async function create(input: InspirationCreate) {
    mutating.value = true
    try {
      const created = await inspirationApi.create(input)
      await load({ page: 1 })
      return created
    } finally {
      mutating.value = false
    }
  }

  async function update(id: string, input: InspirationUpdate) {
    mutating.value = true
    try {
      const updated = await inspirationApi.update(id, input)
      const index = items.value.findIndex((item) => item.id === id)
      if (index >= 0) items.value[index] = updated
      return updated
    } finally {
      mutating.value = false
    }
  }

  async function remove(item: Inspiration) {
    mutating.value = true
    try {
      await inspirationApi.remove(item.id, item.version)
      items.value = items.value.filter((entry) => entry.id !== item.id)
      total.value = Math.max(0, total.value - 1)
    } finally {
      mutating.value = false
    }
  }

  async function restore(item: Inspiration) {
    mutating.value = true
    try {
      const restored = await inspirationApi.restore(item.id, item.version + 1)
      await load()
      return restored
    } finally {
      mutating.value = false
    }
  }

  return { items, total, loading, mutating, errorMessage, query, hasItems, load, create, update, remove, restore }
})
