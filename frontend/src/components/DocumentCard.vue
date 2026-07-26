<script setup lang="ts">
import { computed } from 'vue'
import type {
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
    </p>

    <template v-if="job">
      <el-progress
        :percentage="Math.max(0, Math.min(100, job.progress))"
        :status="progressStatus || undefined"
        :stroke-width="8"
      />
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
  </article>
</template>
