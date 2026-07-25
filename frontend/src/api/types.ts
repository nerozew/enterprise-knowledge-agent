export interface ApiErrorPayload {
  error: {
    code: string
    message: string
    details: unknown[]
  }
  request_id: string
}

export interface User {
  id: string
  email: string
  display_name: string
  is_active: boolean
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type?: string
  user: User
}

export interface LoginInput {
  email: string
  password: string
}

export interface RegisterInput extends LoginInput {
  display_name: string
}

export interface Tag {
  id: string
  name: string
  color: string | null
  version: number
  created_at?: string
  updated_at?: string
}

export interface ManagedTag extends Tag {
  usage_count: number
}

export interface Inspiration {
  id: string
  title: string | null
  content: string
  summary: string | null
  expanded_content: string | null
  source_url: string | null
  tags: Tag[]
  version: number
  created_at: string
  updated_at: string
}

export interface InspirationList {
  items: Inspiration[]
  total: number
  page: number
  page_size: number
}

export type TagMatch = 'any' | 'all'
export type InspirationSort = 'updated_desc' | 'created_desc'

export interface InspirationQuery {
  q?: string
  tag_ids?: string[]
  tag_match?: TagMatch
  date_from?: string
  date_to?: string
  sort?: InspirationSort
  page?: number
  page_size?: number
}

export interface InspirationCreate {
  title?: string | null
  content: string
  source_url?: string | null
  tag_ids?: string[]
}

export interface InspirationUpdate {
  version: number
  title?: string | null
  content?: string
  source_url?: string | null
  summary?: string | null
  expanded_content?: string | null
  tag_ids?: string[]
}

export interface TagCreate {
  name: string
  color?: string | null
}

export interface TagUpdate {
  version: number
  name?: string
  color?: string | null
}

export type AiOperation = 'summary' | 'expand'

export interface AiGeneration {
  generation_id: string
  operation: AiOperation
  mode: 'provider' | 'fallback'
  result: string
  warning?: string
  created_at: string
}

export interface AiGenerationInput {
  operation: AiOperation
  instruction?: string
}
