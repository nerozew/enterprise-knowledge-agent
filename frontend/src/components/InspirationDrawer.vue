<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { Inspiration, InspirationUpdate, Tag } from '@/api/types'

const props = defineProps<{ modelValue: boolean; inspiration: Inspiration | null; tags: Tag[]; busy: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [id: string, payload: InspirationUpdate]
  ai: [item: Inspiration]
}>()

const formRef = ref<FormInstance>()
const form = reactive({ title: '', content: '', sourceUrl: '', tagIds: [] as string[] })
const rules: FormRules = {
  content: [
    { required: true, whitespace: true, message: '正文不能为空', trigger: 'blur' },
    { max: 20000, message: '正文不能超过 20000 个字符', trigger: 'change' },
  ],
  title: [{ max: 200, message: '标题不能超过 200 个字符', trigger: 'change' }],
}

watch(
  () => props.inspiration,
  (item) => {
    if (!item) return
    form.title = item.title ?? ''
    form.content = item.content
    form.sourceUrl = item.source_url ?? ''
    form.tagIds = item.tags.map((tag) => tag.id)
    formRef.value?.clearValidate()
  },
  { immediate: true },
)

async function save() {
  const item = props.inspiration
  if (!item) return
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  emit('save', item.id, {
    version: item.version,
    title: form.title.trim() || null,
    content: form.content.trim(),
    source_url: form.sourceUrl.trim() || null,
    tag_ids: form.tagIds,
  })
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    title="编辑灵感"
    size="min(620px, 100%)"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template v-if="inspiration">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="save">
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" maxlength="200" />
        </el-form-item>
        <el-form-item label="正文" prop="content">
          <el-input v-model="form.content" type="textarea" :rows="12" maxlength="20000" show-word-limit />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tagIds" multiple class="full-width" placeholder="选择标签">
            <el-option v-for="tag in tags" :key="tag.id" :value="tag.id" :label="tag.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="来源链接">
          <el-input v-model="form.sourceUrl" placeholder="https://" />
        </el-form-item>
        <div v-if="inspiration.summary" class="generated-block">
          <span>AI 摘要</span>
          <p>{{ inspiration.summary }}</p>
        </div>
        <div v-if="inspiration.expanded_content" class="generated-block">
          <span>AI 扩展</span>
          <p>{{ inspiration.expanded_content }}</p>
        </div>
      </el-form>
    </template>
    <template #footer>
      <el-button v-if="inspiration" @click="emit('ai', inspiration)">用 AI 加工</el-button>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">保存修改</el-button>
    </template>
  </el-drawer>
</template>
