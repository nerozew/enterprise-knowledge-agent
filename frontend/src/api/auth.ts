import { apiClient } from './client'
import type { AuthResponse, LoginInput, RegisterInput, User } from './types'

export const authApi = {
  async login(input: LoginInput): Promise<AuthResponse> {
    const { data } = await apiClient.post<AuthResponse>('/auth/login', input)
    return data
  },
  async register(input: RegisterInput): Promise<AuthResponse> {
    const { data } = await apiClient.post<AuthResponse>('/auth/register', input)
    return data
  },
  async me(): Promise<User> {
    const { data } = await apiClient.get<User>('/auth/me')
    return data
  },
}
