import { auditActionLabel } from './auditActions'

describe('审计动作名称', () => {
  it.each([
    ['membership.added', '添加成员'],
    ['membership.reinstated', '恢复成员'],
    ['membership.role_changed', '变更成员角色'],
    ['membership.suspended', '停用成员'],
    ['membership.activated', '恢复成员'],
    ['membership.removed', '移除成员'],
  ])('映射冻结动作 %s', (action, label) => {
    expect(auditActionLabel(action)).toBe(label)
  })

  it.each([
    ['membership.created', '添加成员'],
    ['membership.updated', '更新成员'],
  ])('兼容历史动作 %s', (action, label) => {
    expect(auditActionLabel(action)).toBe(label)
  })
})
