import { apiClient } from './client'
import { documentApi } from './documents'

describe('知识文档 API', () => {
  it('组合序列化文档格式与版本状态筛选，不混用文档状态参数', async () => {
    const originalAdapter = apiClient.defaults.adapter
    const requestedUris: string[] = []
    apiClient.defaults.adapter = async (config) => {
      requestedUris.push(apiClient.getUri(config))
      return {
        data: { items: [], total: 0, page: 1, page_size: 12 },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      }
    }

    try {
      await documentApi.list('workspace-1', {
        source_format: 'MARKDOWN',
        version_status: 'PUBLISHED',
      })
      await documentApi.list('workspace-1', {
        source_format: undefined,
        version_status: undefined,
      })
    } finally {
      apiClient.defaults.adapter = originalAdapter
    }

    const serializedQuery = requestedUris[0]?.split('?')[1] ?? ''
    const searchParams = new URLSearchParams(serializedQuery)
    expect(serializedQuery).toBe('source_format=MARKDOWN&version_status=PUBLISHED')
    expect(searchParams.has('status')).toBe(false)
    expect(requestedUris[1]).toBe('/api/v1/workspaces/workspace-1/documents')
  })

  it('multipart 上传携带 Workspace 路径和幂等键', async () => {
    const response = {
      document: {
        id: 'doc-1', workspace_id: 'workspace-1', title: '制度', status: 'ACTIVE', version: 1,
        published_version_id: null, created_at: '2026-07-26T00:00:00Z', updated_at: '2026-07-26T00:00:00Z',
      },
      version: {
        id: 'version-1', document_id: 'doc-1', format: 'TXT', mime_type: 'text/plain',
        file_name: 'policy.txt', file_size: 6, file_sha256: 'a'.repeat(64), status: 'PUBLISHING',
        parser_profile: 'structure-v1', chunk_profile: 'parent-child-v1', index_profile: 'hybrid-v1',
        created_at: '2026-07-26T00:00:00Z', published_at: null,
      },
      job: {
        id: 'job-1', workspace_id: 'workspace-1', document_id: 'doc-1',
        document_version_id: 'version-1', status: 'QUEUED', stage: 'QUEUED', progress: 0,
        attempt_count: 0, retryable: false, version: 1,
        created_at: '2026-07-26T00:00:00Z', updated_at: '2026-07-26T00:00:00Z',
      },
      reused: false,
    }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: response })
    const signal = new AbortController().signal

    await documentApi.upload(
      'workspace-1',
      {
        file: new File(['制度'], 'policy.txt', { type: 'text/plain' }),
        title: '制度',
        duplicate_policy: 'REUSE_EXISTING',
      },
      'intent-key',
      signal,
    )

    expect(post).toHaveBeenCalledTimes(1)
    const [url, form, config] = post.mock.calls[0] ?? []
    expect(url).toBe('/workspaces/workspace-1/documents')
    expect(form).toBeInstanceOf(FormData)
    expect((form as FormData).get('duplicate_policy')).toBe('REUSE_EXISTING')
    expect(config).toMatchObject({ headers: { 'Idempotency-Key': 'intent-key' }, signal })
  })

  it('取消摄取使用 JSON version、幂等键与 If-Match', async () => {
    const job = {
      id: 'job-1',
      workspace_id: 'workspace-1',
      document_id: 'doc-1',
      document_version_id: 'version-1',
      status: 'PARSING' as const,
      stage: 'PARSING',
      progress: 20,
      attempt_count: 1,
      retryable: false,
      version: 3,
      created_at: '2026-07-26T00:00:00Z',
      updated_at: '2026-07-26T00:00:01Z',
    }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { ...job, status: 'CANCELLED', progress: 100, version: 4 },
    })

    await documentApi.cancel('workspace-1', job, 'cancel-key')

    expect(post).toHaveBeenCalledWith(
      '/workspaces/workspace-1/ingestion-jobs/job-1/cancel',
      { version: 3 },
      {
        headers: {
          'Idempotency-Key': 'cancel-key',
          'If-Match': '"3"',
        },
      },
    )
  })
})
