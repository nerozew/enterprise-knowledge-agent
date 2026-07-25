import { apiClient } from './client'
import type {
  AiGeneration,
  AiGenerationInput,
  Inspiration,
  InspirationCreate,
  InspirationList,
  InspirationQuery,
  InspirationUpdate,
} from './types'

export const inspirationApi = {
  async list(workspaceId: string, query: InspirationQuery, signal?: AbortSignal): Promise<InspirationList> {
    const config = signal ? { params: query, signal } : { params: query }
    const { data } = await apiClient.get<InspirationList>(`/workspaces/${workspaceId}/inspirations`, config)
    return data
  },
  async get(workspaceId: string, id: string): Promise<Inspiration> {
    const { data } = await apiClient.get<Inspiration>(`/workspaces/${workspaceId}/inspirations/${id}`)
    return data
  },
  async create(workspaceId: string, input: InspirationCreate, idempotencyKey: string): Promise<Inspiration> {
    const { data } = await apiClient.post<Inspiration>(`/workspaces/${workspaceId}/inspirations`, input, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
    return data
  },
  async update(
    workspaceId: string,
    id: string,
    input: InspirationUpdate,
    idempotencyKey: string,
  ): Promise<Inspiration> {
    const { data } = await apiClient.patch<Inspiration>(
      `/workspaces/${workspaceId}/inspirations/${id}`,
      input,
      { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${input.version}"` } },
    )
    return data
  },
  async remove(workspaceId: string, id: string, version: number, idempotencyKey: string): Promise<void> {
    await apiClient.delete(`/workspaces/${workspaceId}/inspirations/${id}`, {
      params: { version },
      headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${version}"` },
    })
  },
  async restore(workspaceId: string, id: string, version: number, idempotencyKey: string): Promise<Inspiration> {
    const { data } = await apiClient.post<Inspiration>(
      `/workspaces/${workspaceId}/inspirations/${id}/restore`,
      { version },
      { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${version}"` } },
    )
    return data
  },
  async generate(
    workspaceId: string,
    id: string,
    input: AiGenerationInput,
    idempotencyKey: string,
  ): Promise<AiGeneration> {
    const { data } = await apiClient.post<AiGeneration>(
      `/workspaces/${workspaceId}/inspirations/${id}/ai-generations`,
      input,
      { headers: { 'Idempotency-Key': idempotencyKey } },
    )
    return data
  },
}
