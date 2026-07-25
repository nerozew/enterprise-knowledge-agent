<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AppShell from '@/components/AppShell.vue'
import { auditLogApi } from '@/api/auditLogs'
import { mapApiError } from '@/api/client'
import type { AuditLog, AuditLogQuery } from '@/api/types'
import { useWorkspaceStore } from '@/stores/workspaces'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'
import { auditActionLabel } from '@/features/workspace/auditActions'

const workspaces = useWorkspaceStore()
const items = ref<AuditLog[]>([])
const nextCursor = ref<string | null>(null)
const loading = ref(false)
const loadingMore = ref(false)
const errorMessage = ref('')
const filters = reactive<{ action: string; dates: string[] | null }>({ action: '', dates: null })
let activeController: AbortController | null = null

const workspaceId = computed(() => workspaces.activeWorkspaceId)

function buildQuery(cursor?: string): AuditLogQuery {
  const query: AuditLogQuery = { page_size: 50 }
  if (filters.action.trim()) query.action = filters.action.trim()
  if (filters.dates?.[0]) query.date_from = `${filters.dates[0]}T00:00:00Z`
  if (filters.dates?.[1]) query.date_to = `${filters.dates[1]}T23:59:59Z`
  if (cursor) query.cursor = cursor
  return query
}

function metadataText(metadata: Record<string, unknown>): string {
  return Object.keys(metadata).length ? JSON.stringify(metadata) : '无附加信息'
}

async function load(reset = true) {
  if (!workspaceId.value) return
  const cursor = reset ? undefined : nextCursor.value ?? undefined
  if (!reset && !cursor) return
  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  const targetWorkspaceId = workspaceId.value
  if (reset) loading.value = true
  else loadingMore.value = true
  errorMessage.value = ''
  try {
    const result = await auditLogApi.list(targetWorkspaceId, buildQuery(cursor), controller.signal)
    if (controller.signal.aborted || workspaceId.value !== targetWorkspaceId) return
    if (reset) items.value = result.items
    else {
      const existing = new Set(items.value.map((item) => item.id))
      items.value.push(...result.items.filter((item) => !existing.has(item.id)))
    }
    nextCursor.value = result.next_cursor
  } catch (error) {
    if (!controller.signal.aborted) {
      const mapped = mapApiError(error)
      if (mapped.status === 404 && mapped.code === 'resource_not_found') {
        items.value = []
        nextCursor.value = null
        reportWorkspaceAccessRevoked(targetWorkspaceId)
      }
      errorMessage.value = mapped.message
    }
  } finally {
    if (activeController === controller) {
      loading.value = false
      loadingMore.value = false
      activeController = null
    }
  }
}

function resetFilters() {
  filters.action = ''
  filters.dates = null
  void load(true)
}

watch(workspaceId, () => {
  items.value = []
  nextCursor.value = null
  void load(true)
})
onMounted(() => load(true))
onBeforeUnmount(() => activeController?.abort())
</script>

<template>
  <AppShell>
    <section class="management-page" aria-labelledby="audit-title">
      <header class="management-heading">
        <div>
          <p class="eyebrow">Workspace 审计</p>
          <h1 id="audit-title">{{ workspaces.activeWorkspace?.name }} · 操作记录</h1>
          <p>审计记录只追加保存，用于追踪空间和成员权限变化。</p>
        </div>
      </header>

      <form class="audit-filters" @submit.prevent="load(true)">
        <el-input v-model="filters.action" placeholder="动作，例如 membership.role_changed" clearable aria-label="筛选动作" />
        <el-date-picker
          v-model="filters.dates"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          aria-label="筛选日期范围"
        />
        <el-button type="primary" native-type="submit">筛选</el-button>
        <el-button @click="resetFilters">清空</el-button>
      </form>

      <el-alert v-if="errorMessage" type="error" :title="errorMessage" show-icon :closable="false">
        <template #default><el-button link type="primary" @click="load(true)">重新加载</el-button></template>
      </el-alert>
      <el-skeleton v-if="loading && !items.length" :rows="7" animated />
      <el-empty v-else-if="!loading && !errorMessage && !items.length" description="当前条件下没有审计记录" />
      <ol v-else-if="items.length" v-loading="loading" class="audit-timeline">
        <li v-for="entry in items" :key="entry.id">
          <div class="audit-dot" aria-hidden="true" />
          <article>
            <header>
              <strong>{{ auditActionLabel(entry.action) }}</strong>
              <time :datetime="entry.created_at">{{ new Date(entry.created_at).toLocaleString() }}</time>
            </header>
            <p>
              {{ entry.actor.display_name || '系统' }}
              <el-tag v-if="entry.actor.role" size="small">{{ entry.actor.role }}</el-tag>
              · {{ entry.target_type }}
            </p>
            <details>
              <summary>查看脱敏详情</summary>
              <code>{{ metadataText(entry.metadata) }}</code>
              <small>请求 ID：{{ entry.request_id }}</small>
            </details>
          </article>
        </li>
      </ol>
      <div v-if="nextCursor" class="load-more">
        <el-button :loading="loadingMore" @click="load(false)">加载更多</el-button>
      </div>
    </section>
  </AppShell>
</template>
