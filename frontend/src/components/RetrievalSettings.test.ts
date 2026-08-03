import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { defaultKnowledgeSearchInput } from '@/stores/knowledgeSearch'
import RetrievalSettings from './RetrievalSettings.vue'

describe('检索设置', () => {
  it('默认关闭检索诊断并可显式开启 Query Trace', async () => {
    const wrapper = mount(RetrievalSettings, {
      props: {
        modelValue: structuredClone(defaultKnowledgeSearchInput),
      },
      global: {
        stubs: {
          ElCollapse: { template: '<section><slot /></section>' },
          ElCollapseItem: { template: '<section><slot name="title" /><slot /></section>' },
          ElInputNumber: true,
          ElOption: true,
          ElRadioButton: { template: '<span><slot /></span>' },
          ElRadioGroup: { template: '<div><slot /></div>' },
          ElSelect: { template: '<div><slot /></div>' },
          ElCheckbox: {
            props: ['modelValue'],
            emits: ['update:modelValue'],
            template: '<button class="checkbox-stub" @click="$emit(\'update:modelValue\', !modelValue)"><slot /></button>',
          },
        },
      },
    })

    const toggle = wrapper.findAll('.checkbox-stub')
      .find((item) => item.text().includes('显示实际检索查询'))
    expect(toggle).toBeDefined()
    await toggle?.trigger('click')

    const updates = wrapper.emitted('update:modelValue') ?? []
    expect(updates.at(-1)?.[0]).toMatchObject({ include_query_trace: true })
  })
})
