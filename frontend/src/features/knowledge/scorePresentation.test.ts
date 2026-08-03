import { describe, expect, it } from 'vitest'
import type {
  KnowledgeHit,
  KnowledgeScoreDetails,
  KnowledgeSearchScoring,
  ScoreAvailability,
  ScoreStage,
} from '@/api/types'
import { buildScorePresentation } from './scorePresentation'

function stage(
  availability: ScoreAvailability,
  value: number | null,
  extras: Partial<Pick<ScoreStage, 'rank' | 'method' | 'rrf_k'>> = {},
): ScoreStage {
  return {
    availability,
    value,
    rank: extras.rank ?? null,
    method: extras.method ?? null,
    rrf_k: extras.rrf_k ?? null,
    per_query: [],
    contributions: [],
  }
}

function details(): KnowledgeScoreDetails {
  return {
    dense_similarity: stage('NOT_EXPOSED', null),
    bm25: stage('NOT_EXPOSED', null),
    channel_fusion: stage('AVAILABLE', 0.033, { rank: 1, method: 'RRF', rrf_k: 60 }),
    query_fusion: stage('AVAILABLE', 0.016, { rank: 1, method: 'RRF', rrf_k: 60 }),
    rerank: stage('NOT_RUN', null),
    final: stage('AVAILABLE', 0.016, { rank: 1, method: 'QUERY_FUSION' }),
  }
}

function hit(scoreDetails?: KnowledgeScoreDetails): KnowledgeHit {
  return {
    chunk_id: 'chunk-1',
    document_id: 'doc-1',
    document_version_id: 'version-1',
    title: '采购制度',
    page: 3,
    heading_path: ['审批'],
    quote: '超过十万元需要审批。',
    source_span_id: 'span-1',
    score: 0.016,
    scores: { dense: 0.033, fusion: 0.016 },
    ...(scoreDetails ? { score_details: scoreDetails } : {}),
    context: '',
  }
}

function scoring(queryCount: number): KnowledgeSearchScoring {
  return {
    version: 1,
    requested_retrieval_mode: 'HYBRID',
    effective_retrieval_mode: 'HYBRID',
    query_count: queryCount,
    channel_fusion: { method: 'RRF', applied: true, rrf_k: 60 },
    query_fusion: { method: 'RRF', applied: true, rrf_k: 60 },
    rerank_applied: false,
  }
}

describe('检索分数展示模型', () => {
  it('不会把 HYBRID legacy dense 0.033 误称为 Dense', () => {
    const result = buildScorePresentation(hit(details()), scoring(1), 1)

    expect(result.mainLabel).toBe('最终排序分')
    expect(result.mainTooltip).toContain('不是正确率或置信度')
    expect(result.stages.find((item) => item.key === 'dense_similarity')).toMatchObject({
      value: null,
      availabilityText: '未提供',
    })
    expect(result.stages.find((item) => item.key === 'channel_fusion')?.label)
      .toBe('通道融合 RRF · k=60')
    expect(result.stages.find((item) => item.key === 'channel_fusion')?.tooltip)
      .toContain('数值较小是 RRF 公式的正常结果')
    expect(result.stages.some((item) => item.label === 'Dense')).toBe(false)
  })

  it('区分单查询与多查询 RRF，并保留 unavailable 状态', () => {
    const single = buildScorePresentation(hit(details()), scoring(1), 1)
    const multiple = buildScorePresentation(hit(details()), scoring(3), 1)

    expect(single.stages.find((item) => item.key === 'query_fusion')?.label)
      .toBe('查询排序 RRF · k=60')
    expect(multiple.stages.find((item) => item.key === 'query_fusion')?.label)
      .toBe('多查询融合 RRF · k=60')
    expect(single.stages.find((item) => item.key === 'rerank')).toMatchObject({
      value: null,
      availabilityText: '未执行',
    })
  })

  it('只为 AVAILABLE 通道和 Rerank 提供数值', () => {
    const available = details()
    available.dense_similarity = stage('AVAILABLE', 0.82, { rank: 2 })
    available.bm25 = stage('NOT_APPLICABLE', null)
    available.rerank = stage('AVAILABLE', 0.74, { rank: 1 })
    const result = buildScorePresentation(hit(available), scoring(1), 1)

    expect(result.stages.find((item) => item.key === 'dense_similarity')).toMatchObject({
      label: '语义相似度',
      value: '0.820',
      rank: 2,
    })
    expect(result.stages.find((item) => item.key === 'bm25')).toMatchObject({
      value: null,
      availabilityText: '不适用',
    })
    expect(result.stages.find((item) => item.key === 'rerank')).toMatchObject({
      label: '重排分',
      value: '0.740',
    })
  })

  it('安全展示后端新增的真实 fallback method，不伪装为 Query RRF', () => {
    const fallback = details()
    fallback.channel_fusion.method = 'CHANNEL_FALLBACK'
    fallback.channel_fusion.rrf_k = null
    fallback.query_fusion.method = 'RANK_PRESERVING_FALLBACK'
    fallback.query_fusion.rrf_k = null
    const fallbackScoring = scoring(1)
    fallbackScoring.channel_fusion = {
      method: 'CHANNEL_FALLBACK',
      applied: true,
      rrf_k: null,
    }
    fallbackScoring.query_fusion = {
      method: 'RANK_PRESERVING_FALLBACK',
      applied: true,
      rrf_k: null,
    }

    const result = buildScorePresentation(hit(fallback), fallbackScoring, 1)
    const channel = result.stages.find((item) => item.key === 'channel_fusion')
    const query = result.stages.find((item) => item.key === 'query_fusion')

    expect(channel?.label).toBe('通道融合 · CHANNEL_FALLBACK')
    expect(channel?.tooltip).toContain('采用 CHANNEL_FALLBACK 方法')
    expect(query?.label).toBe('查询排序 · RANK_PRESERVING_FALLBACK')
    expect(query?.tooltip).toContain('采用 RANK_PRESERVING_FALLBACK 方法')
    expect(query?.label).not.toContain('RRF')
    expect(query?.tooltip).not.toContain('RRF 排名变换')
  })

  it('准确解释 IDENTITY 与 COMBINED_FUSION', () => {
    const identity = details()
    identity.query_fusion.method = 'IDENTITY'
    identity.query_fusion.rrf_k = null
    const identityScoring = scoring(1)
    identityScoring.query_fusion = { method: 'IDENTITY', applied: false, rrf_k: null }
    const identityResult = buildScorePresentation(hit(identity), identityScoring, 1)
    const identityQuery = identityResult.stages.find((item) => item.key === 'query_fusion')

    expect(identityQuery?.label).toBe('未额外查询融合')
    expect(identityQuery?.tooltip).toContain('未执行额外的查询融合')
    expect(identityQuery?.label).not.toContain('RRF')

    const combined = details()
    combined.channel_fusion.method = 'COMBINED_FUSION'
    combined.channel_fusion.rrf_k = null
    combined.query_fusion.method = 'COMBINED_FUSION'
    combined.query_fusion.rrf_k = null
    const combinedScoring = scoring(3)
    combinedScoring.channel_fusion = {
      method: 'COMBINED_FUSION',
      applied: true,
      rrf_k: null,
    }
    combinedScoring.query_fusion = {
      method: 'COMBINED_FUSION',
      applied: true,
      rrf_k: null,
    }
    const combinedResult = buildScorePresentation(hit(combined), combinedScoring, 1)
    const combinedChannel = combinedResult.stages.find((item) => item.key === 'channel_fusion')
    const combinedQuery = combinedResult.stages.find((item) => item.key === 'query_fusion')

    expect(combinedChannel).toBeUndefined()
    expect(combinedQuery?.label).toBe('通道与查询联合融合')
    expect(combinedQuery?.tooltip).toContain('不能拆解或误标为独立的 Query RRF')
    expect(combinedQuery?.label).not.toContain('RRF')
    expect(combinedResult.stages.filter((item) => item.label === '通道与查询联合融合'))
      .toHaveLength(1)
  })

  it('旧响应只显示无法猜测语义的综合排序分', () => {
    const result = buildScorePresentation(hit(), undefined, 2)

    expect(result).toMatchObject({
      mainLabel: '旧版综合排序分',
      mainValue: '0.016',
      mainRank: 2,
      mainTooltip: '旧版接口未提供可验证的分阶段语义。',
      stages: [],
    })
  })
})
