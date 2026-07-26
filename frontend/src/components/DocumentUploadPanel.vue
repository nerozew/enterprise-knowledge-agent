<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, type UploadFile, type UploadFiles } from 'element-plus'
import type { DocumentUploadInput, DuplicatePolicy } from '@/api/types'

const props = defineProps<{
  busy: boolean
}>()
const emit = defineEmits<{
  submit: [payload: DocumentUploadInput]
}>()

const file = ref<File | null>(null)
const title = ref('')
const duplicatePolicy = ref<DuplicatePolicy>('REUSE_EXISTING')
const fileList = ref<UploadFile[]>([])

function onChange(uploadFile: UploadFile, uploadFiles: UploadFiles) {
  const raw = uploadFile.raw
  if (!raw) return
  const extension = raw.name.split('.').pop()?.toLowerCase()
  if (!extension || !['pdf', 'docx', 'md', 'markdown', 'txt'].includes(extension)) {
    ElMessage.error('仅支持 PDF、DOCX、Markdown 和 TXT 文件')
    file.value = null
    fileList.value = []
    return
  }
  const maxBytes = ['md', 'markdown', 'txt'].includes(extension) ? 10 * 1024 * 1024 : 50 * 1024 * 1024
  if (raw.size > maxBytes) {
    ElMessage.error(['md', 'markdown', 'txt'].includes(extension) ? 'Markdown/TXT 不能超过 10 MiB' : '文件不能超过 50 MiB')
    file.value = null
    fileList.value = []
    return
  }
  file.value = raw
  fileList.value = uploadFiles.slice(-1)
  if (!title.value) title.value = raw.name.replace(/\.[^.]+$/, '')
}

function remove() {
  file.value = null
  fileList.value = []
}

function submit() {
  if (!file.value || props.busy) return
  emit('submit', {
    file: file.value,
    ...(title.value.trim() ? { title: title.value.trim() } : {}),
    duplicate_policy: duplicatePolicy.value,
  })
}

function reset() {
  file.value = null
  title.value = ''
  duplicatePolicy.value = 'REUSE_EXISTING'
  fileList.value = []
}

defineExpose({ reset })
</script>

<template>
  <section class="upload-panel" aria-labelledby="upload-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">发布到知识库</p>
        <h1 id="upload-title">让文档成为可追溯的答案依据</h1>
        <p>上传后会解析结构、生成父子 Chunk，并建立 Dense 与 BM25 索引。只有成功发布的不可变版本可被检索。</p>
      </div>
    </div>
    <div class="upload-grid">
      <el-upload
        v-model:file-list="fileList"
        class="knowledge-uploader"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".pdf,.docx,.md,.markdown,.txt"
        :on-change="onChange"
        :on-remove="remove"
      >
        <div class="upload-glyph" aria-hidden="true">↥</div>
        <div class="el-upload__text">拖入文件，或 <em>选择文件</em></div>
        <template #tip>
          <div class="el-upload__tip">PDF / DOCX 最大 50 MiB；Markdown / TXT 最大 10 MiB</div>
        </template>
      </el-upload>
      <div class="upload-fields">
        <label for="document-title">文档标题</label>
        <el-input id="document-title" v-model="title" maxlength="255" show-word-limit placeholder="默认使用文件名" />
        <label for="duplicate-policy">重复内容</label>
        <el-select id="duplicate-policy" v-model="duplicatePolicy">
          <el-option label="复用已存在版本（推荐）" value="REUSE_EXISTING" />
          <el-option label="明确创建新版本" value="CREATE_NEW" />
        </el-select>
        <el-button type="primary" size="large" :disabled="!file" :loading="busy" @click="submit">
          上传并构建索引
        </el-button>
      </div>
    </div>
  </section>
</template>
