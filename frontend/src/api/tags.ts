import { apiClient } from './client'
import type { ManagedTag, TagCreate, TagUpdate } from './types'

export const tagApi = {
  async list(q?: string): Promise<ManagedTag[]> {
    const { data } = await apiClient.get<ManagedTag[]>('/tags', { params: { q } })
    return data
  },
  async create(input: TagCreate): Promise<ManagedTag> {
    const { data } = await apiClient.post<ManagedTag>('/tags', input)
    return data
  },
  async update(id: string, input: TagUpdate): Promise<ManagedTag> {
    const { data } = await apiClient.patch<ManagedTag>(`/tags/${id}`, input)
    return data
  },
  async remove(id: string, version: number, force = false): Promise<void> {
    await apiClient.delete(`/tags/${id}`, { params: { version, force } })
  },
}
