<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeQueryTrace } from '@/api/types'

const props = defineProps<{
  trace: KnowledgeQueryTrace
}>()

const items = computed(() => {
  let rewriteNumber = 0
  return props.trace.items.map((item) => {
    if (item.kind === 'ORIGINAL') {
      return { ...item, label: 'Original' }
    }
    rewriteNumber += 1
    return {
      ...item,
      label: `Rewrite ${item.index > 0 ? item.index : rewriteNumber}`,
    }
  })
})
</script>

<template>
  <section class="query-trace" aria-labelledby="query-trace-title">
    <h3 id="query-trace-title">实际检索查询</h3>
    <template v-if="trace.exposed">
      <ol>
        <li v-for="item in items" :key="`${item.index}-${item.kind}`">
          <strong>{{ item.label }}</strong>
          <p>{{ item.text }}</p>
        </li>
      </ol>
      <p class="query-trace-note">模型改写仅用于扩展检索，不代表答案。</p>
    </template>
    <p v-else class="query-trace-redacted">
      实际检索查询未开放<span v-if="trace.redaction_reason">：{{ trace.redaction_reason }}</span>
    </p>
  </section>
</template>
