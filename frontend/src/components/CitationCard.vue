<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { mapApiError } from '@/api/client'
import { documentApi } from '@/api/documents'
import type {
  KnowledgeHit,
  KnowledgeSearchScoring,
  KnowledgeSourceSpan,
  SourceSpanLocator,
} from '@/api/types'
import { buildScorePresentation } from '@/features/knowledge/scorePresentation'

const props = defineProps<{
  hit: KnowledgeHit
  workspaceId: string
  rank: number
  scoring?: KnowledgeSearchScoring | undefined
  supportsAnswer?: boolean
}>()

const citationSpans = computed<KnowledgeSourceSpan[]>(() => props.hit.source_spans?.length
  ? props.hit.source_spans
  : [{
      source_span_id: props.hit.source_span_id,
      page_no: props.hit.page,
      slide_no: null,
      bbox: null,
      excerpt: props.hit.quote,
    }])
const primarySpan = computed(() => (
  citationSpans.value.find((span) => span.source_span_id === props.hit.source_span_id)
  ?? citationSpans.value[0]
))
const hasResolvableSourceSpans = computed(() => Boolean(props.hit.source_spans?.length))
const location = computed(() => [
  primarySpan.value?.slide_no === null || primarySpan.value?.slide_no === undefined
    ? null
    : `第 ${primarySpan.value.slide_no} 张幻灯片`,
  primarySpan.value?.page_no === null || primarySpan.value?.page_no === undefined
    ? null
    : `第 ${primarySpan.value.page_no} 页`,
  props.hit.heading_path.length ? props.hit.heading_path.join(' › ') : null,
].filter(Boolean).join(' · '))
const score = computed(() => buildScorePresentation(props.hit, props.scoring, props.rank))
const sourceDialogVisible = ref(false)
const sourceLoading = ref(false)
const sourceError = ref('')
const sourceLocator = ref<SourceSpanLocator | null>(null)
const requestedSpanId = ref('')
let sourceController: AbortController | null = null

function bboxLabel(bbox: Record<string, number> | null): string {
  if (!bbox) return '未提供'
  return Object.entries(bbox)
    .map(([name, value]) => `${name}=${Number(value.toFixed(4))}`)
    .join('，') || '未提供'
}

async function openSourceSpan(sourceSpanId: string) {
  sourceController?.abort()
  const controller = new AbortController()
  sourceController = controller
  requestedSpanId.value = sourceSpanId
  sourceDialogVisible.value = true
  sourceLoading.value = true
  sourceError.value = ''
  sourceLocator.value = null
  try {
    sourceLocator.value = await documentApi.sourceSpan(
      props.workspaceId,
      props.hit.document_id,
      props.hit.document_version_id,
      sourceSpanId,
      controller.signal,
    )
  } catch (error) {
    if (!controller.signal.aborted) sourceError.value = mapApiError(error).message
  } finally {
    if (sourceController === controller) {
      sourceLoading.value = false
      sourceController = null
    }
  }
}

onBeforeUnmount(() => sourceController?.abort())
</script>

<template>
  <article class="citation-card">
    <header>
      <span class="citation-rank">{{ rank }}</span>
      <div>
        <div class="citation-title">
          <h3>{{ hit.title }}</h3>
          <el-tag v-if="supportsAnswer" size="small" type="success" effect="plain">
            支持答案
          </el-tag>
        </div>
        <p :title="`发布版本 ${hit.document_version_id}`">
          {{ location || '原文定位' }} · 发布版本 {{ hit.document_version_id.slice(0, 12) }}
        </p>
      </div>
      <el-tooltip :content="score.mainTooltip" placement="top">
        <span class="primary-score" tabindex="0">
          <small>{{ score.mainLabel }}</small>
          <strong>{{ score.mainValue }}</strong>
          <span>#{{ score.mainRank }}</span>
        </span>
      </el-tooltip>
    </header>
    <blockquote>{{ hit.quote }}</blockquote>
    <details v-if="hit.context">
      <summary>查看父章节与相邻上下文</summary>
      <p>{{ hit.context }}</p>
    </details>
    <footer>
      <el-button
        v-if="hasResolvableSourceSpans && primarySpan"
        link
        type="primary"
        @click="openSourceSpan(primarySpan.source_span_id)"
      >
        Span {{ primarySpan.source_span_id.slice(0, 12) }}
      </el-button>
      <span v-else class="legacy-source-span">
        Span {{ hit.source_span_id.slice(0, 12) }} · 旧版定位
      </span>
      <router-link
        :to="{
          name: 'workspace-knowledge',
          params: { workspaceId },
          query: {
            document: hit.document_id,
            version: hit.document_version_id,
            ...(primarySpan?.page_no === null || primarySpan?.page_no === undefined
              ? {}
              : { page: String(primarySpan.page_no) }),
            ...(primarySpan?.slide_no === null || primarySpan?.slide_no === undefined
              ? {}
              : { slide: String(primarySpan.slide_no) }),
            span: primarySpan?.source_span_id ?? hit.source_span_id,
          },
        }"
      >
        定位固定版本
      </router-link>
    </footer>
    <details v-if="citationSpans.length > 1" class="source-span-list">
      <summary>查看 {{ citationSpans.length }} 个来源定位</summary>
      <ul>
        <li v-for="span in citationSpans" :key="span.source_span_id">
          <el-button link type="primary" @click="openSourceSpan(span.source_span_id)">
            <template v-if="span.slide_no !== null">幻灯片 {{ span.slide_no }} · </template>
            <template v-if="span.page_no !== null">第 {{ span.page_no }} 页 · </template>
            Span {{ span.source_span_id.slice(0, 12) }}
          </el-button>
          <span>{{ span.excerpt }}</span>
        </li>
      </ul>
    </details>
    <details v-if="score.stages.length" class="score-details">
      <summary>查看排序分明细</summary>
      <div class="score-breakdown" aria-label="排序分明细">
        <el-tooltip
          v-for="item in score.stages.filter((entry) => entry.value !== null)"
          :key="item.key"
          :content="item.tooltip"
          placement="top"
        >
          <span tabindex="0">
            {{ item.label }} {{ item.value }}
            <template v-if="item.rank !== null"> · #{{ item.rank }}</template>
          </span>
        </el-tooltip>
      </div>
      <p
        v-if="score.stages.some((item) => item.value === null)"
        class="score-unavailable"
      >
        <span
          v-for="item in score.stages.filter((entry) => entry.value === null)"
          :key="item.key"
        >
          {{ item.label }}：{{ item.availabilityText }}
        </span>
      </p>
    </details>

    <el-dialog
      v-model="sourceDialogVisible"
      title="固定版本来源诊断"
      width="min(680px, 92vw)"
      :append-to-body="false"
      destroy-on-close
    >
      <div v-loading="sourceLoading" class="source-span-diagnostic">
        <el-alert
          v-if="sourceError"
          type="error"
          :title="sourceError"
          show-icon
          :closable="false"
        >
          <template #default>
            <el-button link type="primary" @click="openSourceSpan(requestedSpanId)">重新加载</el-button>
          </template>
        </el-alert>
        <template v-else-if="sourceLocator">
          <dl>
            <dt>文档版本</dt><dd>{{ sourceLocator.document_version_id }}</dd>
            <dt>Source Span</dt><dd>{{ sourceLocator.source_span_id }}</dd>
            <dt>页码</dt><dd>{{ sourceLocator.page_no ?? '不适用' }}</dd>
            <dt>幻灯片</dt><dd>{{ sourceLocator.slide_no ?? '不适用' }}</dd>
            <dt>版面坐标</dt><dd>{{ bboxLabel(sourceLocator.bbox) }}</dd>
            <dt>Block</dt><dd>{{ sourceLocator.block_ref ?? '未提供' }}</dd>
            <dt>Excerpt SHA256</dt><dd>{{ sourceLocator.excerpt_sha256 }}</dd>
          </dl>
          <blockquote>{{ sourceLocator.excerpt }}</blockquote>
        </template>
      </div>
    </el-dialog>
  </article>
</template>
