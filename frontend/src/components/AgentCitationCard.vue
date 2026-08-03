<script setup lang="ts">
import { computed } from 'vue'
import type { AgentCitation } from '@/api/agentTypes'

const props = defineProps<{
  citation: AgentCitation
  workspaceId: string
}>()

const location = computed(() => [
  props.citation.page === null ? null : `第 ${props.citation.page} 页`,
  props.citation.heading_path.length ? props.citation.heading_path.join(' › ') : null,
].filter(Boolean).join(' · '))
</script>

<template>
  <article class="agent-citation-card">
    <header>
      <span class="citation-rank">{{ citation.ordinal }}</span>
      <div>
        <h4>{{ citation.document_title }}</h4>
        <p>{{ location || '原文定位' }}</p>
      </div>
    </header>
    <blockquote>{{ citation.quote }}</blockquote>
    <footer>
      <span :title="citation.document_version_id">发布版本 {{ citation.document_version_id.slice(0, 12) }}</span>
      <router-link
        :to="{
          name: 'workspace-knowledge',
          params: { workspaceId },
          query: {
            document: citation.document_id,
            version: citation.document_version_id,
            ...(citation.page === null ? {} : { page: String(citation.page) }),
            span: citation.source_span_id,
          },
        }"
      >
        定位原文
      </router-link>
    </footer>
  </article>
</template>
