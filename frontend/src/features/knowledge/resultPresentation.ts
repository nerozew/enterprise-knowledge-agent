import type { KnowledgeSearchResult } from '@/api/types'

export interface KnowledgeResultPresentation {
  isRefused: boolean
  eyebrow: string
  title: string
  candidateNotice: string
  refusalDescription: string
  emptyDescription: string
}

export function buildKnowledgeResultPresentation(
  result: KnowledgeSearchResult,
): KnowledgeResultPresentation {
  const count = result.hits.length
  if (result.refusal) {
    const modelRefused = result.refusal.code.toLowerCase() === 'model_refused'
    if (modelRefused) {
      return {
        isRefused: true,
        eyebrow: '模型拒答',
        title: `模型拒绝形成答案，供核对的候选片段（${count}）`,
        candidateNotice:
          '证据门禁已通过，但答案模型在证据上下文下拒绝形成答案。以下候选仅供核对检索与门禁结果，不构成最终引用。',
        refusalDescription: '证据已通过门禁，答案模型在证据上下文下拒绝形成答案。',
        emptyDescription: '模型拒绝形成答案，当前没有可展示的候选片段。',
      }
    }
    return {
      isRefused: true,
      eyebrow: '检索候选',
      title: `未通过证据门禁的候选片段（${count}）`,
      candidateNotice: '以下候选仅供核对检索过程，不代表问题已有可靠答案，也未用于生成答案。',
      refusalDescription:
        '证据未通过门禁，系统没有生成答案。可以调整问题、过滤器或补充已发布文档。',
      emptyDescription: '没有找到可展示的已发布候选片段。',
    }
  }

  return {
    isRefused: false,
    eyebrow: '证据来源',
    title: count ? `${count} 条可复核片段` : '没有找到足够证据',
    candidateNotice: '',
    refusalDescription: '',
    emptyDescription: '没有命中当前发布版本。系统不会用无来源内容补写答案。',
  }
}
