<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/workspaces'

const auth = useAuthStore()
const workspaces = useWorkspaceStore()
const router = useRouter()

async function resolveWorkspace(force = false) {
  if (!auth.user) return
  try {
    await workspaces.initialize(auth.user.id, force)
    if (workspaces.activeWorkspaceId) {
      await router.replace({
        name: 'workspace-home',
        params: { workspaceId: workspaces.activeWorkspaceId },
      })
    }
  } catch {
    // The page keeps the user in a retryable failure state.
  }
}

onMounted(() => resolveWorkspace())
</script>

<template>
  <main class="resolver-page" aria-live="polite">
    <section class="resolver-card">
      <span class="brand-mark large" aria-hidden="true">✦</span>
      <template v-if="workspaces.loading">
        <h1>正在打开你的知识工作台</h1>
        <el-skeleton :rows="3" animated />
      </template>
      <template v-else-if="workspaces.errorMessage">
        <h1>暂时无法加载空间</h1>
        <el-alert type="error" :title="workspaces.errorMessage" show-icon :closable="false" />
        <el-button type="primary" @click="resolveWorkspace(true)">重新加载</el-button>
      </template>
      <template v-else>
        <h1>还没有可访问的空间</h1>
        <p>请联系管理员恢复成员权限，或刷新后重试。</p>
        <el-button @click="resolveWorkspace(true)">刷新空间列表</el-button>
      </template>
    </section>
  </main>
</template>
