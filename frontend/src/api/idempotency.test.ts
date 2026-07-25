import { ApiError } from './client'
import { executeIdempotentIntent } from './idempotency'

describe('幂等操作意图', () => {
  it('不确定网络错误后以相同载荷重试会复用原键，成功后才轮换', async () => {
    const keys: string[] = []
    const request = vi.fn(async (key: string) => {
      keys.push(key)
      if (keys.length === 1) throw new ApiError('网络异常', { code: 'network_error' })
      return { id: 'workspace-1' }
    })

    await expect(
      executeIdempotentIntent('user:user-1:workspace.create', { name: '研发空间' }, request),
    ).rejects.toMatchObject({ code: 'network_error' })
    await executeIdempotentIntent('user:user-1:workspace.create', { name: '研发空间' }, request)
    await executeIdempotentIntent('user:user-1:workspace.create', { name: '研发空间' }, request)

    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[1])
  })

  it('用户修改载荷时立即轮换键', async () => {
    const keys: string[] = []
    const request = vi.fn(async (key: string) => {
      keys.push(key)
      throw new ApiError('请求超时', { code: 'request_timeout' })
    })

    await expect(executeIdempotentIntent('inspiration.create', { content: 'A' }, request)).rejects.toBeDefined()
    await expect(executeIdempotentIntent('inspiration.create', { content: 'B' }, request)).rejects.toBeDefined()

    expect(keys[1]).not.toBe(keys[0])
  })

  it('明确 409 冲突后同一载荷重试会使用新键', async () => {
    const keys: string[] = []
    const request = vi.fn(async (key: string) => {
      keys.push(key)
      throw new ApiError('版本冲突', { code: 'version_conflict', status: 409 })
    })

    await expect(executeIdempotentIntent('inspiration.update:1', { version: 1 }, request)).rejects.toBeDefined()
    await expect(executeIdempotentIntent('inspiration.update:1', { version: 1 }, request)).rejects.toBeDefined()

    expect(keys[1]).not.toBe(keys[0])
  })
})
