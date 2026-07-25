import { apiClient } from './client'
import { parseAuditLogPage } from './validation'
import type { AuditLogPage, AuditLogQuery } from './types'

export const auditLogApi = {
  async list(workspaceId: string, query: AuditLogQuery = {}, signal?: AbortSignal): Promise<AuditLogPage> {
    const config = signal ? { params: query, signal } : { params: query }
    const { data } = await apiClient.get<unknown>(`/workspaces/${workspaceId}/audit-logs`, config)
    return parseAuditLogPage(data)
  },
}
