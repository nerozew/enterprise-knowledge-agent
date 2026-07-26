<script setup lang="ts">
import type { KnowledgeSearchInput } from '@/api/types'

defineProps<{
  modelValue: KnowledgeSearchInput
}>()
defineEmits<{
  'update:modelValue': [value: KnowledgeSearchInput]
}>()
</script>

<template>
  <el-collapse class="retrieval-settings">
    <el-collapse-item name="settings">
      <template #title>
        <span>检索设置</span>
        <small>默认 Hybrid + RRF，可展开做面试消融实验</small>
      </template>
      <div class="settings-grid">
        <label>召回通道
          <el-select
            :model-value="modelValue.retrieval_mode"
            @update:model-value="$emit('update:modelValue', { ...modelValue, retrieval_mode: $event })"
          >
            <el-option label="Hybrid（Dense + BM25）" value="HYBRID" />
            <el-option label="Dense 语义" value="DENSE" />
            <el-option label="Sparse / BM25" value="SPARSE" />
          </el-select>
        </label>
        <label>融合算法
          <el-select
            :model-value="modelValue.fusion"
            @update:model-value="$emit('update:modelValue', { ...modelValue, fusion: $event })"
          >
            <el-option label="RRF（k=60）" value="RRF" />
            <el-option label="Weighted（0.65 / 0.35）" value="WEIGHTED" />
          </el-select>
        </label>
        <label>Top K
          <el-input-number
            :model-value="modelValue.top_k"
            :min="1"
            :max="20"
            @update:model-value="$emit('update:modelValue', { ...modelValue, top_k: $event ?? 5 })"
          />
        </label>
        <label>候选数
          <el-input-number
            :model-value="modelValue.candidate_k"
            :min="10"
            :max="200"
            :step="10"
            @update:model-value="$emit('update:modelValue', { ...modelValue, candidate_k: $event ?? 50 })"
          />
        </label>
        <label>Reranker
          <el-select
            :model-value="modelValue.rerank"
            @update:model-value="$emit('update:modelValue', { ...modelValue, rerank: $event })"
          >
            <el-option label="自动" value="AUTO" />
            <el-option label="开启" value="ON" />
            <el-option label="关闭" value="OFF" />
          </el-select>
        </label>
        <label>Query 改写
          <el-select
            :model-value="modelValue.rewrite"
            @update:model-value="$emit('update:modelValue', { ...modelValue, rewrite: $event })"
          >
            <el-option label="自动" value="AUTO" />
            <el-option label="开启" value="ON" />
            <el-option label="关闭" value="OFF" />
          </el-select>
        </label>
        <label>返回模式
          <el-radio-group
            :model-value="modelValue.response_mode"
            @update:model-value="$emit('update:modelValue', { ...modelValue, response_mode: $event })"
          >
            <el-radio-button value="HITS">只检索</el-radio-button>
            <el-radio-button value="ANSWER">实验答案</el-radio-button>
          </el-radio-group>
        </label>
        <label>格式过滤
          <el-select
            :model-value="modelValue.filters.source_formats"
            multiple
            clearable
            placeholder="全部格式"
            @update:model-value="$emit('update:modelValue', { ...modelValue, filters: { ...modelValue.filters, source_formats: $event } })"
          >
            <el-option v-for="format in ['PDF', 'DOCX', 'MARKDOWN', 'TXT']" :key="format" :label="format" :value="format" />
          </el-select>
        </label>
        <div class="setting-switches">
          <el-checkbox
            :model-value="modelValue.multi_query"
            @update:model-value="$emit('update:modelValue', { ...modelValue, multi_query: $event })"
          >
            多查询（最多 2 个改写）
          </el-checkbox>
          <el-checkbox
            :model-value="modelValue.expand_context"
            @update:model-value="$emit('update:modelValue', { ...modelValue, expand_context: $event })"
          >
            父章节与相邻 Chunk 扩展
          </el-checkbox>
        </div>
      </div>
    </el-collapse-item>
  </el-collapse>
</template>
