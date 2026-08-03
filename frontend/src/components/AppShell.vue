<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/workspaces'
import WorkspaceSwitcher from './WorkspaceSwitcher.vue'

const auth = useAuthStore()
const workspaces = useWorkspaceStore()
const router = useRouter()

async function handleLogout() {
  try {
    await ElMessageBox.confirm('退出后需要重新登录才能查看灵感，确定退出吗？', '退出登录', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
    })
    auth.logout()
    await router.replace({ name: 'login' })
  } catch {
    // 用户取消退出，无需反馈。
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <router-link
        class="brand"
        :to="workspaces.activeWorkspaceId
          ? { name: 'workspace-home', params: { workspaceId: workspaces.activeWorkspaceId } }
          : { name: 'workspace-resolve' }"
        aria-label="返回知识工作台"
      >
        <span class="brand-mark" aria-hidden="true">✦</span>
        <span>
          <strong>灵感拾光</strong>
          <small>企业知识工作台</small>
        </span>
      </router-link>
      <nav v-if="workspaces.activeWorkspaceId" class="primary-nav" aria-label="工作台主导航">
        <router-link :to="{ name: 'workspace-home', params: { workspaceId: workspaces.activeWorkspaceId } }">
          灵感
        </router-link>
        <router-link :to="{ name: 'workspace-knowledge', params: { workspaceId: workspaces.activeWorkspaceId } }">
          知识
        </router-link>
        <router-link :to="{ name: 'workspace-ask', params: { workspaceId: workspaces.activeWorkspaceId } }">
          问答
        </router-link>
        <router-link :to="{ name: 'workspace-agent', params: { workspaceId: workspaces.activeWorkspaceId } }">
          Agent
        </router-link>
        <router-link :to="{ name: 'workspace-todos', params: { workspaceId: workspaces.activeWorkspaceId } }">
          待办
        </router-link>
      </nav>
      <WorkspaceSwitcher />
      <div class="header-actions">
        <nav v-if="workspaces.canManage && workspaces.activeWorkspaceId" class="workspace-nav" aria-label="空间管理">
          <router-link :to="{ name: 'workspace-members', params: { workspaceId: workspaces.activeWorkspaceId } }">
            成员
          </router-link>
          <router-link :to="{ name: 'workspace-audit', params: { workspaceId: workspaces.activeWorkspaceId } }">
            审计
          </router-link>
        </nav>
        <span class="user-name">{{ auth.user?.display_name }}</span>
        <el-button text aria-label="退出登录" @click="handleLogout">退出</el-button>
      </div>
    </header>
    <main class="app-main">
      <slot />
    </main>
  </div>
</template>
