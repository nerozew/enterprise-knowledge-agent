import { apiClient } from './client'
import { parseMemberSummary, parsePage, parseWorkspaceSummary } from './validation'
import type {
  MemberCreate,
  MemberQuery,
  MemberSummary,
  MemberUpdate,
  Page,
  WorkspaceCreate,
  WorkspaceSummary,
  WorkspaceUpdate,
} from './types'

export interface WorkspaceQuery {
  page?: number
  page_size?: number
}

export const workspaceApi = {
  async list(query: WorkspaceQuery = {}, signal?: AbortSignal): Promise<Page<WorkspaceSummary>> {
    const config = signal ? { params: query, signal } : { params: query }
    const { data } = await apiClient.get<unknown>('/workspaces', config)
    return parsePage(data, parseWorkspaceSummary, 'workspaces')
  },
  async get(workspaceId: string, signal?: AbortSignal): Promise<WorkspaceSummary> {
    const config = signal ? { signal } : {}
    const { data } = await apiClient.get<unknown>(`/workspaces/${workspaceId}`, config)
    return parseWorkspaceSummary(data)
  },
  async create(input: WorkspaceCreate, idempotencyKey: string): Promise<WorkspaceSummary> {
    const { data } = await apiClient.post<unknown>('/workspaces', input, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
    return parseWorkspaceSummary(data)
  },
  async update(workspaceId: string, input: WorkspaceUpdate, idempotencyKey: string): Promise<WorkspaceSummary> {
    const { data } = await apiClient.patch<unknown>(`/workspaces/${workspaceId}`, input, {
      headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${input.version}"` },
    })
    return parseWorkspaceSummary(data)
  },
  async listMembers(
    workspaceId: string,
    query: MemberQuery = {},
    signal?: AbortSignal,
  ): Promise<Page<MemberSummary>> {
    const config = signal ? { params: query, signal } : { params: query }
    const { data } = await apiClient.get<unknown>(`/workspaces/${workspaceId}/members`, config)
    return parsePage(data, parseMemberSummary, 'members')
  },
  async addMember(workspaceId: string, input: MemberCreate, idempotencyKey: string): Promise<MemberSummary> {
    const { data } = await apiClient.post<unknown>(`/workspaces/${workspaceId}/members`, input, {
      headers: { 'Idempotency-Key': idempotencyKey },
    })
    return parseMemberSummary(data)
  },
  async updateMember(
    workspaceId: string,
    userId: string,
    input: MemberUpdate,
    idempotencyKey: string,
  ): Promise<MemberSummary> {
    const { data } = await apiClient.patch<unknown>(
      `/workspaces/${workspaceId}/members/${userId}`,
      input,
      { headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${input.version}"` } },
    )
    return parseMemberSummary(data)
  },
  async removeMember(
    workspaceId: string,
    userId: string,
    version: number,
    idempotencyKey: string,
  ): Promise<void> {
    await apiClient.delete(`/workspaces/${workspaceId}/members/${userId}`, {
      params: { version },
      headers: { 'Idempotency-Key': idempotencyKey, 'If-Match': `"${version}"` },
    })
  },
}
