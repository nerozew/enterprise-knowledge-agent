<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/workspaces'
import CreateWorkspaceDialog from './CreateWorkspaceDialog.vue'

const auth = useAuthStore()
const workspaces = useWorkspaceStore()
const router = useRouter()
const createOpen = ref(false)

async function switchWorkspace(workspaceId: string) {
  if (!auth.user || workspaceId === workspaces.activeWorkspaceId) return
  if (!workspaces.select(workspaceId, auth.user.id)) {
    ElMessage.error('这个空间已不可访问，请刷新空间列表')
    return
  }
  await router.push({ name: 'workspace-home', params: { workspaceId } })
}

async function retry() {
  if (!auth.user) return
  try {
    await workspaces.initialize(auth.user.id, true)
  } catch {
    // Store exposes a safe message next to the switcher.
  }
}

async function enterCreated(workspaceId: string) {
  await router.push({ name: 'workspace-home', params: { workspaceId } })
}
</script>

<template>
  <div class="workspace-switcher">
    <el-select
      v-if="workspaces.items.length"
      :model-value="workspaces.activeWorkspaceId"
      :loading="workspaces.loading"
      aria-label="当前 Workspace"
      class="workspace-select"
      @change="switchWorkspace"
    >
      <el-option
        v-for="workspace in workspaces.items"
        :key="workspace.id"
        :label="workspace.name"
        :value="workspace.id"
      >
        <span class="workspace-option-name">{{ workspace.name }}</span>
        <span class="workspace-option-meta">{{ workspace.kind }} · {{ workspace.role }}</span>
      </el-option>
    </el-select>
    <span v-else-if="workspaces.loading" class="workspace-loading">正在加载空间…</span>
    <span v-else-if="workspaces.errorMessage" class="workspace-error">
      空间加载失败
      <el-button link type="primary" @click="retry">重试</el-button>
    </span>
    <span v-else class="workspace-error">暂无可访问空间</span>
    <el-button class="create-workspace-button" text @click="createOpen = true">＋ 新建团队空间</el-button>
    <CreateWorkspaceDialog v-model="createOpen" @created="enterCreated" />
  </div>
</template>
