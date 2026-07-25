const auditActionLabels: Record<string, string> = {
  'workspace.created': '创建空间',
  'workspace.updated': '更新空间',
  'workspace.migration.completed': '完成空间数据迁移',
  'membership.added': '添加成员',
  'membership.reinstated': '恢复成员',
  'membership.role_changed': '变更成员角色',
  'membership.suspended': '停用成员',
  'membership.activated': '恢复成员',
  'membership.removed': '移除成员',
  'inspiration.created': '创建灵感',
  'inspiration.updated': '更新灵感',
  'inspiration.deleted': '删除灵感',
  'inspiration.restored': '恢复灵感',
  'tag.created': '创建标签',
  'tag.updated': '更新标签',
  'tag.deleted': '删除标签',
  'ai_generation.created': '生成 AI 内容',
  // 兼容冻结契约落地前产生的历史审计动作。
  'membership.created': '添加成员',
  'membership.updated': '更新成员',
}

export function auditActionLabel(action: string): string {
  return auditActionLabels[action] ?? action
}
