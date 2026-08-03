<script setup lang="ts">
import type { TodoSummary } from '@/api/agentTypes'

defineProps<{
  items: TodoSummary[]
  loading?: boolean
}>()

function statusText(status: TodoSummary['status']): string {
  return { OPEN: '待处理', COMPLETED: '已完成', CANCELLED: '已取消' }[status]
}

function statusType(status: TodoSummary['status']): 'primary' | 'success' | 'info' {
  if (status === 'OPEN') return 'primary'
  if (status === 'COMPLETED') return 'success'
  return 'info'
}
</script>

<template>
  <div v-loading="loading" class="todo-list" aria-live="polite">
    <article v-for="item in items" :key="item.id" class="todo-card">
      <header>
        <div>
          <h3>{{ item.title }}</h3>
          <p v-if="item.description">{{ item.description }}</p>
        </div>
        <el-tag :type="statusType(item.status)" effect="plain">{{ statusText(item.status) }}</el-tag>
      </header>
      <footer>
        <span>{{ item.due_at ? `截止 ${new Date(item.due_at).toLocaleString()}` : '未设置截止时间' }}</span>
        <span>来自已审批 Agent 运行</span>
      </footer>
    </article>
  </div>
</template>
