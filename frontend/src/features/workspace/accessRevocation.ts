export const WORKSPACE_ACCESS_REVOKED_EVENT = 'workspace:access-revoked'

export interface WorkspaceAccessRevokedDetail {
  workspaceId: string
}

export function reportWorkspaceAccessRevoked(workspaceId: string) {
  if (!workspaceId) return
  window.dispatchEvent(new CustomEvent<WorkspaceAccessRevokedDetail>(
    WORKSPACE_ACCESS_REVOKED_EVENT,
    { detail: { workspaceId } },
  ))
}

export function revokedWorkspaceId(event: Event): string | null {
  if (!(event instanceof CustomEvent)) return null
  const detail = event.detail as unknown
  if (!detail || typeof detail !== 'object') return null
  const workspaceId = (detail as Record<string, unknown>).workspaceId
  return typeof workspaceId === 'string' && workspaceId ? workspaceId : null
}
