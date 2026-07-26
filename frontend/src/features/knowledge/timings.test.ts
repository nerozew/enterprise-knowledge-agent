import { describe, expect, it } from 'vitest'
import { totalTimingMs } from './timings'

describe('RAG 耗时汇总', () => {
  it('后端提供 total 时不重复累加阶段耗时', () => {
    expect(totalTimingMs({ dense: 12, sparse: 8, fusion: 2, total: 25 })).toBe(25)
  })

  it('旧响应没有 total 时才汇总阶段耗时', () => {
    expect(totalTimingMs({ dense: 12, sparse: 8, fusion: 2 })).toBe(22)
  })
})
