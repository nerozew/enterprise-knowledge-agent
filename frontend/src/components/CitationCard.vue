<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeHit, KnowledgeSearchScoring } from '@/api/types'
import { buildScorePresentation } from '@/features/knowledge/scorePresentation'

const props = defineProps<{
  hit: KnowledgeHit
  workspaceId: string
  rank: number
  scoring?: KnowledgeSearchScoring | undefined
  supportsAnswer?: boolean
}>()

const location = computed(() => [
  props.hit.page === null ? null : `第 ${props.hit.page} 页`,
  props.hit.heading_path.length ? props.hit.heading_path.join(' › ') : null,
].filter(Boolean).join(' · '))
const score = computed(() => buildScorePresentation(props.hit, props.scoring, props.rank))
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
      <span>Span {{ hit.source_span_id.slice(0, 12) }}</span>
      <router-link
        :to="{
          name: 'workspace-knowledge',
          params: { workspaceId },
          query: {
            document: hit.document_id,
            version: hit.document_version_id,
            ...(hit.page === null ? {} : { page: String(hit.page) }),
            span: hit.source_span_id,
          },
        }"
      >
        定位固定版本
      </router-link>
    </footer>
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
  </article>
</template>
