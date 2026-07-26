import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import CitationCard from './CitationCard.vue'

describe('引用卡片', () => {
  it('显示固定版本、页码、Span 和分数明细', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/w/:workspaceId/knowledge', name: 'workspace-knowledge', component: { template: '<div />' } }],
    })
    await router.push('/w/workspace-1/knowledge')
    const wrapper = mount(CitationCard, {
      props: {
        workspaceId: 'workspace-1',
        rank: 1,
        hit: {
          chunk_id: 'chunk-1',
          document_id: 'doc-1',
          document_version_id: 'version-123456',
          title: '采购制度',
          page: 3,
          heading_path: ['审批', '大额采购'],
          quote: '超过十万元需要总经理审批。',
          source_span_id: 'span-123456789',
          score: 0.91,
          scores: { dense: 0.8, sparse: 0.7, fusion: 0.75, rerank: 0.91 },
          context: '审批制度上下文',
        },
      },
      global: { plugins: [router] },
    })

    expect(wrapper.text()).toContain('第 3 页')
    expect(wrapper.text()).toContain('发布版本 version-1234')
    expect(wrapper.text()).toContain('Span span-1234567')
    expect(wrapper.text()).toContain('Dense 0.800')
    expect(wrapper.find('a').attributes('href')).toContain('version=version-123456')
  })
})
