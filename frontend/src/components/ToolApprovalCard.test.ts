import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it } from 'vitest'
import ToolApprovalCard from './ToolApprovalCard.vue'

describe('ToolApprovalCard', () => {
  it('展示最终 Tool、只读参数、影响、哈希并提交审批决定', async () => {
    const wrapper = mount(ToolApprovalCard, {
      props: {
        proposal: {
          tool_call_id: 'tool-1', tool_name: 'todo_create', arguments: { title: '完成复盘' },
          tool_args_hash: 'a'.repeat(64), impact_scope: '当前 Workspace 创建 1 条待办', requires_approval: true,
        },
        approval: {
          id: 'approval-1', workspace_id: 'workspace-1', run_id: 'run-1', tool_call_id: 'tool-1',
          requested_by_user_id: 'user-1', tool_name: 'todo_create', arguments: { title: '完成复盘' },
          tool_args_hash: 'a'.repeat(64), status: 'PENDING', version: 3,
          expires_at: '2099-08-03T00:00:00Z', resolved_at: null,
          created_at: '2026-08-03T00:00:00Z', updated_at: '2026-08-03T00:00:00Z',
        },
        busy: false,
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('todo_create')
    expect(wrapper.text()).toContain('当前 Workspace 创建 1 条待办')
    expect(wrapper.text()).toContain('a'.repeat(64))
    expect(wrapper.find('pre').text()).toContain('完成复盘')
    expect(wrapper.find('pre').attributes('contenteditable')).toBeUndefined()

    const buttons = wrapper.findAll('button')
    await buttons.find((button) => button.text().includes('批准'))?.trigger('click')
    expect(wrapper.emitted('decide')?.[0]).toEqual(['APPROVED', ''])
    wrapper.unmount()
  })
})
