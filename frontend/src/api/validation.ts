import { ApiError } from './client'
import type {
  AuditLog,
  AuditLogPage,
  MemberStatus,
  MemberSummary,
  Page,
  WorkspaceKind,
  WorkspaceRole,
  WorkspaceStatus,
  WorkspaceSummary,
} from './types'

type JsonObject = Record<string, unknown>

function invalidResponse(field: string): never {
  throw new ApiError('服务器返回了无法识别的数据，请稍后重试', {
    code: 'invalid_response',
    details: [{ field }],
  })
}

function object(value: unknown, field: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalidResponse(field)
  return value as JsonObject
}

function string(value: unknown, field: string): string {
  if (typeof value !== 'string') invalidResponse(field)
  return value
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null
  return string(value, field)
}

function number(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) invalidResponse(field)
  return value
}

function boolean(value: unknown, field: string): boolean {
  if (typeof value !== 'boolean') invalidResponse(field)
  return value
}

function enumeration<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== 'string' || !allowed.includes(value as T)) invalidResponse(field)
  return value as T
}

const workspaceKinds = ['PERSONAL', 'TEAM'] as const satisfies readonly WorkspaceKind[]
const workspaceStatuses = ['ACTIVE'] as const satisfies readonly WorkspaceStatus[]
const workspaceRoles = ['OWNER', 'ADMIN', 'EDITOR', 'VIEWER'] as const satisfies readonly WorkspaceRole[]
const memberStatuses = ['INVITED', 'ACTIVE', 'SUSPENDED', 'REMOVED'] as const satisfies readonly MemberStatus[]

export function parseWorkspaceSummary(value: unknown): WorkspaceSummary {
  const item = object(value, 'workspace')
  return {
    id: string(item.id, 'workspace.id'),
    name: string(item.name, 'workspace.name'),
    kind: enumeration(item.kind, workspaceKinds, 'workspace.kind'),
    status: enumeration(item.status, workspaceStatuses, 'workspace.status'),
    role: enumeration(item.role, workspaceRoles, 'workspace.role'),
    member_status: enumeration(item.member_status, memberStatuses, 'workspace.member_status'),
    version: number(item.version, 'workspace.version'),
    is_default: boolean(item.is_default, 'workspace.is_default'),
    created_at: string(item.created_at, 'workspace.created_at'),
    updated_at: string(item.updated_at, 'workspace.updated_at'),
  }
}

export function parsePage<T>(value: unknown, parseItem: (entry: unknown) => T, field: string): Page<T> {
  const page = object(value, field)
  if (!Array.isArray(page.items)) invalidResponse(`${field}.items`)
  return {
    items: page.items.map(parseItem),
    total: number(page.total, `${field}.total`),
    page: number(page.page, `${field}.page`),
    page_size: number(page.page_size, `${field}.page_size`),
  }
}

export function parseMemberSummary(value: unknown): MemberSummary {
  const item = object(value, 'member')
  return {
    workspace_id: string(item.workspace_id, 'member.workspace_id'),
    user_id: string(item.user_id, 'member.user_id'),
    email: string(item.email, 'member.email'),
    display_name: string(item.display_name, 'member.display_name'),
    role: enumeration(item.role, workspaceRoles, 'member.role'),
    status: enumeration(item.status, memberStatuses, 'member.status'),
    version: number(item.version, 'member.version'),
    joined_at: nullableString(item.joined_at, 'member.joined_at'),
    updated_at: string(item.updated_at, 'member.updated_at'),
  }
}

function parseAuditLog(value: unknown): AuditLog {
  const item = object(value, 'audit_log')
  const actor = object(item.actor, 'audit_log.actor')
  const metadata = object(item.metadata, 'audit_log.metadata')
  return {
    id: string(item.id, 'audit_log.id'),
    action: string(item.action, 'audit_log.action'),
    actor: {
      user_id: nullableString(actor.user_id, 'audit_log.actor.user_id'),
      display_name: string(actor.display_name, 'audit_log.actor.display_name'),
      role: actor.role === null
        ? null
        : enumeration(actor.role, workspaceRoles, 'audit_log.actor.role'),
    },
    target_type: string(item.target_type, 'audit_log.target_type'),
    target_id: nullableString(item.target_id, 'audit_log.target_id'),
    result: enumeration(item.result, ['SUCCESS'] as const, 'audit_log.result'),
    metadata,
    request_id: string(item.request_id, 'audit_log.request_id'),
    created_at: string(item.created_at, 'audit_log.created_at'),
  }
}

export function parseAuditLogPage(value: unknown): AuditLogPage {
  const page = object(value, 'audit_logs')
  if (!Array.isArray(page.items)) invalidResponse('audit_logs.items')
  return {
    items: page.items.map(parseAuditLog),
    next_cursor: nullableString(page.next_cursor, 'audit_logs.next_cursor'),
  }
}
