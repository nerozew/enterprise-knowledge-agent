import { afterEach, vi } from 'vitest'
import { clearPendingIdempotencyIntents } from '@/api/idempotency'

afterEach(() => {
  localStorage.clear()
  clearPendingIdempotencyIntents()
  vi.restoreAllMocks()
})
