import { describe, expect, it } from 'vitest'
import type { ManagedTag } from './types'

describe('managed tag contract', () => {
  it('requires usage_count instead of treating an absent field as zero', () => {
    const complete: ManagedTag = {
      id: 'tag-1',
      workspace_id: 'workspace-1',
      name: '动漫',
      color: null,
      version: 1,
      usage_count: 2,
    }

    // @ts-expect-error Management API fixtures must include usage_count.
    const missing: ManagedTag = {
      id: 'tag-2',
      workspace_id: 'workspace-1',
      name: '缺少计数',
      color: null,
      version: 1,
    }

    expect(complete.usage_count).toBe(2)
    expect('usage_count' in missing).toBe(false)
  })
})
