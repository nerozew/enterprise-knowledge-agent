<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import AppShell from '@/components/AppShell.vue'
import AgentCitationCard from '@/components/AgentCitationCard.vue'
import ToolApprovalCard from '@/components/ToolApprovalCard.vue'
import { useAgentStore } from '@/stores/agent'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/workspaces'
import type { AgentEventType, AgentRoute, ApprovalDecision } from '@/api/agentTypes'

const agent = useAgentStore()
const auth = useAuthStore()
const workspaces = useWorkspaceStore()
const route = useRoute()
const router = useRouter()
const prompt = ref('')
const promptInput = ref<{ focus: () => void } | null>(null)

const workspaceId = computed(() => workspaces.activeWorkspaceId ?? '')
const snapshot = computed(() => agent.snapshot)
const routeLabel = computed(() => snapshot.value?.run.route
  ? ({
      DIRECT_ANSWER: '直接回答',
      KNOWLEDGE_QA: '知识问答',
      INSPIRATION_SEARCH: '灵感检索',
      TODO_CREATE: '待办创建',
    } satisfies Record<AgentRoute, string>)[snapshot.value.run.route]
  : '正在判断')
const streamText = computed(() => {
  if (!agent.runIsActive) return ''
  if (agent.streamState === 'RECONNECTING') {
    return agent.retryInMs ? `连接中断，${Math.ceil(agent.retryInMs / 1000)} 秒后重连` : '正在重新连接'
  }
  if (agent.streamState === 'CONNECTED') return '运行进度已连接'
  return '正在连接运行进度'
})
const terminalEvent = computed(() => agent.events[agent.events.length - 1]?.type ?? null)
const hasFinalAnswerMessage = computed(() => {
  const current = snapshot.value
  return Boolean(
    current?.final_answer
    && current.messages.some(
      (message) => message.role === 'ASSISTANT' && message.content === current.final_answer,
    ),
  )
})

function eventText(type: AgentEventType | null): string {
  if (!type) return '正在准备'
  return {
    'run.created': '运行已创建',
    'route.selected': '已选择处理路线',
    'retrieval.started': '正在检索可信内容',
    'retrieval.completed': '检索已完成',
    'tool.proposed': '已生成 Tool 提案',
    'tool.started': '审批通过，正在执行 Tool',
    'tool.completed': 'Tool 执行完成',
    'tool.failed': 'Tool 执行失败',
    'approval.required': '等待人工审批',
    'approval.resolved': '审批已处理',
    'todo.created': '待办已创建',
    'run.completed': '运行完成',
    'run.failed': '运行失败',
    'run.cancelled': '运行已取消',
  }[type]
}

watch(
  () => workspaces.activeWorkspaceId,
  async (nextWorkspaceId) => {
    if (!nextWorkspaceId) return
    await agent.loadConversations()
    const runId = typeof route.query.run === 'string' ? route.query.run : null
    if (runId) await agent.recoverRun(runId)
  },
  { immediate: true },
)

watch(
  () => agent.activeRunId,
  async (runId) => {
    if (!runId || route.query.run === runId) return
    await router.replace({ query: { ...route.query, run: runId } })
  },
)

watch(
  () => agent.membershipRefreshRevision,
  async (revision, previousRevision) => {
    if (revision === previousRevision || !auth.user) return
    try {
      await workspaces.initialize(auth.user.id, true)
    } catch {
      ElMessage.warning('审批权限已变化，但成员角色刷新失败，请稍后重试。')
    }
  },
)

async function startNewConversation() {
  agent.selectConversation(null)
  await router.replace({ query: {} })
  await nextTick()
  promptInput.value?.focus()
}

async function selectConversation(conversationId: string) {
  agent.selectConversation(conversationId)
  await router.replace({ query: {} })
}

async function send() {
  const message = prompt.value.trim()
  if (!message) return
  const runId = await agent.sendMessage(message)
  if (runId) prompt.value = ''
}

async function decide(decision: ApprovalDecision['decision'], comment?: string) {
  const succeeded = await agent.decideApproval(decision, comment)
  if (succeeded) ElMessage.success(decision === 'APPROVED' ? '已批准，待办已由服务端创建' : '已拒绝，本次操作不会执行')
}
</script>

<template>
  <AppShell>
    <section class="agent-hero">
      <div>
        <p class="eyebrow">单 Agent · 当前会话短期记忆</p>
        <h1>智能行动工作台</h1>
        <p>可以直接问答、检索当前空间知识与灵感，或提出待办创建请求。知识问答仅使用当前可信 Basic RAG。</p>
      </div>
      <router-link :to="{ name: 'workspace-todos', params: { workspaceId } }">
        <el-button>查看待办</el-button>
      </router-link>
    </section>

    <div class="agent-layout">
      <aside class="conversation-panel" aria-label="会话列表">
        <div class="conversation-heading">
          <strong>会话</strong>
          <el-button type="primary" plain size="small" @click="startNewConversation">新会话</el-button>
        </div>
        <el-skeleton v-if="agent.loadingConversations" :rows="4" animated />
        <el-alert v-else-if="agent.errorMessage && !agent.snapshot" type="error" :title="agent.errorMessage" :closable="false">
          <template #default><el-button link type="primary" @click="agent.loadConversations">重试</el-button></template>
        </el-alert>
        <el-empty v-else-if="!agent.conversations.length" description="还没有会话" :image-size="64" />
        <button
          v-for="item in agent.conversations"
          v-else
          :key="item.id"
          type="button"
          class="conversation-item"
          :class="{ active: item.id === agent.activeConversationId }"
          @click="selectConversation(item.id)"
        >
          <strong>{{ item.title || '未命名会话' }}</strong>
          <small>{{ item.last_message_at ? new Date(item.last_message_at).toLocaleString() : '尚未发送消息' }}</small>
        </button>
      </aside>

      <section class="agent-thread" aria-labelledby="agent-thread-title">
        <header class="agent-thread-heading">
          <div>
            <p class="eyebrow">{{ routeLabel }}</p>
            <h2 id="agent-thread-title">{{ agent.activeConversation?.title || '开始一段新会话' }}</h2>
          </div>
          <div v-if="snapshot" class="run-status">
            <el-tag :type="snapshot.run.status === 'FAILED' ? 'danger' : snapshot.run.status === 'COMPLETED' ? 'success' : 'warning'">
              {{ snapshot.run.status }}
            </el-tag>
            <small>{{ eventText(terminalEvent) }}</small>
          </div>
        </header>

        <el-alert
          v-if="streamText"
          :type="agent.streamState === 'RECONNECTING' ? 'warning' : 'info'"
          :title="streamText"
          show-icon
          :closable="false"
        />
        <el-alert v-if="agent.errorMessage && agent.snapshot" type="error" :title="agent.errorMessage" show-icon :closable="false">
          <template #default>
            <el-button v-if="agent.activeRunId" link type="primary" @click="agent.recoverRun(agent.activeRunId)">恢复运行状态</el-button>
          </template>
        </el-alert>

        <el-skeleton v-if="agent.loadingRun && !snapshot" class="agent-loading" :rows="8" animated />
        <div v-else-if="snapshot" class="agent-results" aria-live="polite">
          <article
            v-for="message in snapshot.messages"
            :key="message.id"
            class="agent-message"
            :class="`role-${message.role.toLowerCase()}`"
          >
            <small>{{ message.role === 'USER' ? '你' : message.role === 'ASSISTANT' ? 'Agent' : '系统' }}</small>
            <p>{{ message.content }}</p>
          </article>

          <article v-if="snapshot.final_answer && !hasFinalAnswerMessage" class="agent-message role-assistant">
            <small>Agent</small>
            <p>{{ snapshot.final_answer }}</p>
          </article>

          <el-alert
            v-if="snapshot.refusal"
            type="warning"
            :title="snapshot.refusal.message"
            :description="`证据门禁：${snapshot.refusal.code}`"
            show-icon
            :closable="false"
          />

          <section v-if="snapshot.citations.length" class="agent-evidence" aria-labelledby="agent-evidence-title">
            <div>
              <p class="eyebrow">固定发布版本</p>
              <h3 id="agent-evidence-title">回答引用</h3>
            </div>
            <AgentCitationCard
              v-for="citation in snapshot.citations"
              :key="citation.id"
              :citation="citation"
              :workspace-id="workspaceId"
            />
          </section>

          <section v-if="snapshot.inspiration_hits.length" class="agent-inspiration-results" aria-labelledby="inspiration-results-title">
            <h3 id="inspiration-results-title">匹配的灵感</h3>
            <article v-for="hit in snapshot.inspiration_hits" :key="hit.id">
              <h4>{{ hit.title || '未命名灵感' }}</h4>
              <p>{{ hit.summary || hit.content }}</p>
              <div>
                <el-tag v-for="tag in hit.tag_names" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
              </div>
            </article>
          </section>

          <ToolApprovalCard
            v-if="snapshot.tool_proposal && snapshot.approval"
            :proposal="snapshot.tool_proposal"
            :approval="snapshot.approval"
            :busy="agent.decidingApproval"
            :readonly="!workspaces.canWrite"
            @decide="decide"
          />

          <el-result
            v-if="snapshot.todo"
            icon="success"
            title="待办已创建"
            :sub-title="snapshot.todo.title"
          >
            <template #extra>
              <router-link :to="{ name: 'workspace-todos', params: { workspaceId } }">
                <el-button type="primary">查看待办列表</el-button>
              </router-link>
            </template>
          </el-result>
        </div>
        <el-empty v-else-if="!agent.loadingRun" description="输入问题，或告诉 Agent 需要创建的待办" />

        <form class="agent-composer" @submit.prevent="send">
          <label for="agent-prompt">发送给 Agent</label>
          <el-input
            id="agent-prompt"
            ref="promptInput"
            v-model="prompt"
            type="textarea"
            :rows="3"
            maxlength="4000"
            show-word-limit
            resize="none"
            :disabled="!agent.canSend"
            placeholder="例如：根据当前空间资料解释发布流程；或创建一条周五前完成复盘的待办"
            @keydown.ctrl.enter="send"
            @keydown.meta.enter="send"
          />
          <div>
            <small v-if="agent.runIsActive">当前运行完成或审批处理后才能发送下一条消息。</small>
            <small v-else>Ctrl / ⌘ + Enter 发送</small>
            <div>
              <el-button v-if="agent.runIsActive" :loading="agent.cancelling" @click="agent.cancelRun">取消运行</el-button>
              <el-button native-type="submit" type="primary" :loading="agent.sending" :disabled="!prompt.trim() || !agent.canSend">
                发送
              </el-button>
            </div>
          </div>
        </form>
      </section>
    </div>
  </AppShell>
</template>
