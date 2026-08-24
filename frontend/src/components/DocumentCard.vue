<script setup lang="ts">
import { computed } from 'vue'
import type {
  DiagnosticWarning,
  DocumentSummary,
  DocumentVersionSummary,
  IngestionJobSummary,
} from '@/api/types'

const props = defineProps<{
  document: DocumentSummary
  version: DocumentVersionSummary | undefined
  job: IngestionJobSummary | undefined
  readonly: boolean
  busy: boolean
  selected: boolean
}>()
defineEmits<{
  retry: [job: IngestionJobSummary]
  cancel: [job: IngestionJobSummary]
}>()

const stateLabel: Record<string, string> = {
  QUEUED: '等待处理',
  PARSING: '解析文档',
  CHUNKING: '结构切分',
  STORING: '保存内容',
  INDEXING: '建立索引',
  SUCCEEDED: '可检索',
  RETRY_WAIT: '等待重试',
  COMPENSATING: '清理失败产物',
  FAILED: '摄取失败',
  CANCELLED: '已取消',
  PUBLISHING: '发布中',
  PUBLISHED: '已发布',
  SUPERSEDED: '历史版本',
}
const activeState = computed(() => props.job?.status ?? props.version?.status ?? 'PUBLISHING')
const isTerminal = computed(() => ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(props.job?.status ?? ''))
const progressStatus = computed(() => props.job?.status === 'FAILED' ? 'exception' : props.job?.status === 'SUCCEEDED' ? 'success' : '')
const sizeLabel = computed(() => {
  const bytes = props.version?.file_size
  if (bytes === undefined) return ''
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB`
})
const pageProgressLabel = computed(() => {
  const pages = props.job?.pages
  if (!pages) return ''
  if (pages.ocr_required > 0) {
    return `${pages.total} 页 · OCR ${pages.ocr_completed}/${pages.ocr_required}`
  }
  return `${pages.total} 页 · 无需 OCR`
})
const structureCountLabel = computed(() => {
  if (props.version?.slide_count !== undefined && props.version.slide_count !== null) {
    return `${props.version.slide_count} 张幻灯片`
  }
  if (props.version?.page_count !== undefined && props.version.page_count !== null) {
    return `${props.version.page_count} 页`
  }
  return ''
})
const diagnosticWarnings = computed(() => [
  ...(props.job?.warnings ?? []),
  ...(props.version?.parse_warnings ?? []),
].filter((warning, index, all) => (
  all.findIndex((candidate) => JSON.stringify(candidate) === JSON.stringify(warning)) === index
)))

function warningLabel(warning: DiagnosticWarning): string {
  const message = warning.message
  const code = warning.code
  const locator = typeof warning.page_no === 'number'
    ? `第 ${warning.page_no} 页`
    : typeof warning.slide_no === 'number'
      ? `第 ${warning.slide_no} 张幻灯片`
      : ''
  const summary = typeof message === 'string' && message.trim()
    ? message.trim()
    : typeof code === 'string' && code.trim()
      ? code.trim()
      : '解析器返回了诊断信息'
  return locator ? `${locator}：${summary}` : summary
}
</script>

<template>
  <article class="document-card" :class="{ 'is-selected': selected }">
    <header>
      <div class="document-format">{{ version?.format ?? 'DOC' }}</div>
      <el-tag :type="activeState === 'FAILED' ? 'danger' : activeState === 'SUCCEEDED' || activeState === 'PUBLISHED' ? 'success' : 'warning'" effect="plain">
        {{ stateLabel[activeState] ?? activeState }}
      </el-tag>
    </header>
    <h3>{{ document.title }}</h3>
    <p class="document-meta">
      <span v-if="version">版本 {{ version.id.slice(0, 8) }}</span>
      <span v-if="version?.published_at">发布于 {{ new Date(version.published_at).toLocaleString() }}</span>
      <span v-if="sizeLabel">{{ sizeLabel }}</span>
      <span v-if="structureCountLabel">{{ structureCountLabel }}</span>
    </p>
    <div v-if="version?.layout_profile || version?.ocr_profile" class="parser-badges" aria-label="解析方式">
      <el-tag v-if="version.layout_profile" size="small" effect="plain">版面解析</el-tag>
      <el-tag
        v-if="(version.ocr_page_count ?? 0) > 0"
        size="small"
        type="warning"
        effect="plain"
      >
        OCR {{ version.ocr_page_count }} 页
      </el-tag>
      <el-tag v-else-if="version.layout_profile" size="small" type="success" effect="plain">
        原生文本
      </el-tag>
    </div>

    <template v-if="job">
      <el-progress
        :percentage="Math.max(0, Math.min(100, job.progress))"
        :status="progressStatus || undefined"
        :stroke-width="8"
      />
      <p v-if="job.stage_detail || pageProgressLabel" class="job-detail">
        <span v-if="job.stage_detail">{{ job.stage_detail }}</span>
        <span v-if="pageProgressLabel">{{ pageProgressLabel }}</span>
      </p>
      <p v-if="job.error_message" class="job-error" role="alert">{{ job.error_message }}</p>
      <div v-if="!readonly" class="document-actions">
        <el-button
          v-if="job.retryable"
          size="small"
          :loading="busy"
          @click="$emit('retry', job)"
        >
          重试
        </el-button>
        <el-button
          v-if="!isTerminal"
          size="small"
          text
          :loading="busy"
          @click="$emit('cancel', job)"
        >
          取消
        </el-button>
      </div>
    </template>
    <p v-else-if="version?.status === 'PUBLISHED'" class="published-note">
      已固定到不可变发布版本，可用于检索与引用。
    </p>
    <details v-if="diagnosticWarnings.length" class="parse-warnings">
      <summary>解析警告（{{ diagnosticWarnings.length }}）</summary>
      <ul>
        <li v-for="(warning, index) in diagnosticWarnings" :key="`${index}-${warningLabel(warning)}`">
          {{ warningLabel(warning) }}
        </li>
      </ul>
    </details>
  </article>
</template>
