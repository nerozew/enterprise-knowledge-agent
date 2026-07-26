<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeHit } from '@/api/types'

const props = defineProps<{
  hit: KnowledgeHit
  workspaceId: string
  rank: number
}>()

const location = computed(() => [
  props.hit.page === null ? null : `第 ${props.hit.page} 页`,
  props.hit.heading_path.length ? props.hit.heading_path.join(' › ') : null,
].filter(Boolean).join(' · '))
</script>

<template>
  <article class="citation-card">
    <header>
      <span class="citation-rank">{{ rank }}</span>
      <div>
        <h3>{{ hit.title }}</h3>
        <p :title="`发布版本 ${hit.document_version_id}`">
          {{ location || '原文定位' }} · 发布版本 {{ hit.document_version_id.slice(0, 12) }}
        </p>
      </div>
      <strong>{{ hit.score.toFixed(3) }}</strong>
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
    <div class="score-breakdown" aria-label="分数明细">
      <span v-if="hit.scores.dense !== undefined">Dense {{ hit.scores.dense.toFixed(3) }}</span>
      <span v-if="hit.scores.sparse !== undefined">BM25 {{ hit.scores.sparse.toFixed(3) }}</span>
      <span>Fusion {{ hit.scores.fusion.toFixed(3) }}</span>
      <span v-if="hit.scores.rerank !== undefined">Rerank {{ hit.scores.rerank.toFixed(3) }}</span>
    </div>
  </article>
</template>
