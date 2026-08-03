import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { workspaceApi } from '@/api/workspaces'
import { executeIdempotentIntent } from '@/api/idempotency'
import { mapApiError } from '@/api/client'
import type { WorkspaceCreate, WorkspaceSummary } from '@/api/types'
import { useInspirationStore } from './inspirations'
import { useTagStore } from './tags'
import { useDocumentStore } from './documents'
import { useKnowledgeSearchStore } from './knowledgeSearch'
import { useAgentStore } from './agent'

const LAST_WORKSPACE_PREFIX = 'inspiration-assistant.last-workspace'

function storageKey(userId: string): string {
  return `${LAST_WORKSPACE_PREFIX}.${userId}`
}

export const useWorkspaceStore = defineStore('workspaces', () => {
  const items = ref<WorkspaceSummary[]>([])
  const activeWorkspaceId = ref<string | null>(null)
  const loading = ref(false)
  const creating = ref(false)
  const errorMessage = ref('')
  const recoveryMessage = ref('')
  let loadedForUserId: string | null = null
  let activeController: AbortController | null = null
  let loadPromise: Promise<void> | null = null
  let revocationPromise: Promise<WorkspaceSummary | null> | null = null
  let revokingWorkspaceId: string | null = null

  const activeWorkspace = computed(
    () => items.value.find((workspace) => workspace.id === activeWorkspaceId.value) ?? null,
  )
  const canManage = computed(
    () => activeWorkspace.value?.role === 'OWNER' || activeWorkspace.value?.role === 'ADMIN',
  )
  const canWrite = computed(() => {
    const workspace = activeWorkspace.value
    return Boolean(
      workspace
      && workspace.status === 'ACTIVE'
      && workspace.member_status === 'ACTIVE'
      && ['OWNER', 'ADMIN', 'EDITOR'].includes(workspace.role),
    )
  })

  function resetScopedStores(workspaceId: string | null) {
    useInspirationStore().activateWorkspace(workspaceId)
    useTagStore().activateWorkspace(workspaceId)
    useDocumentStore().activateWorkspace(workspaceId)
    useKnowledgeSearchStore().activateWorkspace(workspaceId)
    useAgentStore().activateWorkspace(workspaceId)
  }

  function selectLocal(workspaceId: string, userId: string) {
    const available = items.value.some(
      (workspace) => workspace.id === workspaceId && workspace.member_status === 'ACTIVE',
    )
    if (!available) return false
    if (activeWorkspaceId.value !== workspaceId) {
      activeWorkspaceId.value = workspaceId
      resetScopedStores(workspaceId)
    } else {
      resetScopedStores(workspaceId)
    }
    localStorage.setItem(storageKey(userId), workspaceId)
    return true
  }

  function chooseInitial(userId: string): WorkspaceSummary | null {
    const lastWorkspaceId = localStorage.getItem(storageKey(userId))
    const lastWorkspace = items.value.find(
      (workspace) => workspace.id === lastWorkspaceId && workspace.member_status === 'ACTIVE',
    )
    if (lastWorkspace) return lastWorkspace

    const defaultWorkspace = items.value.find(
      (workspace) => workspace.is_default && workspace.member_status === 'ACTIVE',
    )
    if (lastWorkspaceId && defaultWorkspace) {
      recoveryMessage.value = '上次使用的空间已不可访问，已回到默认个人空间。'
    }
    if (defaultWorkspace) return defaultWorkspace

    const firstActive = items.value.find((workspace) => workspace.member_status === 'ACTIVE') ?? null
    if (firstActive) {
      recoveryMessage.value = '未找到默认个人空间，已临时进入首个可访问空间。'
    }
    return firstActive
  }

  async function initialize(userId: string, force = false): Promise<void> {
    if (!force && loadedForUserId === userId && items.value.length) return
    if (!force && loadPromise && loadedForUserId === userId) return loadPromise
    if (loadedForUserId !== userId) {
      items.value = []
      activeWorkspaceId.value = null
      resetScopedStores(null)
    }

    loadedForUserId = userId
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    loading.value = true
    errorMessage.value = ''
    recoveryMessage.value = ''

    const promise = (async () => {
      try {
        const page = await workspaceApi.list({ page: 1, page_size: 100 }, controller.signal)
        if (controller.signal.aborted || loadedForUserId !== userId) return
        items.value = page.items
        const initial = chooseInitial(userId)
        if (initial) selectLocal(initial.id, userId)
        else resetScopedStores(null)
      } catch (error) {
        if (controller.signal.aborted) return
        errorMessage.value = mapApiError(error).message
        throw error
      } finally {
        if (activeController === controller) {
          loading.value = false
          activeController = null
          loadPromise = null
        }
      }
    })()
    loadPromise = promise
    return promise
  }

  function select(workspaceId: string, userId: string): boolean {
    recoveryMessage.value = ''
    return selectLocal(workspaceId, userId)
  }

  function fallback(userId: string): WorkspaceSummary | null {
    const next = chooseInitial(userId)
    if (next) selectLocal(next.id, userId)
    return next
  }

  async function create(input: WorkspaceCreate, userId: string): Promise<WorkspaceSummary> {
    creating.value = true
    try {
      const created = await executeIdempotentIntent(
        `user:${userId}:workspace.create`,
        input,
        (key) => workspaceApi.create(input, key),
      )
      items.value.push(created)
      selectLocal(created.id, userId)
      return created
    } finally {
      creating.value = false
    }
  }

  async function recoverAccessRevocation(
    revokedWorkspaceId: string,
    userId: string,
  ): Promise<WorkspaceSummary | null> {
    if (revocationPromise && revokingWorkspaceId === revokedWorkspaceId) return revocationPromise
    if (activeWorkspaceId.value !== revokedWorkspaceId) return activeWorkspace.value

    activeController?.abort()
    activeController = null
    loadPromise = null
    revokingWorkspaceId = revokedWorkspaceId
    items.value = items.value.filter((workspace) => workspace.id !== revokedWorkspaceId)
    activeWorkspaceId.value = null
    resetScopedStores(null)
    if (localStorage.getItem(storageKey(userId)) === revokedWorkspaceId) {
      localStorage.removeItem(storageKey(userId))
    }
    loading.value = true
    errorMessage.value = ''
    recoveryMessage.value = '当前空间权限已失效，正在返回可访问空间。'

    const controller = new AbortController()
    activeController = controller
    const promise = (async (): Promise<WorkspaceSummary | null> => {
      try {
        const page = await workspaceApi.list({ page: 1, page_size: 100 }, controller.signal)
        if (controller.signal.aborted || loadedForUserId !== userId) return null
        // 即使权限列表存在短暂缓存，也不能在本次恢复中重新选中已被拒绝的空间。
        items.value = page.items.filter((workspace) => workspace.id !== revokedWorkspaceId)
        const fallback = chooseInitial(userId)
        if (fallback) {
          selectLocal(fallback.id, userId)
          recoveryMessage.value = '当前空间已不可访问，已回到默认个人空间。'
        } else {
          resetScopedStores(null)
          recoveryMessage.value = '当前空间已不可访问，且暂时没有其他可访问空间。'
        }
        return fallback
      } catch (error) {
        if (controller.signal.aborted) return null
        errorMessage.value = mapApiError(error).message
        return null
      } finally {
        if (activeController === controller) {
          activeController = null
          loading.value = false
        }
        if (revokingWorkspaceId === revokedWorkspaceId) {
          revocationPromise = null
          revokingWorkspaceId = null
        }
      }
    })()
    revocationPromise = promise
    return promise
  }

  async function update(input: { name: string; version: number }): Promise<WorkspaceSummary> {
    const target = activeWorkspace.value
    if (!target) throw new Error('Workspace context is not ready')
    const updated = await executeIdempotentIntent(
      `workspace:${target.id}:workspace.update`,
      input,
      (key) => workspaceApi.update(target.id, input, key),
    )
    const index = items.value.findIndex((workspace) => workspace.id === target.id)
    if (index >= 0) items.value[index] = updated
    return updated
  }

  function clear() {
    activeController?.abort()
    activeController = null
    loadPromise = null
    revocationPromise = null
    revokingWorkspaceId = null
    loadedForUserId = null
    items.value = []
    activeWorkspaceId.value = null
    errorMessage.value = ''
    recoveryMessage.value = ''
    loading.value = false
    resetScopedStores(null)
  }

  return {
    items,
    activeWorkspaceId,
    activeWorkspace,
    loading,
    creating,
    errorMessage,
    recoveryMessage,
    canManage,
    canWrite,
    initialize,
    select,
    fallback,
    create,
    update,
    recoverAccessRevocation,
    clear,
  }
})
