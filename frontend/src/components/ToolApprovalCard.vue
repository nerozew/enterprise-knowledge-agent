<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ApprovalSummary, ToolProposal } from '@/api/agentTypes'

const props = defineProps<{
  proposal: ToolProposal
  approval: ApprovalSummary
  busy: boolean
  readonly: boolean
}>()

const emit = defineEmits<{
  decide: [decision: 'APPROVED' | 'REJECTED', comment?: string]
}>()

const comment = ref('')
const currentTime = ref(Date.now())
let expiryTimer: ReturnType<typeof setTimeout> | null = null
const expiresAt = computed(() => new Date(props.approval.expires_at))
const isExpired = computed(
  () => props.approval.status === 'EXPIRED' || expiresAt.value.getTime() <= currentTime.value,
)
const canDecide = computed(
  () => props.approval.status === 'PENDING' && !isExpired.value && !props.busy && !props.readonly,
)
const statusText = computed(() => ({
  PENDING: isExpired.value ? '已过期' : '等待审批',
  APPROVED: '已批准',
  REJECTED: '已拒绝',
  EXPIRED: '已过期',
}[props.approval.status]))
const statusType = computed(() => {
  if (props.approval.status === 'APPROVED') return 'success'
  if (props.approval.status === 'REJECTED' || isExpired.value) return 'danger'
  return 'warning'
})
const formattedArguments = computed(() => JSON.stringify(props.approval.arguments, null, 2))

function scheduleExpiry() {
  if (expiryTimer !== null) clearTimeout(expiryTimer)
  currentTime.value = Date.now()
  const remaining = expiresAt.value.getTime() - currentTime.value
  if (remaining <= 0 || props.approval.status !== 'PENDING') return
  expiryTimer = setTimeout(scheduleExpiry, Math.min(remaining + 25, 2_147_483_647))
}

onMounted(scheduleExpiry)
watch(() => [props.approval.expires_at, props.approval.status], scheduleExpiry)
onBeforeUnmount(() => {
  if (expiryTimer !== null) clearTimeout(expiryTimer)
})
</script>

<template>
  <section class="tool-approval-card" aria-labelledby="approval-title">
    <header>
      <div>
        <p class="eyebrow">需要人工确认</p>
        <h3 id="approval-title">创建待办</h3>
      </div>
      <el-tag :type="statusType" effect="dark">{{ statusText }}</el-tag>
    </header>

    <el-descriptions :column="1" border size="small">
      <el-descriptions-item label="最终 Tool">
        <code>{{ approval.tool_name }}</code>
      </el-descriptions-item>
      <el-descriptions-item label="影响范围">
        {{ proposal.impact_scope }}
      </el-descriptions-item>
      <el-descriptions-item label="参数哈希">
        <code class="approval-hash">{{ approval.tool_args_hash }}</code>
      </el-descriptions-item>
      <el-descriptions-item label="审批版本">
        {{ approval.version }}
      </el-descriptions-item>
      <el-descriptions-item label="有效期至">
        {{ expiresAt.toLocaleString() }}
      </el-descriptions-item>
    </el-descriptions>

    <div class="approval-arguments">
      <strong>规范化参数（只读）</strong>
      <pre tabindex="0">{{ formattedArguments }}</pre>
    </div>

    <el-alert
      v-if="isExpired"
      type="error"
      title="审批已过期，不能继续执行。请重新发起待办请求。"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="approval.status === 'PENDING' && readonly"
      type="info"
      title="你当前是 VIEWER，可以查看审批详情，但不能批准或拒绝。"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="approval.status === 'APPROVED'"
      type="success"
      title="审批已批准；待办只会由服务端按幂等规则创建。"
      show-icon
      :closable="false"
    />
    <el-alert
      v-else-if="approval.status === 'REJECTED'"
      type="info"
      title="审批已拒绝，本次 Tool 不会执行。"
      show-icon
      :closable="false"
    />

    <template v-if="approval.status === 'PENDING' && !isExpired && !readonly">
      <el-input
        v-model="comment"
        type="textarea"
        :rows="2"
        maxlength="300"
        show-word-limit
        aria-label="审批备注"
        placeholder="补充审批备注（可选）"
      />
      <div class="approval-actions">
        <el-button :disabled="!canDecide" :loading="busy" @click="emit('decide', 'REJECTED', comment)">
          拒绝
        </el-button>
        <el-button type="primary" :disabled="!canDecide" :loading="busy" @click="emit('decide', 'APPROVED', comment)">
          批准并执行
        </el-button>
      </div>
      <small>批准请求会同时提交审批版本与参数哈希；前端不会直接创建待办。</small>
    </template>
  </section>
</template>
