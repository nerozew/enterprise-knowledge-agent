import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { documentApi } from '@/api/documents'
import { executeIdempotentIntent } from '@/api/idempotency'
import { mapApiError } from '@/api/client'
import type {
  DocumentQuery,
  DocumentSummary,
  DocumentUploadInput,
  DocumentVersionSummary,
  IngestionJobSummary,
} from '@/api/types'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'

const terminalStates = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED'])
const defaultQuery: DocumentQuery = { page: 1, page_size: 12 }
const activeJobsStoragePrefix = 'knowledge.active-jobs'

function compareVersionsDesc(left: DocumentVersionSummary, right: DocumentVersionSummary): number {
  const createdAtDifference = Date.parse(right.created_at) - Date.parse(left.created_at)
  if (Number.isFinite(createdAtDifference) && createdAtDifference !== 0) return createdAtDifference
  const createdAtTextDifference = right.created_at.localeCompare(left.created_at)
  return createdAtTextDifference || right.id.localeCompare(left.id)
}

function activeJobsKey(workspaceId: string): string {
  return `${activeJobsStoragePrefix}.${workspaceId}`
}

function readActiveJobIds(workspaceId: string): string[] {
  try {
    const value = JSON.parse(sessionStorage.getItem(activeJobsKey(workspaceId)) ?? '[]') as unknown
    return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []
  } catch {
    return []
  }
}

export const useDocumentStore = defineStore('documents', () => {
  const workspaceId = ref<string | null>(null)
  const items = ref<DocumentSummary[]>([])
  const versions = ref<Record<string, DocumentVersionSummary[]>>({})
  const jobs = ref<Record<string, IngestionJobSummary>>({})
  const total = ref(0)
  const query = ref<DocumentQuery>({ ...defaultQuery })
  const loading = ref(false)
  const uploading = ref(false)
  const mutatingJobId = ref<string | null>(null)
  const errorMessage = ref('')
  const uploadMessage = ref('')
  let generation = 0
  let loadController: AbortController | null = null
  const mutationControllers = new Set<AbortController>()
  const pollControllers = new Map<string, AbortController>()
  const pollTimers = new Map<string, ReturnType<typeof setTimeout>>()
  const pollDelays = new Map<string, number>()

  const hasItems = computed(() => items.value.length > 0)
  const jobsByDocument = computed<Record<string, IngestionJobSummary>>(() => {
    const result: Record<string, IngestionJobSummary> = {}
    Object.values(jobs.value).forEach((job) => {
      const existing = result[job.document_id]
      if (!existing || existing.updated_at < job.updated_at) result[job.document_id] = job
    })
    return result
  })
  const currentVersions = computed<Record<string, DocumentVersionSummary | undefined>>(() => {
    const result: Record<string, DocumentVersionSummary | undefined> = {}
    items.value.forEach((item) => {
      const entries = versions.value[item.id] ?? []
      const versionStatus = query.value.version_status
      if (!versionStatus) {
        result[item.id] = entries.find((entry) => entry.id === item.published_version_id) ?? entries[0]
        return
      }
      if (versionStatus === 'PUBLISHED') {
        result[item.id] = entries.find((entry) => entry.id === item.published_version_id)
        return
      }
      const sourceFormat = query.value.source_format
      result[item.id] = entries
        .filter((entry) => (
          entry.status === versionStatus
          && (!sourceFormat || entry.format === sourceFormat)
        ))
        .sort(compareVersionsDesc)[0]
    })
    return result
  })

  function persistActiveJobs() {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) return
    const ids = Object.values(jobs.value)
      .filter((job) => !terminalStates.has(job.status))
      .map((job) => job.id)
    sessionStorage.setItem(activeJobsKey(targetWorkspaceId), JSON.stringify(ids))
  }

  function stopPolling() {
    pollTimers.forEach((timer) => clearTimeout(timer))
    pollTimers.clear()
    pollControllers.forEach((controller) => controller.abort())
    pollControllers.clear()
    pollDelays.clear()
  }

  function activateWorkspace(nextWorkspaceId: string | null) {
    if (workspaceId.value === nextWorkspaceId) return
    generation += 1
    loadController?.abort()
    loadController = null
    mutationControllers.forEach((controller) => controller.abort())
    mutationControllers.clear()
    stopPolling()
    workspaceId.value = nextWorkspaceId
    items.value = []
    versions.value = {}
    jobs.value = {}
    total.value = 0
    query.value = { ...defaultQuery }
    loading.value = false
    uploading.value = false
    mutatingJobId.value = null
    errorMessage.value = ''
    uploadMessage.value = ''
    if (nextWorkspaceId) {
      readActiveJobIds(nextWorkspaceId).forEach((jobId) => schedulePoll(jobId, 0))
    }
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
      versions.value = {}
      jobs.value = {}
      total.value = 0
      stopPolling()
      reportWorkspaceAccessRevoked(targetWorkspaceId)
    }
    return mapped
  }

  async function load(overrides: DocumentQuery = {}) {
    const targetWorkspaceId = requireWorkspace()
    query.value = { ...query.value, ...overrides }
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    const requestGeneration = generation
    loading.value = true
    errorMessage.value = ''
    try {
      const result = await documentApi.list(targetWorkspaceId, query.value, controller.signal)
      const loadedVersions = await Promise.all(result.items.map(async (item) => {
        try {
          return [item.id, await documentApi.versions(targetWorkspaceId, item.id, controller.signal)] as const
        } catch (error) {
          const mapped = mapApiError(error)
          if (mapped.status === 404 && mapped.code === 'resource_not_found') throw mapped
          return [item.id, []] as const
        }
      }))
      if (controller.signal.aborted || requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return
      items.value = result.items
      total.value = result.total
      query.value.page = result.page
      query.value.page_size = result.page_size
      versions.value = Object.fromEntries(loadedVersions)
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return
      errorMessage.value = handleUnavailable(error, targetWorkspaceId, requestGeneration).message
    } finally {
      if (loadController === controller) {
        loading.value = false
        loadController = null
      }
    }
  }

  function schedulePoll(jobId: string, requestedDelay: number) {
    const delay = document.hidden ? Math.max(10_000, requestedDelay) : requestedDelay
    const existing = pollTimers.get(jobId)
    if (existing) clearTimeout(existing)
    pollTimers.set(jobId, setTimeout(() => {
      pollTimers.delete(jobId)
      void poll(jobId)
    }, delay))
  }

  async function poll(jobId: string) {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId || pollControllers.has(jobId)) return
    const requestGeneration = generation
    const controller = new AbortController()
    pollControllers.set(jobId, controller)
    try {
      const job = await documentApi.job(targetWorkspaceId, jobId, controller.signal)
      if (controller.signal.aborted || requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return
      jobs.value = { ...jobs.value, [job.id]: job }
      persistActiveJobs()
      if (terminalStates.has(job.status)) {
        pollDelays.delete(jobId)
        await load()
      } else {
        const nextDelay = Math.min(10_000, Math.round((pollDelays.get(jobId) ?? 1_250) * 1.6))
        pollDelays.set(jobId, nextDelay)
        schedulePoll(jobId, nextDelay)
      }
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return
      const mapped = handleUnavailable(error, targetWorkspaceId, requestGeneration)
      if (mapped.status !== 404) {
        const nextDelay = Math.min(10_000, Math.round((pollDelays.get(jobId) ?? 1_250) * 1.6))
        pollDelays.set(jobId, nextDelay)
        schedulePoll(jobId, nextDelay)
      }
    } finally {
      if (pollControllers.get(jobId) === controller) pollControllers.delete(jobId)
    }
  }

  async function upload(input: DocumentUploadInput) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    const controller = new AbortController()
    mutationControllers.add(controller)
    uploading.value = true
    uploadMessage.value = ''
    const fingerprint = {
      name: input.file.name,
      size: input.file.size,
      lastModified: input.file.lastModified,
      type: input.file.type,
      title: input.title?.trim() ?? '',
      duplicate_policy: input.duplicate_policy,
      ocr_mode: input.ocr_mode,
    }
    try {
      const result = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:document.upload`,
        fingerprint,
        (key) => documentApi.upload(targetWorkspaceId, input, key, controller.signal),
      )
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return result
      jobs.value = { ...jobs.value, [result.job.id]: result.job }
      versions.value = { ...versions.value, [result.document.id]: [result.version] }
      uploadMessage.value = result.reused ? '检测到相同内容，已复用现有版本。' : '文档已接收，正在构建可检索版本。'
      persistActiveJobs()
      if (!terminalStates.has(result.job.status)) schedulePoll(result.job.id, 2_000)
      await load({ page: 1 })
      return result
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return undefined
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    } finally {
      mutationControllers.delete(controller)
      if (requestGeneration === generation) uploading.value = false
    }
  }

  async function retry(job: IngestionJobSummary) {
    return mutateJob('retry', job)
  }

  async function cancel(job: IngestionJobSummary) {
    return mutateJob('cancel', job)
  }

  async function mutateJob(action: 'retry' | 'cancel', job: IngestionJobSummary) {
    const targetWorkspaceId = requireWorkspace()
    const requestGeneration = generation
    const controller = new AbortController()
    mutationControllers.add(controller)
    mutatingJobId.value = job.id
    try {
      const updated = await executeIdempotentIntent(
        `workspace:${targetWorkspaceId}:ingestion.${action}:${job.id}`,
        { version: job.version },
        (key) => documentApi[action](targetWorkspaceId, job, key, controller.signal),
      )
      if (requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return updated
      jobs.value = { ...jobs.value, [updated.id]: updated }
      persistActiveJobs()
      if (!terminalStates.has(updated.status)) schedulePoll(updated.id, 2_000)
      return updated
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return undefined
      handleUnavailable(error, targetWorkspaceId, requestGeneration)
      throw error
    } finally {
      mutationControllers.delete(controller)
      if (requestGeneration === generation) mutatingJobId.value = null
    }
  }

  return {
    workspaceId,
    items,
    versions,
    currentVersions,
    jobs,
    jobsByDocument,
    total,
    query,
    loading,
    uploading,
    mutatingJobId,
    errorMessage,
    uploadMessage,
    hasItems,
    activateWorkspace,
    load,
    upload,
    retry,
    cancel,
  }
})
