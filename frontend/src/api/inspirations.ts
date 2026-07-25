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
  async list(query: InspirationQuery, signal?: AbortSignal): Promise<InspirationList> {
    const config = signal ? { params: query, signal } : { params: query }
    const { data } = await apiClient.get<InspirationList>('/inspirations', config)
    return data
  },
  async get(id: string): Promise<Inspiration> {
    const { data } = await apiClient.get<Inspiration>(`/inspirations/${id}`)
    return data
  },
  async create(input: InspirationCreate): Promise<Inspiration> {
    const { data } = await apiClient.post<Inspiration>('/inspirations', input)
    return data
  },
  async update(id: string, input: InspirationUpdate): Promise<Inspiration> {
    const { data } = await apiClient.patch<Inspiration>(`/inspirations/${id}`, input)
    return data
  },
  async remove(id: string, version: number): Promise<void> {
    await apiClient.delete(`/inspirations/${id}`, { params: { version } })
  },
  async restore(id: string, version: number): Promise<Inspiration> {
    const { data } = await apiClient.post<Inspiration>(`/inspirations/${id}/restore`, { version })
    return data
  },
  async generate(id: string, input: AiGenerationInput, idempotencyKey: string): Promise<AiGeneration> {
    const { data } = await apiClient.post<AiGeneration>(`/inspirations/${id}/ai-generations`, input, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
    return data
  },
}
