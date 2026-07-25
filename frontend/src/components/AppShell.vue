<script setup lang="ts">
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
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
      <router-link class="brand" to="/workspace" aria-label="返回灵感工作台">
        <span class="brand-mark" aria-hidden="true">✦</span>
        <span>
          <strong>灵感拾光</strong>
          <small>把转瞬即逝变成随手可得</small>
        </span>
      </router-link>
      <div class="header-actions">
        <span class="user-name">{{ auth.user?.display_name }}</span>
        <el-button text aria-label="退出登录" @click="handleLogout">退出</el-button>
      </div>
    </header>
    <main class="app-main">
      <slot />
    </main>
  </div>
</template>
