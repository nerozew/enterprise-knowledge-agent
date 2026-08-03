import type {
  KnowledgeHit,
  KnowledgeSearchScoring,
  ScoreAvailability,
  ScoreStage,
} from '@/api/types'

export interface ScoreStagePresentation {
  key: 'dense_similarity' | 'bm25' | 'channel_fusion' | 'query_fusion' | 'rerank'
  label: string
  value: string | null
  rank: number | null
  availability: ScoreAvailability
  availabilityText: string
  tooltip: string
}

export interface ScorePresentation {
  mainLabel: '最终排序分' | '旧版综合排序分'
  mainValue: string
  mainRank: number
  mainTooltip: string
  stages: ScoreStagePresentation[]
}

const finalScoreTooltip =
  '用于本次结果排序，不是正确率或置信度；不同算法的分数不可直接横向比较。'
const legacyScoreTooltip = '旧版接口未提供可验证的分阶段语义。'

const availabilityText: Record<ScoreAvailability, string> = {
  AVAILABLE: '已提供',
  NOT_APPLICABLE: '不适用',
  NOT_EXPOSED: '未提供',
  NOT_RUN: '未执行',
}

function formatted(value: number | null): string | null {
  return value === null ? null : value.toFixed(3)
}

function rrfLabel(prefix: string, method: string | null, rrfK: number | null): string {
  if (method?.toUpperCase() !== 'RRF') {
    return method ? `${prefix} · ${method}` : prefix
  }
  return rrfK === null ? `${prefix} RRF` : `${prefix} RRF · k=${rrfK}`
}

function methodKey(method: string | null): string | null {
  return method?.trim().toUpperCase() || null
}

function channelFusionLabel(method: string | null, rrfK: number | null): string {
  if (methodKey(method) === 'COMBINED_FUSION') return '通道与查询联合融合'
  if (methodKey(method) === 'IDENTITY') return '未额外通道融合'
  return rrfLabel('通道融合', method, rrfK)
}

function queryFusionLabel(
  method: string | null,
  rrfK: number | null,
  queryPrefix: string,
): string {
  if (methodKey(method) === 'IDENTITY') return '未额外查询融合'
  if (methodKey(method) === 'COMBINED_FUSION') return '通道与查询联合融合'
  return rrfLabel(queryPrefix, method, rrfK)
}

function channelFusionTooltip(method: string | null): string {
  if (methodKey(method) === 'RRF') {
    return '在同一查询内按 Dense 与 BM25 名次融合。数值较小是 RRF 公式的正常结果。'
  }
  if (methodKey(method) === 'COMBINED_FUSION') {
    return '后端在同一阶段联合处理检索通道与查询结果；该值不能拆解或误标为独立的 Query RRF。'
  }
  if (methodKey(method) === 'IDENTITY') {
    return '后端未执行额外的通道融合；该值沿用上游排序结果，不是正确率或置信度。'
  }
  return method
    ? `后端报告本阶段采用 ${method} 方法合并检索通道；展示值不是正确率或置信度。`
    : '后端未提供本阶段的通道融合方法；展示值仅用于本次结果排序。'
}

function queryFusionTooltip(method: string | null, queryCount: number | undefined): string {
  if (methodKey(method) === 'RRF') {
    return queryCount !== undefined && queryCount > 1
      ? '汇总原问题及改写查询的候选名次，不是多查询共识度或置信度。'
      : '当前流水线在单查询时仍执行此 RRF 排名变换；不是多查询共识分。'
  }
  if (methodKey(method) === 'IDENTITY') {
    return '本次未执行额外的查询融合；该值沿用上游排序结果，不是正确率或置信度。'
  }
  if (methodKey(method) === 'COMBINED_FUSION') {
    return '后端在同一阶段联合处理检索通道与查询结果；该值不能拆解或误标为独立的 Query RRF。'
  }
  return method
    ? `后端报告本阶段采用 ${method} 方法进行查询结果排序；展示值不是正确率或置信度。`
    : '后端未提供本阶段的查询排序方法；展示值仅用于本次结果排序。'
}

function stage(
  key: ScoreStagePresentation['key'],
  label: string,
  value: ScoreStage,
  tooltip: string,
): ScoreStagePresentation {
  return {
    key,
    label,
    value: value.availability === 'AVAILABLE' ? formatted(value.value) : null,
    rank: value.availability === 'AVAILABLE' ? value.rank : null,
    availability: value.availability,
    availabilityText: availabilityText[value.availability],
    tooltip,
  }
}

export function buildScorePresentation(
  hit: KnowledgeHit,
  scoring: KnowledgeSearchScoring | undefined,
  responseRank: number,
): ScorePresentation {
  const details = hit.score_details
  if (!details || details.final.availability !== 'AVAILABLE' || details.final.value === null) {
    return {
      mainLabel: '旧版综合排序分',
      mainValue: hit.score.toFixed(3),
      mainRank: responseRank,
      mainTooltip: legacyScoreTooltip,
      stages: [],
    }
  }

  const channelMethod = details.channel_fusion.method ?? scoring?.channel_fusion.method ?? null
  const channelRrfK = details.channel_fusion.rrf_k ?? scoring?.channel_fusion.rrf_k ?? null
  const queryMethod = details.query_fusion.method ?? scoring?.query_fusion.method ?? null
  const queryRrfK = details.query_fusion.rrf_k ?? scoring?.query_fusion.rrf_k ?? null
  const queryPrefix = scoring?.query_count !== undefined && scoring.query_count > 1
    ? '多查询融合'
    : '查询排序'
  const combinedFusion = methodKey(channelMethod) === 'COMBINED_FUSION'
    && methodKey(queryMethod) === 'COMBINED_FUSION'
  const fusionStages: ScoreStagePresentation[] = combinedFusion
    ? [
        stage(
          'query_fusion',
          '通道与查询联合融合',
          details.query_fusion,
          queryFusionTooltip(queryMethod, scoring?.query_count),
        ),
      ]
    : [
        stage(
          'channel_fusion',
          channelFusionLabel(channelMethod, channelRrfK),
          details.channel_fusion,
          channelFusionTooltip(channelMethod),
        ),
        stage(
          'query_fusion',
          queryFusionLabel(queryMethod, queryRrfK, queryPrefix),
          details.query_fusion,
          queryFusionTooltip(queryMethod, scoring?.query_count),
        ),
      ]

  return {
    mainLabel: '最终排序分',
    mainValue: details.final.value.toFixed(3),
    mainRank: details.final.rank ?? responseRank,
    mainTooltip: finalScoreTooltip,
    stages: [
      stage(
        'dense_similarity',
        '语义相似度',
        details.dense_similarity,
        'Dense 余弦相似度；仅在检索后端返回原始 Dense 分时显示，不是置信度。',
      ),
      stage(
        'bm25',
        '关键词相关度',
        details.bm25,
        'BM25 原始相关度；尺度受查询和语料影响，不适合跨查询比较。',
      ),
      ...fusionStages,
      stage(
        'rerank',
        '重排分',
        details.rerank,
        '重排模型的相关度输出，数值尺度由 Provider 决定。',
      ),
    ],
  }
}
