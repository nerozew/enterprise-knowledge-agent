<script setup lang="ts">
import type { Inspiration } from '@/api/types'

defineProps<{ items: Inspiration[]; loading: boolean; readonly?: boolean }>()
const emit = defineEmits<{ select: [item: Inspiration]; remove: [item: Inspiration]; ai: [item: Inspiration] }>()

const formatter = new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未知' : formatter.format(date)
}
</script>

<template>
  <div v-loading="loading" class="inspiration-grid" aria-live="polite">
    <article
      v-for="item in items"
      :key="item.id"
      class="inspiration-card"
      tabindex="0"
      @click="emit('select', item)"
      @keydown.enter="emit('select', item)"
    >
      <div class="card-topline">
        <span>{{ formatDate(item.updated_at) }}</span>
        <span v-if="!readonly" class="card-actions" @click.stop>
          <el-button text size="small" aria-label="AI 加工" @click="emit('ai', item)">AI</el-button>
          <el-button text size="small" type="danger" aria-label="删除灵感" @click="emit('remove', item)">删除</el-button>
        </span>
      </div>
      <h3>{{ item.title || '未命名灵感' }}</h3>
      <p>{{ item.content }}</p>
      <div v-if="item.tags.length" class="tag-row">
        <span
          v-for="tag in item.tags"
          :key="tag.id"
          class="soft-tag"
          :style="{ '--tag-color': tag.color ?? '#5A7D68' }"
        >
          {{ tag.name }}
        </span>
      </div>
      <p v-if="item.summary" class="summary-line"><strong>摘要</strong>{{ item.summary }}</p>
    </article>
  </div>
</template>
