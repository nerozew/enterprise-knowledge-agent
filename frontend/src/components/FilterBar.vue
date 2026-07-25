<script setup lang="ts">
import { ref, watch } from 'vue'
import type { InspirationQuery, InspirationSort, Tag, TagMatch } from '@/api/types'

const props = defineProps<{ query: InspirationQuery; tags: Tag[]; readonly?: boolean }>()
const emit = defineEmits<{ change: [query: InspirationQuery]; manageTags: [] }>()
const keyword = ref(props.query.q ?? '')
let timer: ReturnType<typeof setTimeout> | undefined

watch(
  () => props.query.q,
  (value) => {
    if ((value ?? '') !== keyword.value) keyword.value = value ?? ''
  },
)

function update(patch: InspirationQuery) {
  emit('change', { ...patch, page: 1 })
}

function onKeywordInput(value: string) {
  keyword.value = value
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => update({ q: value.trim() }), 350)
}
</script>

<template>
  <section class="filter-bar" aria-label="灵感筛选">
    <el-input
      :model-value="keyword"
      clearable
      placeholder="搜索标题、正文或 AI 内容"
      aria-label="搜索灵感"
      @update:model-value="onKeywordInput"
    >
      <template #prefix>⌕</template>
    </el-input>
    <el-select
      :model-value="query.tag_ids ?? []"
      multiple
      collapse-tags
      clearable
      placeholder="全部标签"
      aria-label="按标签筛选"
      @update:model-value="update({ tag_ids: $event as string[] })"
    >
      <el-option v-for="tag in tags" :key="tag.id" :label="tag.name" :value="tag.id" />
    </el-select>
    <el-select
      :model-value="query.tag_match ?? 'any'"
      aria-label="标签匹配方式"
      @update:model-value="update({ tag_match: $event as TagMatch })"
    >
      <el-option label="匹配任一标签" value="any" />
      <el-option label="匹配全部标签" value="all" />
    </el-select>
    <el-select
      :model-value="query.sort ?? 'updated_desc'"
      aria-label="排序方式"
      @update:model-value="update({ sort: $event as InspirationSort })"
    >
      <el-option label="最近更新" value="updated_desc" />
      <el-option label="最近创建" value="created_desc" />
    </el-select>
    <el-button v-if="!readonly" class="tag-manage-button" @click="emit('manageTags')">管理标签</el-button>
  </section>
</template>
