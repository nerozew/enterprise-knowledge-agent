import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { knowledgeSearchApi } from '@/api/knowledgeSearch'
import { mapApiError } from '@/api/client'
import type { KnowledgeSearchInput, KnowledgeSearchResult } from '@/api/types'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'

export const defaultKnowledgeSearchInput: KnowledgeSearchInput = {
  query: '',
  strategy: 'BASIC',
  retrieval_mode: 'HYBRID',
  top_k: 5,
  candidate_k: 50,
  fusion: 'RRF',
  rerank: 'AUTO',
  rewrite: 'AUTO',
  multi_query: false,
  expand_context: true,
  response_mode: 'HITS',
  filters: {
    document_ids: [],
    source_formats: [],
    published_after: null,
    published_before: null,
  },
}

export const useKnowledgeSearchStore = defineStore('knowledge-search', () => {
  const workspaceId = ref<string | null>(null)
  const form = ref<KnowledgeSearchInput>(structuredClone(defaultKnowledgeSearchInput))
  const result = ref<KnowledgeSearchResult | null>(null)
  const loading = ref(false)
  const errorMessage = ref('')
  let activeController: AbortController | null = null
  let generation = 0

  const hasResult = computed(() => result.value !== null)

  function activateWorkspace(nextWorkspaceId: string | null) {
    if (workspaceId.value === nextWorkspaceId) return
    generation += 1
    activeController?.abort()
    activeController = null
    workspaceId.value = nextWorkspaceId
    form.value = structuredClone(defaultKnowledgeSearchInput)
    result.value = null
    loading.value = false
    errorMessage.value = ''
  }

  async function search() {
    const targetWorkspaceId = workspaceId.value
    if (!targetWorkspaceId) throw new Error('Workspace context is not ready')
    const normalizedQuery = form.value.query.trim()
    if (!normalizedQuery) return
    activeController?.abort()
    const controller = new AbortController()
    activeController = controller
    const requestGeneration = generation
    loading.value = true
    errorMessage.value = ''
    result.value = null
    try {
      const response = await knowledgeSearchApi.search(
        targetWorkspaceId,
        { ...form.value, query: normalizedQuery },
        controller.signal,
      )
      if (controller.signal.aborted || requestGeneration !== generation || workspaceId.value !== targetWorkspaceId) return
      result.value = response
    } catch (error) {
      if (controller.signal.aborted || requestGeneration !== generation) return
      const mapped = mapApiError(error)
      if (
        mapped.status === 404
        && mapped.code === 'resource_not_found'
        && workspaceId.value === targetWorkspaceId
      ) {
        result.value = null
        reportWorkspaceAccessRevoked(targetWorkspaceId)
      }
      errorMessage.value = mapped.code === 'llm_dependency_unavailable'
        ? '当前未配置可用的大模型，无法生成实验性答案。你可以切换到“只返回检索结果”。'
        : mapped.message
    } finally {
      if (activeController === controller) {
        activeController = null
        loading.value = false
      }
    }
  }

  function clearResult() {
    activeController?.abort()
    activeController = null
    result.value = null
    errorMessage.value = ''
    loading.value = false
  }

  return {
    workspaceId,
    form,
    result,
    loading,
    errorMessage,
    hasResult,
    activateWorkspace,
    search,
    clearResult,
  }
})
