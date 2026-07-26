import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { documentApi } from '@/api/documents'
import type { DocumentSummary, Page } from '@/api/types'
import { useDocumentStore } from './documents'

vi.mock('@/api/documents', () => ({
  documentApi: {
    list: vi.fn(),
    versions: vi.fn(),
    upload: vi.fn(),
    job: vi.fn(),
    retry: vi.fn(),
    cancel: vi.fn(),
  },
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

function page(workspaceId: string): Page<DocumentSummary> {
  return {
    items: [{
      id: `doc-${workspaceId}`,
      workspace_id: workspaceId,
      title: workspaceId,
      status: 'ACTIVE',
      version: 1,
      published_version_id: null,
      created_at: '2026-07-26T00:00:00Z',
      updated_at: '2026-07-26T00:00:00Z',
    }],
    total: 1,
    page: 1,
    page_size: 12,
  }
}

describe('Workspace 作用域文档 store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(documentApi.versions).mockResolvedValue([])
  })

  it('切换 Workspace 后清空结果并忽略迟到响应', async () => {
    const first = deferred<Page<DocumentSummary>>()
    const second = deferred<Page<DocumentSummary>>()
    vi.mocked(documentApi.list).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const store = useDocumentStore()

    store.activateWorkspace('a')
    const firstLoad = store.load()
    store.activateWorkspace('b')
    const secondLoad = store.load()
    second.resolve(page('b'))
    await secondLoad
    first.resolve(page('a'))
    await firstLoad

    expect(store.workspaceId).toBe('b')
    expect(store.items.map((item) => item.workspace_id)).toEqual(['b'])
    expect(vi.mocked(documentApi.list).mock.calls[0]?.[2]?.aborted).toBe(true)
  })

  it('404 撤权后立即清除文档、版本与 Job', async () => {
    vi.mocked(documentApi.list).mockRejectedValue(
      new ApiError('资源不存在', { code: 'resource_not_found', status: 404 }),
    )
    const store = useDocumentStore()
    store.activateWorkspace('a')
    store.items = page('a').items
    store.jobs = {
      job: {
        id: 'job', workspace_id: 'a', document_id: 'doc-a', document_version_id: 'v1',
        status: 'PARSING', stage: 'PARSING', progress: 20, attempt_count: 1,
        retryable: false, version: 1, created_at: '', updated_at: '',
      },
    }

    await store.load()

    expect(store.items).toEqual([])
    expect(store.jobs).toEqual({})
    expect(store.errorMessage).toBe('资源不存在')
  })

  it('页面隐藏时把恢复轮询降频到十秒', async () => {
    vi.useFakeTimers()
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    sessionStorage.setItem('knowledge.active-jobs.a', JSON.stringify(['job-1']))
    vi.mocked(documentApi.job).mockResolvedValue({
      id: 'job-1', workspace_id: 'a', document_id: 'doc-a', document_version_id: 'v1',
      status: 'PARSING', stage: 'PARSING', progress: 20, attempt_count: 1,
      retryable: false, version: 1, created_at: '', updated_at: '',
    })
    const store = useDocumentStore()

    store.activateWorkspace('a')
    await vi.advanceTimersByTimeAsync(9_999)
    expect(documentApi.job).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(documentApi.job).toHaveBeenCalledOnce()
    store.activateWorkspace(null)
    vi.useRealTimers()
  })
})
