<script setup lang="ts">
import { computed, watch } from 'vue'
import AppShell from '@/components/AppShell.vue'
import CitationCard from '@/components/CitationCard.vue'
import RetrievalSettings from '@/components/RetrievalSettings.vue'
import { totalTimingMs } from '@/features/knowledge/timings'
import { useKnowledgeSearchStore } from '@/stores/knowledgeSearch'
import { useWorkspaceStore } from '@/stores/workspaces'

const search = useKnowledgeSearchStore()
const workspaces = useWorkspaceStore()
const workspaceId = computed(() => workspaces.activeWorkspaceId ?? '')
const timingTotal = computed(() => {
  const timings = search.result?.timings_ms
  return timings ? totalTimingMs(timings) : 0
})

watch(
  () => workspaces.activeWorkspaceId,
  (activeWorkspaceId) => {
    if (activeWorkspaceId) search.clearResult()
  },
)
</script>

<template>
  <AppShell>
    <section class="ask-hero" aria-labelledby="ask-title">
      <p class="eyebrow">可信 RAG 实验台</p>
      <h1 id="ask-title">先找到证据，再组织答案</h1>
      <p>检索只访问当前 Workspace 的已发布版本。来源卡固定到版本、页码和 Source Span，便于复核与面试演示。</p>
      <form class="ask-form" @submit.prevent="search.search()">
        <el-input
          v-model="search.form.query"
          type="textarea"
          :rows="3"
          maxlength="1000"
          show-word-limit
          placeholder="例如：公司的采购审批流程中，超过 10 万元需要谁审批？"
          aria-label="知识问题"
          @keydown.ctrl.enter.prevent="search.search()"
        />
        <div class="ask-actions">
          <small>Ctrl + Enter 提问。检索到的文档只作为不可信证据，不会触发业务操作。</small>
          <el-button type="primary" size="large" native-type="submit" :loading="search.loading" :disabled="!search.form.query.trim()">
            {{ search.form.response_mode === 'ANSWER' ? '检索并生成实验答案' : '检索证据' }}
          </el-button>
        </div>
      </form>
      <RetrievalSettings v-model="search.form" />
    </section>

    <section class="answer-area" aria-live="polite">
      <el-alert v-if="search.errorMessage" type="error" :title="search.errorMessage" show-icon :closable="false">
        <template #default>
          <el-button
            v-if="search.form.response_mode === 'ANSWER'"
            link
            type="primary"
            @click="search.form.response_mode = 'HITS'"
          >
            切换为只检索
          </el-button>
        </template>
      </el-alert>
      <el-skeleton v-else-if="search.loading" :rows="6" animated />

      <template v-else-if="search.result">
        <el-alert
          v-if="search.result.provider.mode === 'FIXTURE'"
          type="warning"
          title="当前使用 FIXTURE 测试 Provider：结果仅用于验证流程，不能作为真实 RAG 准确率或简历数据。"
          show-icon
          :closable="false"
        />
        <el-alert
          v-if="search.result.degraded"
          type="warning"
          title="本次检索发生能力降级，请结合下方警告解读结果。"
          show-icon
          :closable="false"
        />
        <div v-if="search.result.warnings.length" class="warning-list">
          <el-tag v-for="warning in search.result.warnings" :key="warning" type="warning" effect="plain">{{ warning }}</el-tag>
        </div>

        <article v-if="search.result.answer" class="answer-card">
          <header>
            <span>实验性答案</span>
            <el-tag type="warning" effect="plain">P4 · 非正式证据门禁</el-tag>
          </header>
          <p>{{ search.result.answer }}</p>
        </article>
        <el-alert
          v-else-if="search.result.refusal"
          type="info"
          :title="search.result.refusal.message"
          description="当前证据不足，因此没有生成答案。可以调整问题、过滤器或补充已发布文档。"
          show-icon
          :closable="false"
        >
          <template #default>
            <el-tag size="small" effect="plain">{{ search.result.refusal.code }}</el-tag>
          </template>
        </el-alert>

        <div class="result-summary">
          <div>
            <p class="eyebrow">证据来源</p>
            <h2>{{ search.result.hits.length ? `${search.result.hits.length} 条可复核片段` : '没有找到足够证据' }}</h2>
          </div>
          <div class="provider-summary">
            <span>{{ search.result.provider.mode }} · {{ search.result.provider.profile }}</span>
            <span>{{ search.result.components.join(' + ') || 'Basic' }}</span>
            <span>{{ timingTotal.toFixed(0) }} ms</span>
          </div>
        </div>
        <div v-if="search.result.hits.length" class="citation-list">
          <CitationCard
            v-for="(hit, index) in search.result.hits"
            :key="hit.chunk_id"
            :hit="hit"
            :rank="index + 1"
            :workspace-id="workspaceId"
          />
        </div>
        <el-empty v-else description="没有命中当前发布版本。系统不会用无来源内容补写答案。" />

        <details class="timing-details">
          <summary>查看实际 Provider、组件与耗时</summary>
          <dl>
            <template v-for="(value, name) in search.result.timings_ms" :key="name">
              <dt>{{ name }}</dt><dd>{{ value.toFixed(1) }} ms</dd>
            </template>
          </dl>
        </details>
      </template>
      <el-empty v-else description="输入一个可以从企业文档中回答的问题" />
    </section>
  </AppShell>
</template>
