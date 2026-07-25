import { mapApiError } from '@/api/client'

const memberErrorMessages: Record<string, string> = {
  last_owner_required: '团队空间至少需要保留一位有效 OWNER。',
  personal_workspace_owner_required: '个人空间的所有者不能被降级、停用或移除。',
  // 兼容 P1 冻结前短暂使用过的旧错误名。
  last_owner_protected: '团队空间至少需要保留一位有效 OWNER。',
  personal_owner_protected: '个人空间的所有者不能被降级、停用或移除。',
  membership_exists: '该用户已经是空间成员。',
  member_candidate_not_found: '未找到可加入的已注册用户。',
  role_not_assignable: '当前角色不能授予或管理这个成员角色。',
  invalid_member_transition: '成员状态已经变化，请刷新后重试。',
  version_conflict: '成员信息已被其他人更新，请刷新后重试。',
  idempotency_conflict: '请求内容与之前的操作冲突，请刷新后重试。',
  permission_denied: '你没有管理该成员的权限。',
  resource_not_found: '空间或成员已不可访问。',
}

export function memberErrorMessage(error: unknown): string {
  const mapped = mapApiError(error)
  return memberErrorMessages[mapped.code] ?? mapped.message
}
