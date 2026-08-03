<script setup lang="ts">
import { watch } from 'vue'
import AppShell from '@/components/AppShell.vue'
import TodoList from '@/components/TodoList.vue'
import { useAgentStore } from '@/stores/agent'
import { useWorkspaceStore } from '@/stores/workspaces'

const agent = useAgentStore()
const workspaces = useWorkspaceStore()

watch(
  () => workspaces.activeWorkspaceId,
  (workspaceId) => {
    if (workspaceId) void agent.loadTodos()
  },
  { immediate: true },
)
</script>

<template>
  <AppShell>
    <section class="page-heading todo-page-heading">
      <div>
        <p class="eyebrow">人工审批后的行动结果</p>
        <h1>待办</h1>
        <p>这里只展示当前 Workspace 内的待办。Agent 提案不会绕过审批直接创建内容。</p>
      </div>
      <router-link :to="{ name: 'workspace-agent', params: { workspaceId: workspaces.activeWorkspaceId } }">
        <el-button type="primary">返回 Agent</el-button>
      </router-link>
    </section>
    <el-alert v-if="agent.todoErrorMessage" type="error" :title="agent.todoErrorMessage" show-icon :closable="false">
      <template #default><el-button link type="primary" @click="agent.loadTodos">重新加载</el-button></template>
    </el-alert>
    <TodoList v-if="agent.todos.length || agent.loadingTodos" :items="agent.todos" :loading="agent.loadingTodos" />
    <el-empty v-else-if="!agent.todoErrorMessage" description="当前空间还没有待办" />
  </AppShell>
</template>
