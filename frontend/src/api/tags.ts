import { apiClient } from './client'
import type { ManagedTag, TagCreate, TagUpdate } from './types'

export const tagApi = {
  async list(workspaceId: string, q?: string, signal?: AbortSignal): Promise<ManagedTag[]> {
    const config = signal ? { params: { q }, signal } : { params: { q } }
    const { data } = await apiClient.get<ManagedTag[]>(`/workspaces/${workspaceId}/tags`, config)
    return data
  },
  async create(workspaceId: string, input: TagCreate, idempotencyKey: string): Promise<ManagedTag> {
    const { data } = await apiClient.post<ManagedTag>(`/workspaces/${workspaceId}/tags`, input, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
    return data
  },
  async update(workspaceId: string, id: string, input: TagUpdate, idempotencyKey: string): Promise<ManagedTag> {
    const { data } = await apiClient.patch<ManagedTag>(
      `/workspaces/${workspaceId}/tags/${id}`,
      input,
      { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${input.version}"` } },
    )
    return data
  },
  async remove(
    workspaceId: string,
    id: string,
    version: number,
    force: boolean,
    idempotencyKey: string,
  ): Promise<void> {
    await apiClient.delete(`/workspaces/${workspaceId}/tags/${id}`, {
      params: { version, force },
      headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${version}"` },
    })
  },
}
