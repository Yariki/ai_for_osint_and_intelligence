export interface Source {
  id: number
  name: string
  url: string
  period_minutes: number
  enabled: boolean
  next_run_at: string
  last_checked_at: string | null
  last_success_at: string | null
  last_entry_id: string | null
}
export interface SourceDraft {
  name: string
  url: string
  period_minutes: number
  enabled: boolean
}
export interface Tag {
  id: number
  name: string
}
export interface Article {
  id: number
  title: string
  link: string
  published_at: string | null
  discovered_at: string
  description: string
  tags: Tag[]
  source_ids: number[]
}
export type JobStatus =
  'queued' | 'running' | 'retrying' | 'successful' | 'partial' | 'failed' | 'blocked'
export interface Job {
  id: number
  source_id: number
  status: JobStatus
  trigger: string
  attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  next_attempt_at: string
  http_status: number | null
  found_count: number
  new_count: number
  duplicate_count: number
  invalid_count: number
  errors: { code: string; message: string }[]
}
export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
export interface Stats {
  sources: number
  enabled_sources: number
  jobs: number
  active_jobs: number
  articles: number
  error_jobs: number
  invalid_entries: number
}
