<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AppShell from '@/components/AppShell.vue'
import { workspaceApi } from '@/api/workspaces'
import { executeIdempotentIntent } from '@/api/idempotency'
import { mapApiError } from '@/api/client'
import type { MemberSummary, MemberUpdate, WorkspaceRole } from '@/api/types'
import { useWorkspaceStore } from '@/stores/workspaces'
import { memberErrorMessage } from '@/features/workspace/memberErrors'
import { reportWorkspaceAccessRevoked } from '@/features/workspace/accessRevocation'

const workspaces = useWorkspaceStore()
const members = ref<MemberSummary[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const mutatingUserId = ref<string | null>(null)
const errorMessage = ref('')
const addOpen = ref(false)
const adding = ref(false)
const addForm = reactive({ email: '', role: 'EDITOR' as WorkspaceRole })
let activeController: AbortController | null = null

const workspaceId = computed(() => workspaces.activeWorkspaceId)
const assignableRoles = computed<WorkspaceRole[]>(() => (
  workspaces.activeWorkspace?.role === 'OWNER'
    ? ['OWNER', 'ADMIN', 'EDITOR', 'VIEWER']
    : ['EDITOR', 'VIEWER']
))

function canOperate(member: MemberSummary): boolean {
  if (workspaces.activeWorkspace?.kind === 'PERSONAL' && member.role === 'OWNER') return false
  if (workspaces.activeWorkspace?.role === 'OWNER') return true
  return member.role === 'EDITOR' || member.role === 'VIEWER'
}

async function loadMembers() {
  if (!workspaceId.value) return
  activeController?.abort()
  const controller = new AbortController()
  activeController = controller
  const targetWorkspaceId = workspaceId.value
  loading.value = true
  errorMessage.value = ''
  try {
    const result = await workspaceApi.listMembers(
      targetWorkspaceId,
      { page: page.value, page_size: 50 },
      controller.signal,
    )
    if (controller.signal.aborted || workspaceId.value !== targetWorkspaceId) return
    members.value = result.items
    total.value = result.total
    page.value = result.page
  } catch (error) {
    if (!controller.signal.aborted) {
      const mapped = mapApiError(error)
      if (mapped.status === 404 && mapped.code === 'resource_not_found') {
        members.value = []
        total.value = 0
        reportWorkspaceAccessRevoked(targetWorkspaceId)
      }
      errorMessage.value = memberErrorMessage(mapped)
    }
  } finally {
    if (activeController === controller) {
      loading.value = false
      activeController = null
    }
  }
}

async function addMember() {
  if (!workspaceId.value || !addForm.email.trim()) {
    ElMessage.warning('请输入已注册用户的邮箱')
    return
  }
  adding.value = true
  try {
    const input = { email: addForm.email.trim(), role: addForm.role }
    await executeIdempotentIntent(
      `workspace:${workspaceId.value}:member.add`,
      input,
      (key) => workspaceApi.addMember(workspaceId.value!, input, key),
    )
    addOpen.value = false
    addForm.email = ''
    addForm.role = 'EDITOR'
    ElMessage.success('成员已加入空间')
    await loadMembers()
  } catch (error) {
    const mapped = mapApiError(error)
    if (mapped.status === 404 && mapped.code === 'resource_not_found' && workspaceId.value) {
      reportWorkspaceAccessRevoked(workspaceId.value)
    }
    ElMessage.error(memberErrorMessage(error))
  } finally {
    adding.value = false
  }
}

async function changeRole(member: MemberSummary, role: WorkspaceRole) {
  if (!workspaceId.value || role === member.role) return
  mutatingUserId.value = member.user_id
  try {
    const input = { role, version: member.version }
    await executeIdempotentIntent(
      `workspace:${workspaceId.value}:member.update:${member.user_id}`,
      input,
      (key) => workspaceApi.updateMember(workspaceId.value!, member.user_id, input, key),
    )
    ElMessage.success('成员角色已更新')
  } catch (error) {
    const mapped = mapApiError(error)
    if (mapped.status === 404 && mapped.code === 'resource_not_found' && workspaceId.value) {
      reportWorkspaceAccessRevoked(workspaceId.value)
    }
    ElMessage.error(memberErrorMessage(error))
  } finally {
    mutatingUserId.value = null
    await loadMembers()
  }
}

async function toggleStatus(member: MemberSummary) {
  if (!workspaceId.value) return
  const nextStatus = member.status === 'SUSPENDED' ? 'ACTIVE' : 'SUSPENDED'
  const impact = nextStatus === 'SUSPENDED'
    ? '停用后，该成员将立即无法访问这个空间。'
    : '恢复后，该成员将重新获得其角色对应的空间权限。'
  try {
    await ElMessageBox.confirm(impact, nextStatus === 'SUSPENDED' ? '停用成员' : '恢复成员', {
      confirmButtonText: nextStatus === 'SUSPENDED' ? '确认停用' : '确认恢复',
      cancelButtonText: '取消',
      type: 'warning',
    })
    mutatingUserId.value = member.user_id
    const input: MemberUpdate = { status: nextStatus, version: member.version }
    await executeIdempotentIntent(
      `workspace:${workspaceId.value}:member.update:${member.user_id}`,
      input,
      (key) => workspaceApi.updateMember(workspaceId.value!, member.user_id, input, key),
    )
    ElMessage.success(nextStatus === 'SUSPENDED' ? '成员已停用' : '成员已恢复')
    await loadMembers()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      const mapped = mapApiError(error)
      if (mapped.status === 404 && mapped.code === 'resource_not_found' && workspaceId.value) {
        reportWorkspaceAccessRevoked(workspaceId.value)
      }
      ElMessage.error(memberErrorMessage(error))
    }
  } finally {
    mutatingUserId.value = null
  }
}

async function removeMember(member: MemberSummary) {
  if (!workspaceId.value) return
  try {
    await ElMessageBox.confirm(
      '移除后，该成员将立即失去这个空间的访问权限；历史审计记录仍会保留。',
      '移除成员',
      {
        confirmButtonText: '确认移除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    mutatingUserId.value = member.user_id
    const input = { version: member.version }
    await executeIdempotentIntent(
      `workspace:${workspaceId.value}:member.delete:${member.user_id}`,
      input,
      (key) => workspaceApi.removeMember(workspaceId.value!, member.user_id, member.version, key),
    )
    ElMessage.success('成员已移除')
    await loadMembers()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      const mapped = mapApiError(error)
      if (mapped.status === 404 && mapped.code === 'resource_not_found' && workspaceId.value) {
        reportWorkspaceAccessRevoked(workspaceId.value)
      }
      ElMessage.error(memberErrorMessage(error))
    }
  } finally {
    mutatingUserId.value = null
  }
}

watch(workspaceId, () => {
  members.value = []
  page.value = 1
  void loadMembers()
})
onMounted(loadMembers)
onBeforeUnmount(() => activeController?.abort())
</script>

<template>
  <AppShell>
    <section class="management-page" aria-labelledby="members-title">
      <header class="management-heading">
        <div>
          <p class="eyebrow">Workspace 成员</p>
          <h1 id="members-title">{{ workspaces.activeWorkspace?.name }} · 成员管理</h1>
          <p>角色决定成员在当前空间内可执行的操作，前端提示不会替代服务端授权。</p>
        </div>
        <el-button type="primary" @click="addOpen = true">添加已注册成员</el-button>
      </header>

      <el-alert v-if="errorMessage" type="error" :title="errorMessage" show-icon :closable="false">
        <template #default><el-button link type="primary" @click="loadMembers">重新加载</el-button></template>
      </el-alert>
      <el-skeleton v-if="loading && !members.length" :rows="6" animated />
      <el-empty v-else-if="!loading && !errorMessage && !members.length" description="这个空间还没有可管理的成员" />
      <div v-else-if="members.length" class="members-table-wrap" v-loading="loading">
        <el-table :data="members" row-key="user_id">
          <el-table-column label="成员" min-width="220">
            <template #default="{ row }: { row: MemberSummary }">
              <strong>{{ row.display_name }}</strong>
              <small class="member-email">{{ row.email }}</small>
            </template>
          </el-table-column>
          <el-table-column label="角色" min-width="150">
            <template #default="{ row }: { row: MemberSummary }">
              <el-select
                :model-value="row.role"
                :disabled="!canOperate(row) || mutatingUserId === row.user_id"
                :aria-label="`修改 ${row.display_name} 的角色`"
                @change="changeRole(row, $event)"
              >
                <el-option v-for="role in assignableRoles" :key="role" :label="role" :value="role" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }: { row: MemberSummary }">
              <el-tag :type="row.status === 'ACTIVE' ? 'success' : 'warning'">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" min-width="190" align="right">
            <template #default="{ row }: { row: MemberSummary }">
              <el-button
                link
                :disabled="!canOperate(row)"
                :loading="mutatingUserId === row.user_id"
                @click="toggleStatus(row)"
              >
                {{ row.status === 'SUSPENDED' ? '恢复' : '停用' }}
              </el-button>
              <el-button link type="danger" :disabled="!canOperate(row)" @click="removeMember(row)">移除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <p class="management-count">共 {{ total }} 位成员</p>
        <el-pagination
          v-if="total > 50"
          class="pagination"
          background
          layout="prev, pager, next"
          :current-page="page"
          :page-size="50"
          :total="total"
          @current-change="page = $event; loadMembers()"
        />
      </div>
    </section>

    <el-dialog v-model="addOpen" title="添加成员" width="min(480px, 92vw)">
      <p class="dialog-help">仅支持添加已注册且启用的用户。未知邮箱不会泄露账户信息。</p>
      <el-form label-position="top" @submit.prevent="addMember">
        <el-form-item label="用户邮箱" required>
          <el-input v-model="addForm.email" type="email" autocomplete="off" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="addForm.role">
            <el-option v-for="role in assignableRoles" :key="role" :label="role" :value="role" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addOpen = false">取消</el-button>
        <el-button type="primary" :loading="adding" @click="addMember">确认添加</el-button>
      </template>
    </el-dialog>
  </AppShell>
</template>
