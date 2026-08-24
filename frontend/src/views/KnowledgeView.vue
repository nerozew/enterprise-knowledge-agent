<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import DocumentUploadPanel from '@/components/DocumentUploadPanel.vue'
import DocumentCard from '@/components/DocumentCard.vue'
import { mapApiError } from '@/api/client'
import type {
  DocumentFormat,
  DocumentQuery,
  DocumentUploadInput,
  DocumentVersionStatus,
  IngestionJobSummary,
} from '@/api/types'
import { useDocumentStore } from '@/stores/documents'
import { useWorkspaceStore } from '@/stores/workspaces'

const documents = useDocumentStore()
const workspaces = useWorkspaceStore()
const route = useRoute()
const uploadPanel = ref<InstanceType<typeof DocumentUploadPanel>>()
const q = ref('')
const sourceFormat = ref<DocumentFormat | ''>('')
const versionStatus = ref<DocumentVersionStatus | ''>('')
const readonly = computed(() => !workspaces.canWrite)
const selectedDocumentId = computed(() => typeof route.query.document === 'string' ? route.query.document : '')
const page = computed(() => documents.query.page ?? 1)
const pageSize = computed(() => documents.query.page_size ?? 12)

watch(
  () => workspaces.activeWorkspaceId,
  async (workspaceId) => {
    q.value = ''
    sourceFormat.value = ''
    versionStatus.value = ''
    if (workspaceId) {
      await documents.load({
        q: undefined,
        source_format: undefined,
        version_status: readonly.value ? 'PUBLISHED' : undefined,
        page: 1,
      })
    }
  },
  { immediate: true },
)

watch(readonly, (isReadonly, wasReadonly) => {
  if (isReadonly === wasReadonly || !workspaces.activeWorkspaceId) return
  versionStatus.value = ''
  void documents.load({
    q: q.value.trim() || undefined,
    source_format: sourceFormat.value || undefined,
    version_status: isReadonly ? 'PUBLISHED' : undefined,
    page: 1,
  })
})

watch(selectedDocumentId, async (documentId) => {
  if (!documentId) return
  await nextTick()
  document.querySelector(`[data-document-id="${documentId}"]`)?.scrollIntoView({
    behavior: 'smooth',
    block: 'center',
  })
}, { immediate: true })

async function upload(input: DocumentUploadInput) {
  try {
    const result = await documents.upload(input)
    if (!result) return
    uploadPanel.value?.reset()
    ElMessage.success(result.reused ? '已复用相同内容的发布版本' : '上传成功，摄取任务已启动')
  } catch (error) {
    ElMessage.error(mapApiError(error).message)
  }
}

function applyFilters() {
  const patch: DocumentQuery = {
    q: q.value.trim() || undefined,
    source_format: sourceFormat.value || undefined,
    version_status: readonly.value ? 'PUBLISHED' : versionStatus.value || undefined,
    page: 1,
  }
  void documents.load(patch)
}

async function retry(job: IngestionJobSummary) {
  try {
    await documents.retry(job)
    ElMessage.success('已创建重试任务，旧失败记录仍会保留')
  } catch (error) {
    ElMessage.error(mapApiError(error).message)
  }
}

async function cancel(job: IngestionJobSummary) {
  try {
    await documents.cancel(job)
    ElMessage.success('已请求取消，系统会先清理未完成产物')
  } catch (error) {
    ElMessage.error(mapApiError(error).message)
  }
}
</script>

<template>
  <AppShell>
    <el-alert
      v-if="readonly"
      class="workspace-notice"
      type="info"
      title="你当前是 VIEWER，可以浏览已发布文档，但不能上传、重试或取消摄取。"
      show-icon
      :closable="false"
    />
    <DocumentUploadPanel v-if="!readonly" ref="uploadPanel" :busy="documents.uploading" @submit="upload" />

    <el-alert
      v-if="documents.uploadMessage"
      class="knowledge-message"
      type="success"
      :title="documents.uploadMessage"
      show-icon
      closable
    />
    <section class="knowledge-library" aria-labelledby="knowledge-title">
      <div class="timeline-heading">
        <div>
          <p class="eyebrow">已发布知识</p>
          <h2 id="knowledge-title">{{ documents.total ? `共 ${documents.total} 个文档` : '知识文档' }}</h2>
        </div>
        <router-link
          v-if="documents.hasItems"
          :to="{ name: 'workspace-ask', params: { workspaceId: workspaces.activeWorkspaceId } }"
        >
          去提问 →
        </router-link>
      </div>
      <form class="document-filters" @submit.prevent="applyFilters">
        <el-input v-model="q" clearable placeholder="搜索文档标题" aria-label="搜索文档标题" />
        <el-select v-model="sourceFormat" clearable placeholder="全部格式" aria-label="按格式筛选">
          <el-option v-for="format in ['PDF', 'DOCX', 'PPTX', 'MARKDOWN', 'TXT']" :key="format" :label="format" :value="format" />
        </el-select>
        <el-select
          v-if="!readonly"
          v-model="versionStatus"
          clearable
          placeholder="全部状态"
          aria-label="按状态筛选"
        >
          <el-option label="已发布" value="PUBLISHED" />
          <el-option label="处理中" value="PUBLISHING" />
          <el-option label="失败" value="FAILED" />
        </el-select>
        <el-button native-type="submit">筛选</el-button>
      </form>

      <el-alert v-if="documents.errorMessage" type="error" :title="documents.errorMessage" show-icon :closable="false">
        <template #default><el-button link type="primary" @click="documents.load()">重新加载</el-button></template>
      </el-alert>
      <div v-if="documents.hasItems || documents.loading" v-loading="documents.loading" class="document-grid">
        <div
          v-for="item in documents.items"
          :key="item.id"
          :data-document-id="item.id"
        >
          <DocumentCard
            :document="item"
            :version="documents.currentVersions[item.id]"
            :job="documents.jobsByDocument[item.id]"
            :readonly="readonly"
            :busy="documents.mutatingJobId === documents.jobsByDocument[item.id]?.id"
            :selected="selectedDocumentId === item.id"
            @retry="retry"
            @cancel="cancel"
          />
        </div>
      </div>
      <el-empty
        v-else-if="!documents.errorMessage"
        :description="q || sourceFormat || versionStatus ? '没有匹配的文档' : readonly ? '这个空间还没有已发布文档' : '上传第一份文档，开始构建企业知识库'"
      />
      <el-pagination
        v-if="documents.total > pageSize"
        class="pagination"
        background
        layout="prev, pager, next"
        :current-page="page"
        :page-size="pageSize"
        :total="documents.total"
        @current-change="documents.load({ page: $event })"
      />
    </section>
  </AppShell>
</template>
