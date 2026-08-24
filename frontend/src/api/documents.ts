import { apiClient } from './client'
import {
  parseDocument,
  parseDocumentPage,
  parseJob,
  parseSourceSpanLocator,
  parseUploadResult,
  parseVersions,
} from './ragValidation'
import type {
  DocumentQuery,
  DocumentSummary,
  DocumentUploadInput,
  DocumentUploadResult,
  DocumentVersionSummary,
  IngestionJobSummary,
  Page,
  SourceSpanLocator,
} from './types'

function uploadForm(input: DocumentUploadInput): FormData {
  const form = new FormData()
  form.append('file', input.file)
  if (input.title?.trim()) form.append('title', input.title.trim())
  form.append('duplicate_policy', input.duplicate_policy)
  form.append('ocr_mode', input.ocr_mode)
  return form
}

export const documentApi = {
  async list(workspaceId: string, query: DocumentQuery, signal?: AbortSignal): Promise<Page<DocumentSummary>> {
    const config = signal ? { params: query, signal } : { params: query }
    const { data } = await apiClient.get<unknown>(`/workspaces/${workspaceId}/documents`, config)
    return parseDocumentPage(data)
  },

  async get(workspaceId: string, documentId: string, signal?: AbortSignal): Promise<DocumentSummary> {
    const config = signal ? { signal } : {}
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/documents/${documentId}`,
      config,
    )
    return parseDocument(data)
  },

  async versions(
    workspaceId: string,
    documentId: string,
    signal?: AbortSignal,
  ): Promise<DocumentVersionSummary[]> {
    const config = signal ? { signal } : {}
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/documents/${documentId}/versions`,
      config,
    )
    return parseVersions(data)
  },

  async sourceSpan(
    workspaceId: string,
    documentId: string,
    versionId: string,
    sourceSpanId: string,
    signal?: AbortSignal,
  ): Promise<SourceSpanLocator> {
    const config = signal ? { signal } : {}
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/documents/${documentId}/versions/${versionId}/source-spans/${sourceSpanId}`,
      config,
    )
    return parseSourceSpanLocator(data)
  },

  async upload(
    workspaceId: string,
    input: DocumentUploadInput,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<DocumentUploadResult> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/documents`,
      uploadForm(input),
      {
        headers: { 'Idempotency-Key': idempotencyKey },
        ...(signal ? { signal } : {}),
      },
    )
    return parseUploadResult(data)
  },

  async job(workspaceId: string, jobId: string, signal?: AbortSignal): Promise<IngestionJobSummary> {
    const config = signal ? { signal } : {}
    const { data } = await apiClient.get<unknown>(
      `/workspaces/${workspaceId}/ingestion-jobs/${jobId}`,
      config,
    )
    return parseJob(data)
  },

  async retry(
    workspaceId: string,
    job: IngestionJobSummary,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<IngestionJobSummary> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/ingestion-jobs/${job.id}/retry`,
      { version: job.version },
      {
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': `"${job.version}"`,
        },
        ...(signal ? { signal } : {}),
      },
    )
    return parseJob(data)
  },

  async cancel(
    workspaceId: string,
    job: IngestionJobSummary,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<IngestionJobSummary> {
    const { data } = await apiClient.post<unknown>(
      `/workspaces/${workspaceId}/ingestion-jobs/${job.id}/cancel`,
      { version: job.version },
      {
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': `"${job.version}"`,
        },
        ...(signal ? { signal } : {}),
      },
    )
    return parseJob(data)
  },
}
