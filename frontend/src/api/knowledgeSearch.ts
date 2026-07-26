import { apiClient } from './client'
import { parseSearchResult } from './ragValidation'
import type { KnowledgeSearchInput, KnowledgeSearchResult } from './types'

export const knowledgeSearchApi = {
  async search(
    workspaceId: string,
    input: KnowledgeSearchInput,
    signal?: AbortSignal,
  ): Promise<KnowledgeSearchResult> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/search`,
      input,
      signal ? { signal } : {},
    )
    return parseSearchResult(data)
  },
}
