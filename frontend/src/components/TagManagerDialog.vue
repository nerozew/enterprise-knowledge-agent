<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ApiError, mapApiError } from '@/api/client'
import type { ManagedTag } from '@/api/types'
import { useTagStore } from '@/stores/tags'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; changed: [] }>()
const tags = useTagStore()
const busy = ref(false)
const editingId = ref<string | null>(null)
const form = reactive({ name: '', color: '#5A7D68' })

watch(
  () => props.modelValue,
  (open) => {
    if (open) void tags.load()
  },
)

function edit(tag: ManagedTag) {
  editingId.value = tag.id
  form.name = tag.name
  form.color = tag.color ?? '#5A7D68'
}

function reset() {
  editingId.value = null
  form.name = ''
  form.color = '#5A7D68'
}

async function save() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('请输入标签名称')
    return
  }
  busy.value = true
  try {
    const current = tags.items.find((tag) => tag.id === editingId.value)
    if (current) await tags.update(current.id, { version: current.version, name, color: form.color })
    else await tags.create({ name, color: form.color })
    ElMessage.success(current ? '标签已更新' : '标签已创建')
    reset()
    emit('changed')
  } catch (error) {
    ElMessage.error(mapApiError(error).message)
  } finally {
    busy.value = false
  }
}

async function remove(tag: ManagedTag, force = false) {
  try {
    await ElMessageBox.confirm(
      force ? `“${tag.name}”正在使用中，删除后会从所有灵感中解除，确定继续吗？` : `确定删除标签“${tag.name}”吗？`,
      '删除标签',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
    await tags.remove(tag, force)
    emit('changed')
    ElMessage.success('标签已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    const mapped = mapApiError(error)
    if (mapped instanceof ApiError && mapped.status === 409 && !force) {
      await remove(tag, true)
      return
    }
    ElMessage.error(mapped.message)
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="管理标签"
    width="min(540px, 92vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <form class="tag-form" @submit.prevent="save">
      <el-input v-model="form.name" maxlength="50" placeholder="标签名称" aria-label="标签名称" />
      <el-color-picker v-model="form.color" aria-label="标签颜色" />
      <el-button type="primary" native-type="submit" :loading="busy">{{ editingId ? '保存' : '添加' }}</el-button>
      <el-button v-if="editingId" @click="reset">取消</el-button>
    </form>
    <el-alert v-if="tags.errorMessage" type="error" :title="tags.errorMessage" :closable="false" />
    <div v-loading="tags.loading" class="tag-manager-list">
      <el-empty v-if="!tags.loading && !tags.items.length" description="还没有标签" :image-size="70" />
      <div v-for="tag in tags.items" :key="tag.id" class="tag-manager-row">
        <span class="tag-preview"><i :style="{ background: tag.color ?? '#5A7D68' }" />{{ tag.name }}</span>
        <span class="usage-count">{{ tag.usage_count }} 条灵感</span>
        <el-button link @click="edit(tag)">编辑</el-button>
        <el-button link type="danger" @click="remove(tag)">删除</el-button>
      </div>
    </div>
  </el-dialog>
</template>
