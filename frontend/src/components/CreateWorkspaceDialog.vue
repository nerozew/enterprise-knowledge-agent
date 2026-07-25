<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { mapApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceStore } from '@/stores/workspaces'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: [workspaceId: string]
}>()

const auth = useAuthStore()
const workspaces = useWorkspaceStore()
const formRef = ref<FormInstance>()
const form = reactive({ name: '' })
const errorMessage = ref('')
const rules: FormRules = {
  name: [
    { required: true, whitespace: true, message: '请输入团队空间名称', trigger: 'blur' },
    { max: 100, message: '名称不能超过 100 个字符', trigger: 'change' },
  ],
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    form.name = ''
    errorMessage.value = ''
    formRef.value?.clearValidate()
  },
)

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || !auth.user) return
  errorMessage.value = ''
  try {
    const created = await workspaces.create({ name: form.name.trim() }, auth.user.id)
    emit('update:modelValue', false)
    emit('created', created.id)
  } catch (error) {
    errorMessage.value = mapApiError(error).message
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="新建团队空间"
    width="min(460px, 92vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="dialog-help">团队空间适合与同事共同整理灵感。创建后，你将成为 OWNER。</p>
    <el-alert v-if="errorMessage" type="error" :title="errorMessage" show-icon :closable="false" />
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <el-form-item label="空间名称" prop="name">
        <el-input v-model="form.name" maxlength="100" show-word-limit autocomplete="off" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="workspaces.creating" @click="submit">创建并进入</el-button>
    </template>
  </el-dialog>
</template>
