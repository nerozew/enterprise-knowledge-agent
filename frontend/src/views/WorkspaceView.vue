<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppShell from '@/components/AppShell.vue'
import QuickCapture from '@/components/QuickCapture.vue'
import FilterBar from '@/components/FilterBar.vue'
import InspirationList from '@/components/InspirationList.vue'
import InspirationDrawer from '@/components/InspirationDrawer.vue'
import AiDrawer from '@/components/AiDrawer.vue'
import TagManagerDialog from '@/components/TagManagerDialog.vue'
import { mapApiError } from '@/api/client'
import type { Inspiration, InspirationCreate, InspirationQuery, InspirationUpdate } from '@/api/types'
import { useInspirationStore } from '@/stores/inspirations'
import { useTagStore } from '@/stores/tags'

const inspirations = useInspirationStore()
const tags = useTagStore()
const quickCapture = ref<InstanceType<typeof QuickCapture>>()
const selected = ref<Inspiration | null>(null)
const aiTarget = ref<Inspiration | null>(null)
const detailOpen = ref(false)
const aiOpen = ref(false)
const tagsOpen = ref(false)
const recentlyDeleted = ref<Inspiration | null>(null)
const page = computed(() => inspirations.query.page ?? 1)
const pageSize = computed(() => inspirations.query.page_size ?? 12)
const resultLabel = computed(() => inspirations.total ? `共 ${inspirations.total} 条灵感` : '你的灵感时间线')

onMounted(async () => {
  await Promise.all([inspirations.load(), tags.load()])
})

async function createInspiration(payload: InspirationCreate) {
  try {
    await inspirations.create(payload)
    quickCapture.value?.reset()
    ElMessage.success('灵感已保存')
  } catch (error) {
    ElMessage.error(mapApiError(error).message)
  }
}

function applyFilters(patch: InspirationQuery) {
  void inspirations.load(patch)
}

function openDetail(item: Inspiration) {
  selected.value = item
  detailOpen.value = true
}

function openAi(item: Inspiration) {
  aiTarget.value = item
  aiOpen.value = true
}

async function updateInspiration(id: string, payload: InspirationUpdate) {
  try {
    const updated = await inspirations.update(id, payload)
    selected.value = selected.value?.id === id ? updated : selected.value
    aiTarget.value = aiTarget.value?.id === id ? updated : aiTarget.value
    detailOpen.value = false
    aiOpen.value = false
    ElMessage.success('修改已保存')
  } catch (error) {
    const mapped = mapApiError(error)
    if (mapped.status === 409) {
      await ElMessageBox.alert('这条灵感已在其他位置更新。请刷新列表后重新编辑，以免覆盖较新的内容。', '内容版本冲突', {
        confirmButtonText: '刷新列表',
        type: 'warning',
      })
      detailOpen.value = false
      aiOpen.value = false
      await inspirations.load()
      return
    }
    ElMessage.error(mapped.message)
  }
}

async function removeInspiration(item: Inspiration) {
  try {
    await ElMessageBox.confirm('删除后灵感会进入可恢复状态。确定删除吗？', '删除灵感', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await inspirations.remove(item)
    recentlyDeleted.value = item
    ElMessage.success('已移到回收状态')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(mapApiError(error).message)
  }
}

async function undoDelete() {
  const item = recentlyDeleted.value
  if (!item) return
  try {
    await inspirations.restore(item)
    recentlyDeleted.value = null
    ElMessage.success('灵感已恢复')
  } catch (error) {
    const mapped = mapApiError(error)
    if (mapped.status === 409) await inspirations.load()
    ElMessage.error(mapped.message)
  }
}

async function reloadTags() {
  await tags.load()
  await inspirations.load()
}

async function changePage(nextPage: number) {
  await inspirations.load({ page: nextPage })
  await nextTick()
  document.querySelector('.timeline-heading')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <AppShell>
    <QuickCapture ref="quickCapture" :tags="tags.items" :busy="inspirations.mutating" @submit="createInspiration" />

    <el-alert
      v-if="recentlyDeleted"
      class="undo-alert"
      type="success"
      title="灵感已删除"
      :closable="true"
      @close="recentlyDeleted = null"
    >
      <template #default>
        <span>“{{ recentlyDeleted.title || '未命名灵感' }}”已移到回收状态。</span>
        <el-button link type="primary" :loading="inspirations.mutating" @click="undoDelete">撤销并恢复</el-button>
      </template>
    </el-alert>

    <section class="timeline-section" aria-labelledby="timeline-title">
      <div class="timeline-heading">
        <div>
          <p class="eyebrow">灵感时间线</p>
          <h2 id="timeline-title">{{ resultLabel }}</h2>
        </div>
      </div>
      <FilterBar :query="inspirations.query" :tags="tags.items" @change="applyFilters" @manage-tags="tagsOpen = true" />

      <el-alert v-if="inspirations.errorMessage" type="error" :title="inspirations.errorMessage" show-icon :closable="false">
        <template #default><el-button link type="primary" @click="inspirations.load()">重新加载</el-button></template>
      </el-alert>

      <InspirationList
        v-if="inspirations.hasItems || inspirations.loading"
        :items="inspirations.items"
        :loading="inspirations.loading"
        @select="openDetail"
        @remove="removeInspiration"
        @ai="openAi"
      />
      <el-empty
        v-else-if="!inspirations.errorMessage"
        :description="inspirations.query.q || inspirations.query.tag_ids?.length ? '没有找到匹配的灵感，试试调整筛选条件' : '还没有灵感，从上方记录第一条吧'"
      />

      <el-pagination
        v-if="inspirations.total > pageSize"
        class="pagination"
        background
        layout="prev, pager, next"
        :current-page="page"
        :page-size="pageSize"
        :total="inspirations.total"
        @current-change="changePage"
      />
    </section>

    <InspirationDrawer
      v-model="detailOpen"
      :inspiration="selected"
      :tags="tags.items"
      :busy="inspirations.mutating"
      @save="updateInspiration"
      @ai="openAi"
    />
    <AiDrawer
      v-model="aiOpen"
      :inspiration="aiTarget"
      :saving="inspirations.mutating"
      @save="updateInspiration"
    />
    <TagManagerDialog v-model="tagsOpen" @changed="reloadTags" />
  </AppShell>
</template>
