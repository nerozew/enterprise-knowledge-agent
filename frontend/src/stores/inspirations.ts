import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { inspirationApi } from '@/api/inspirations'
import { executeIdempotentIntent } from '@/api/idempotency'
import { mapApiError } from '@/api/client'
import type { Inspiration, InspirationCreate, InspirationQuery, InspirationUpdate } from '@/api/types'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'

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
  const workspaceId = ref<string | null>(null)
  let activeController: AbortController | null = null
  let generation = 0

  const hasItems = computed(() => items.value.length > 0)

  function activateWorkspace(nextWorkspaceId: string | null) {
    if (workspaceId.value === nextWorkspaceId) return
    generation += 1
    activeController?.abort()
    activeController = null
    workspaceId.value = nextWorkspaceId
    items.value = []
    total.value = 0
    query.value = { ...defaultQuery }
    loading.value = false
    mutating.value = false
    errorMessage.value = ''
  }

  function requireWorkspace(): string {
    if (!workspaceId.value) throw new Error('Workspace context is not ready')
    return workspaceId.value
  }

  function handleUnavailable(error: unknown, targetWorkspaceId: string, requestGeneration: number) {
    const mapped = mapApiError(error)
    if (
      mapped.status === 404
      && mapped.code === 'resource_not_found'
      && requestGeneration === generation
      && workspaceId.value === targetWorkspaceId
    ) {
      items.value = []
      total.value = 0
      reportWorkspaceAccessRevoked(targetWorkspaceId)
    }
    return mapped
  }

  async function load(overrides: InspirationQuery = {}) {
    const targetWorkspaceId = requireWorkspace()
    query.value = { ...query.value, ...overrides }
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    const requestGeneration = generation
    loading.value = true
    errorMessage.value = ''
    try {
      const result = await inspirationApi.list(targetWorkspaceId, query.value, controller.signal)
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return
      items.value = result.items
      total.value = result.total
      query.value.page = result.page
      query.value.page_size = result.page_size
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return
      const mapped = handleUnavailable(error, targetWorkspaceId, requestGeneration)
      if (requestGeneration === generation && workspaceId.value === targetWorkspaceId) {
        errorMessage.value = mapped.message
      }
    } finally {
      if (activeController === controller) loading.value = false
    }
  }

  async function create(input: InspirationCreate) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    mutating.value = true
    try {
      const created = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:inspiration.create`,
        input,
        (key) => inspirationApi.create(targetWorkspaceId, input, key),
      )
      if (requestGeneration === generation && workspaceId.value === targetWorkspaceId) await load({ page: 1 })
      return created
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    } finally {
      if (requestGeneration === generation) mutating.value = false
    }
  }

  async function update(id: string, input: InspirationUpdate) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    mutating.value = true
    try {
      const updated = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:inspiration.update:${id}`,
        input,
        (key) => inspirationApi.update(targetWorkspaceId, id, input, key),
      )
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return updated
      const index = items.value.findIndex((item) => item.id === id)
      if (index >= 0) items.value[index] = updated
      return updated
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    } finally {
      if (requestGeneration === generation) mutating.value = false
    }
  }

  async function remove(item: Inspiration) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    mutating.value = true
    try {
      await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:inspiration.delete:${item.id}`,
        { version: item.version },
        (key) => inspirationApi.remove(targetWorkspaceId, item.id, item.version, key),
      )
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return
      items.value = items.value.filter((entry) => entry.id !== item.id)
      total.value = Math.max(0, total.value - 1)
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    } finally {
      if (requestGeneration === generation) mutating.value = false
    }
  }

  async function restore(item: Inspiration) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    mutating.value = true
    try {
      const version = item.version + 1
      const restored = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:inspiration.restore:${item.id}`,
        { version },
        (key) => inspirationApi.restore(targetWorkspaceId, item.id, version, key),
      )
      if (requestGeneration === generation && workspaceId.value === targetWorkspaceId) await load()
      return restored
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    } finally {
      if (requestGeneration === generation) mutating.value = false
    }
  }

  return {
    items,
    total,
    loading,
    mutating,
    errorMessage,
    query,
    hasItems,
    workspaceId,
    activateWorkspace,
    load,
    create,
    update,
    remove,
    restore,
  }
})
