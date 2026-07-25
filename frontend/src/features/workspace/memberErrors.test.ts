import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/client'
import { memberErrorMessage } from './memberErrors'

describe('成员管理错误提示', () => {
  it.each([
    ['last_owner_required', '至少需要保留一位有效 OWNER'],
    ['personal_workspace_owner_required', '个人空间的所有者不能被降级'],
    ['permission_denied', '没有管理该成员的权限'],
    ['resource_not_found', '空间或成员已不可访问'],
    ['version_conflict', '成员信息已被其他人更新'],
    ['idempotency_conflict', '请求内容与之前的操作冲突'],
    ['last_owner_protected', '至少需要保留一位有效 OWNER'],
    ['personal_owner_protected', '个人空间的所有者不能被降级'],
  ])('为 %s 提供不泄露资源信息的提示', (code, expected) => {
    const error = new ApiError('server message', { code, status: code === 'permission_denied' ? 403 : 409 })
    expect(memberErrorMessage(error)).toContain(expected)
  })
})
