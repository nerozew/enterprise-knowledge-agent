<script setup lang="ts">
import { reactive, ref } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { InspirationCreate, Tag } from '@/api/types'

defineProps<{ tags: Tag[]; busy: boolean }>()
const emit = defineEmits<{ submit: [payload: InspirationCreate] }>()

const formRef = ref<FormInstance>()
const form = reactive({ title: '', content: '', sourceUrl: '', tagIds: [] as string[] })
const rules: FormRules = {
  content: [
    { required: true, whitespace: true, message: '先写下一点灵感吧', trigger: 'blur' },
    { max: 20000, message: '正文不能超过 20000 个字符', trigger: 'change' },
  ],
  title: [{ max: 200, message: '标题不能超过 200 个字符', trigger: 'change' }],
  sourceUrl: [
    {
      validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
        if (!value || /^https?:\/\//i.test(value)) callback()
        else callback(new Error('来源链接必须以 http:// 或 https:// 开头'))
      },
      trigger: 'blur',
    },
  ],
}

async function submit() {
  const content = form.content.trim()
  if (!content) {
    await formRef.value?.validateField('content').catch(() => undefined)
    return
  }
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  emit('submit', {
    title: form.title.trim() || null,
    content,
    source_url: form.sourceUrl.trim() || null,
    tag_ids: form.tagIds,
  })
}

function reset() {
  form.title = ''
  form.content = ''
  form.sourceUrl = ''
  form.tagIds = []
  formRef.value?.clearValidate()
}

defineExpose({ reset })
</script>

<template>
  <section class="capture-card" aria-labelledby="quick-capture-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">快速记录</p>
        <h1 id="quick-capture-title">现在，脑海里闪过了什么？</h1>
      </div>
      <span class="shortcut-hint">Ctrl / ⌘ + Enter 保存</span>
    </div>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <el-form-item prop="content">
        <el-input
          v-model="form.content"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 8 }"
          maxlength="20000"
          show-word-limit
          resize="none"
          placeholder="一句话、一个画面，或一个还没想完整的问题……"
          aria-label="灵感正文"
          @keydown.ctrl.enter="submit"
          @keydown.meta.enter="submit"
        />
      </el-form-item>
      <div class="capture-options">
        <el-form-item prop="title" label="标题（可选）">
          <el-input v-model="form.title" maxlength="200" placeholder="给它一个名字" clearable />
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="form.tagIds" multiple collapse-tags placeholder="选择标签" clearable>
            <el-option v-for="tag in tags" :key="tag.id" :label="tag.name" :value="tag.id">
              <span class="tag-option-dot" :style="{ background: tag.color ?? '#5A7D68' }" />{{ tag.name }}
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item prop="sourceUrl" label="来源链接（可选）">
          <el-input v-model="form.sourceUrl" placeholder="https://" clearable />
        </el-form-item>
      </div>
      <div class="capture-actions">
        <span>内容仅保存在你的账户中</span>
        <el-button type="primary" native-type="submit" :loading="busy">保存灵感</el-button>
      </div>
    </el-form>
  </section>
</template>
