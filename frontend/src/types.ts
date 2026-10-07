export type AnalysisState = {
  profile: string | null
  status: string
  progress: number
  stage: string
  error?: string | null
}

export type MediaOrder = 'capture_date' | 'manual' | 'random'
export type MediaFilter = 'all' | 'video' | 'photo'
export type DurationMode = 0 | 15 | 30 | 60

export type MediaAsset = {
  id: string
  name: string
  media_type: 'photo' | 'video'
  mime_type: string
  size: number
  selected: boolean
  sort_order: number
  metadata: Record<string, unknown>
  source_url: string
  thumbnail_url: string | null
  proxy_url: string | null
  analysis: AnalysisState
}

export type Project = {
  id: string
  name: string
  status: string
  duration: DurationMode
  include_all: boolean
  media_filter: MediaFilter
  media_order: MediaOrder
  random_seed: number
  min_clip_duration: number
  processing_profile: 'fast' | 'smart' | 'maximum'
  music_track_id: string | null
  music_start: number | null
  asset_count?: number
  created_at: string
  updated_at: string
}

export type Job = {
  id: string
  kind: string
  status: string
  progress: number
  stage: string
  error?: string | null
  result: Record<string, unknown>
  created_at: string
  updated_at: string
  items: Array<{
    asset_id: string
    status: string
    progress: number
    stage: string
    error?: string | null
  }>
}

export type ProjectDetail = {
  project: Project
  assets: MediaAsset[]
  job: Job | null
}

export type MusicTrack = {
  id: string
  title: string
  source_kind: 'audio' | 'video'
  is_preset: boolean
  status: string
  progress: number
  stage: string
  error?: string | null
  duration: number
  bpm: number
  metadata: {
    artist?: string
    description?: string
    tags?: string[]
    genres?: string[]
    genre_sources?: Record<string, string[]>
    shazam_id?: string
    shazam_url?: string
    release_date?: string
    explicit?: boolean
    version?: string
    filename?: string
  }
  sort_order: number
  highlights: Record<string, { start: number; end: number; duration: number; score: number }>
  energy: Array<{ time: number; value: number }>
  downbeats: number[]
  sections: Array<{ start: number; end: number; label: string; energy: number }>
}

export type TimelineItem = {
  asset_id: string
  timeline_start: number
  timeline_end: number
  candidate_id: string
  path: string
  kind: 'photo' | 'video'
  source_start: number
  source_end: number
  width: number
  height: number
  score: number
  focus_x: number
  focus_y: number
  motion: string
  asset_url: string
  source_name: string
}

export type PreviewVariant = {
  id: string
  index: number
  name: string
  description: string
  score: number
  items: TimelineItem[]
}

export type PreviewData = {
  project: Project
  duration: number
  audio_offset: number
  bpm: number
  beat_confidence: number
  beats: number[]
  downbeats: number[]
  analyzer: string
  track_url: string
  energy: Array<{ time: number; value: number }>
  sections: Array<{ start: number; end: number; label: string; energy: number }>
  variants: PreviewVariant[]
}
