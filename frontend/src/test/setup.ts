import { afterEach, vi } from 'vitest'
import { clearPendingIdempotencyIntents } from '@/api/idempotency'

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  clearPendingIdempotencyIntents()
  vi.restoreAllMocks()
})
