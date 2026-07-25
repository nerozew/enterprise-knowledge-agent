import { ref } from 'vue'
import { defineStore } from 'pinia'
import { mapApiError } from '@/api/client'
import { tagApi } from '@/api/tags'
import { executeIdempotentIntent } from '@/api/idempotency'
import type { ManagedTag, TagCreate, TagUpdate } from '@/api/types'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'

export const useTagStore = defineStore('tags', () => {
  const items = ref<ManagedTag[]>([])
  const loading = ref(false)
  const errorMessage = ref('')
  const workspaceId = ref<string | null>(null)
  let activeController: AbortController | null = null
  let generation = 0

  function activateWorkspace(nextWorkspaceId: string | null) {
    if (workspaceId.value === nextWorkspaceId) return
    generation += 1
    activeController?.abort()
    activeController = null
    workspaceId.value = nextWorkspaceId
    items.value = []
    loading.value = false
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
      reportWorkspaceAccessRevoked(targetWorkspaceId)
    }
    return mapped
  }

  async function load(q?: string) {
    const targetWorkspaceId = requireWorkspace()
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    const requestGeneration = generation
    loading.value = true
    errorMessage.value = ''
    try {
      const result = await tagApi.list(targetWorkspaceId, q, controller.signal)
      if (requestGeneration === generation && workspaceId.value === targetWorkspaceId) items.value = result
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return
      const mapped = handleUnavailable(error, targetWorkspaceId, requestGeneration)
      if (requestGeneration === generation && workspaceId.value === targetWorkspaceId) {
        errorMessage.value = mapped.message
      }
    } finally {
      if (activeController === controller) {
        loading.value = false
        activeController = null
      }
    }
  }

  async function create(input: TagCreate) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    try {
      const created = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:tag.create`,
        input,
        (key) => tagApi.create(targetWorkspaceId, input, key),
      )
      if (requestGeneration === generation && workspaceId.value === targetWorkspaceId) items.value.push(created)
      return created
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    }
  }

  async function update(id: string, input: TagUpdate) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    try {
      const updated = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:tag.update:${id}`,
        input,
        (key) => tagApi.update(targetWorkspaceId, id, input, key),
      )
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return updated
      const index = items.value.findIndex((item) => item.id === id)
      if (index >= 0) items.value[index] = updated
      return updated
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    }
  }

  async function remove(item: ManagedTag, force = false) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    try {
      await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:tag.delete:${item.id}`,
        { version: item.version, force },
        (key) => tagApi.remove(targetWorkspaceId, item.id, item.version, force, key),
      )
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return
      items.value = items.value.filter((entry) => entry.id !== item.id)
    } catch (error) {
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    }
  }

  return { items, loading, errorMessage, workspaceId, activateWorkspace, load, create, update, remove }
})
