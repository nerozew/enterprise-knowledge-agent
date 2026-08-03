<script setup lang="ts">
import { computed } from 'vue'
import type {
  KnowledgeAnswerability,
  SearchResponseMode,
} from '@/api/types'

const props = defineProps<{
  answerability: KnowledgeAnswerability
  responseMode: SearchResponseMode
}>()

const presentation = computed(() => {
  if (props.responseMode === 'HITS') {
    return {
      type: 'info' as const,
      title: '仅展示候选，未执行语义可回答性判断',
      description: '当前为 HITS 模式，候选片段仅用于核对检索过程，不能据此判断问题可回答。',
    }
  }
  if (props.answerability.status === 'SUPPORTED') {
    return {
      type: 'success' as const,
      title: '已通过直接证据支持检查',
      description: '答案具有当前已发布片段的直接支持，仍可通过固定版本和原文定位复核。',
    }
  }
  if (props.answerability.status === 'REJECTED') {
    return {
      type: 'warning' as const,
      title: '当前已发布语料未提供这个问题的直接答案',
      description: '下方检索结果仅为候选片段，不代表问题答案已被证明。',
    }
  }
  return {
    type: 'info' as const,
    title: '未执行语义可回答性判断',
    description: '当前结果只可作为检索候选核对。',
  }
})
</script>

<template>
  <el-alert
    class="answerability-status"
    :type="presentation.type"
    :title="presentation.title"
    :description="presentation.description"
    show-icon
    :closable="false"
  />
</template>
