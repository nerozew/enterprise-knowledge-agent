import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type {
  DocumentSummary,
  DocumentVersionSummary,
  IngestionJobSummary,
} from '@/api/types'
import DocumentCard from './DocumentCard.vue'

const document: DocumentSummary = {
  id: 'doc-1',
  workspace_id: 'workspace-1',
  title: '季度路线图',
  status: 'ACTIVE',
  version: 1,
  published_version_id: null,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:00:00Z',
}

const version: DocumentVersionSummary = {
  id: 'version-123456',
  document_id: 'doc-1',
  format: 'PPTX',
  mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  file_name: 'roadmap.pptx',
  file_size: 2048,
  file_sha256: 'a'.repeat(64),
  status: 'PUBLISHING',
  parser_profile: 'docling-v2',
  chunk_profile: 'parent-child-v2',
  index_profile: 'hybrid-v2',
  structure_schema_version: 2,
  layout_profile: 'docling-layout-v2',
  ocr_profile: 'rapidocr-ch-en',
  slide_count: 12,
  ocr_page_count: 2,
  parse_warnings: [{ code: 'low_confidence', message: '图中文字置信度较低', slide_no: 7 }],
  created_at: '2026-08-24T00:00:00Z',
  published_at: null,
}

const job: IngestionJobSummary = {
  id: 'job-1',
  workspace_id: 'workspace-1',
  document_id: 'doc-1',
  document_version_id: 'version-123456',
  status: 'PARSING',
  stage: 'PARSING',
  stage_detail: 'OCR',
  progress: 45,
  pages: { total: 12, ocr_required: 3, ocr_completed: 2 },
  warnings: [{ code: 'low_confidence', message: '图中文字置信度较低', slide_no: 7 }],
  attempt_count: 1,
  retryable: true,
  version: 1,
  created_at: '2026-08-24T00:00:00Z',
  updated_at: '2026-08-24T00:00:00Z',
}

describe('知识文档卡片', () => {
  it('展示 PPTX 结构、OCR 进度和去重后的诊断警告', () => {
    const wrapper = mount(DocumentCard, {
      props: {
        document,
        version,
        job,
        readonly: false,
        busy: false,
        selected: false,
      },
      global: {
        stubs: {
          ElButton: { template: '<button><slot /></button>' },
          ElProgress: { props: ['percentage'], template: '<div>进度 {{ percentage }}%</div>' },
          ElTag: { template: '<span><slot /></span>' },
        },
      },
    })

    expect(wrapper.text()).toContain('PPTX')
    expect(wrapper.text()).toContain('12 张幻灯片')
    expect(wrapper.text()).toContain('版面解析')
    expect(wrapper.text()).toContain('OCR 2 页')
    expect(wrapper.text()).toContain('12 页 · OCR 2/3')
    expect(wrapper.text()).toContain('第 7 张幻灯片：图中文字置信度较低')
    expect(wrapper.findAll('.parse-warnings li')).toHaveLength(1)
  })
})
