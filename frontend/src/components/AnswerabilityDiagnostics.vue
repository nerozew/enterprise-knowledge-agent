<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeAnswerability } from '@/api/types'

const props = defineProps<{
  answerability: KnowledgeAnswerability
}>()

const hasDiagnostics = computed(() => [
  props.answerability.raw_top_score,
  props.answerability.gate_score,
  props.answerability.score_threshold,
  props.answerability.lexical_coverage,
  props.answerability.lexical_threshold,
  props.answerability.lexical_passed,
].some((value) => value !== undefined))

function score(value: number | undefined): string {
  return value === undefined ? '未提供' : value.toFixed(3)
}

function percentage(value: number | undefined): string {
  return value === undefined ? '未提供' : `${(value * 100).toFixed(1)}%`
}
</script>

<template>
  <section
    v-if="hasDiagnostics"
    class="answerability-diagnostics"
    aria-labelledby="answerability-diagnostics-title"
  >
    <h3 id="answerability-diagnostics-title">可回答性预检诊断</h3>
    <dl>
      <dt>原始排序分</dt>
      <dd>{{ score(answerability.raw_top_score) }}</dd>
      <dt>归一化门禁分 / 阈值</dt>
      <dd>{{ score(answerability.gate_score) }} / {{ score(answerability.score_threshold) }}</dd>
      <dt>词法覆盖率 / 阈值</dt>
      <dd>{{ percentage(answerability.lexical_coverage) }} / {{ percentage(answerability.lexical_threshold) }}</dd>
      <dt>词法预检</dt>
      <dd>
        {{ answerability.lexical_passed === undefined ? '未提供' : answerability.lexical_passed ? '通过' : '未通过' }}
      </dd>
    </dl>
    <p>排序分与门禁分仅用于本次检索和预检，不等于答案正确率或置信度。</p>
  </section>
</template>
