import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { documentApi } from '@/api/documents'
import type { DocumentSummary, DocumentVersionSummary, Page } from '@/api/types'
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

  it('按筛选状态和格式展示目标最新版本，发布筛选固定到发布版本', async () => {
    const result = page('a')
    result.items[0]!.published_version_id = 'version-published'
    const versions: DocumentVersionSummary[] = [
      {
        id: 'version-published',
        document_id: 'doc-a',
        format: 'MARKDOWN',
        mime_type: 'text/markdown',
        file_name: 'published.md',
        file_size: 10,
        file_sha256: 'a'.repeat(64),
        status: 'PUBLISHED',
        parser_profile: 'structure-v1',
        chunk_profile: 'parent-child-v1',
        index_profile: 'hybrid-v1',
        created_at: '2026-07-25T00:00:00Z',
        published_at: '2026-07-25T00:01:00Z',
      },
      {
        id: 'version-failed-pdf',
        document_id: 'doc-a',
        format: 'PDF',
        mime_type: 'application/pdf',
        file_name: 'newer.pdf',
        file_size: 20,
        file_sha256: 'b'.repeat(64),
        status: 'FAILED',
        parser_profile: 'structure-v1',
        chunk_profile: 'parent-child-v1',
        index_profile: 'hybrid-v1',
        created_at: '2026-07-28T00:00:00Z',
        published_at: null,
      },
      {
        id: 'version-failed-a',
        document_id: 'doc-a',
        format: 'MARKDOWN',
        mime_type: 'text/markdown',
        file_name: 'failed-a.md',
        file_size: 30,
        file_sha256: 'c'.repeat(64),
        status: 'FAILED',
        parser_profile: 'structure-v1',
        chunk_profile: 'parent-child-v1',
        index_profile: 'hybrid-v1',
        created_at: '2026-07-27T00:00:00Z',
        published_at: null,
      },
      {
        id: 'version-failed-z',
        document_id: 'doc-a',
        format: 'MARKDOWN',
        mime_type: 'text/markdown',
        file_name: 'failed-z.md',
        file_size: 40,
        file_sha256: 'd'.repeat(64),
        status: 'FAILED',
        parser_profile: 'structure-v1',
        chunk_profile: 'parent-child-v1',
        index_profile: 'hybrid-v1',
        created_at: '2026-07-27T00:00:00Z',
        published_at: null,
      },
    ]
    vi.mocked(documentApi.list).mockResolvedValue(result)
    vi.mocked(documentApi.versions).mockResolvedValue(versions)
    const store = useDocumentStore()
    store.activateWorkspace('a')

    await store.load({ source_format: 'MARKDOWN', version_status: 'FAILED' })

    expect(store.currentVersions['doc-a']).toMatchObject({
      id: 'version-failed-z',
      format: 'MARKDOWN',
      status: 'FAILED',
    })

    await store.load({ version_status: 'PUBLISHED' })

    expect(store.currentVersions['doc-a']).toMatchObject({
      id: 'version-published',
      status: 'PUBLISHED',
    })
  })
})
