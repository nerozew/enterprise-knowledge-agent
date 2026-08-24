import { mount } from '@vue/test-utils'
import ElementPlus, { type UploadFile, type UploadFiles, type UploadRawFile } from 'element-plus'
import { describe, expect, it } from 'vitest'
import DocumentUploadPanel from './DocumentUploadPanel.vue'

describe('知识文档上传面板', () => {
  it('接受 PPTX 并默认使用 AUTO OCR 模式提交', async () => {
    const wrapper = mount(DocumentUploadPanel, {
      props: { busy: false },
      global: { plugins: [ElementPlus] },
    })
    const raw = Object.assign(
      new File(['slides'], 'roadmap.pptx', {
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      }),
      { uid: 1 },
    ) as UploadRawFile
    const uploadFile = {
      name: raw.name,
      percentage: 0,
      status: 'ready',
      size: raw.size,
      raw,
      uid: raw.uid,
    } satisfies UploadFile
    const upload = wrapper.findComponent({ name: 'ElUpload' })
    const onChange = upload.props('onChange') as (
      file: UploadFile,
      files: UploadFiles,
    ) => void

    onChange(uploadFile, [uploadFile])
    await wrapper.vm.$nextTick()
    const submitButton = wrapper.findAll('button')
      .find((button) => button.text().includes('上传并构建索引'))
    await submitButton?.trigger('click')

    const submitted = wrapper.emitted('submit')?.[0]?.[0]
    expect(submitted).toMatchObject({
      title: 'roadmap',
      duplicate_policy: 'REUSE_EXISTING',
      ocr_mode: 'AUTO',
    })
    expect((submitted as { file: File }).file.name).toBe('roadmap.pptx')
    expect(upload.props('accept')).toContain('.pptx')
    wrapper.unmount()
  })
})
