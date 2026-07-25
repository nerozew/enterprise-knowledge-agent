<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { inspirationApi } from '@/api/inspirations'
import { mapApiError } from '@/api/client'
import type { AiGeneration, AiOperation, Inspiration, InspirationUpdate } from '@/api/types'

const props = defineProps<{ modelValue: boolean; inspiration: Inspiration | null; saving: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  save: [id: string, payload: InspirationUpdate]
}>()

const operation = ref<AiOperation>('summary')
const instruction = ref('')
const generation = ref<AiGeneration | null>(null)
const generating = ref(false)
const errorMessage = ref('')
const canSave = computed(() => Boolean(generation.value?.result && props.inspiration))

watch(
  () => [props.modelValue, props.inspiration?.id],
  () => {
    generation.value = null
    instruction.value = ''
    errorMessage.value = ''
  },
)

function idempotencyKey(): string {
  return typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function generate() {
  if (!props.inspiration) return
  generating.value = true
  errorMessage.value = ''
  generation.value = null
  try {
    const trimmedInstruction = instruction.value.trim()
    generation.value = await inspirationApi.generate(
      props.inspiration.id,
      trimmedInstruction ? { operation: operation.value, instruction: trimmedInstruction } : { operation: operation.value },
      idempotencyKey(),
    )
  } catch (error) {
    errorMessage.value = mapApiError(error).message
  } finally {
    generating.value = false
  }
}

function save() {
  if (!props.inspiration || !generation.value) return
  const resultField = generation.value.operation === 'summary'
    ? { summary: generation.value.result }
    : { expanded_content: generation.value.result }
  emit('save', props.inspiration.id, { version: props.inspiration.version, ...resultField })
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    title="AI 灵感加工"
    size="min(560px, 100%)"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div v-if="inspiration" class="ai-panel">
      <p class="ai-source"><strong>{{ inspiration.title || '未命名灵感' }}</strong>{{ inspiration.content }}</p>
      <el-radio-group v-model="operation" aria-label="AI 操作">
        <el-radio-button value="summary">提炼摘要</el-radio-button>
        <el-radio-button value="expand">扩展思路</el-radio-button>
      </el-radio-group>
      <el-input
        v-model="instruction"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        placeholder="补充你的要求（可选），例如：用三条行动建议展开"
      />
      <el-button type="primary" :loading="generating" @click="generate">
        {{ generation ? '重新生成' : '开始生成' }}
      </el-button>

      <el-alert v-if="errorMessage" type="error" :title="errorMessage" show-icon :closable="false">
        <template #default><el-button link type="primary" @click="generate">重试</el-button></template>
      </el-alert>
      <el-skeleton v-if="generating" :rows="6" animated />
      <section v-if="generation && !generating" class="ai-result" aria-live="polite">
        <el-alert v-if="generation.warning" type="warning" :title="generation.warning" show-icon :closable="false" />
        <div class="result-heading">
          <strong>生成预览</strong>
          <el-tag v-if="generation.mode === 'fallback'" type="warning">本地降级</el-tag>
        </div>
        <p>{{ generation.result }}</p>
        <small>AI 内容可能不准确，保存前请检查。结果不会自动覆盖原文。</small>
      </section>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button type="primary" :disabled="!canSave" :loading="saving" @click="save">保存到灵感</el-button>
    </template>
  </el-drawer>
</template>
