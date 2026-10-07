import type { DurationMode, Job, MediaAsset, MediaFilter, MediaOrder, MusicTrack, PreviewData, Project, ProjectDetail } from './types'

export class ApiError extends Error {}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: options?.body instanceof FormData
      ? options.headers
      : { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (response.status === 204) return undefined as T
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(payload.detail || 'Request failed')
  return payload as T
}

export const api = {
  session: () => request<{ user_id: string; label: string }>('/api/session'),
  createSessionTransfer: (nextPath: string) =>
    request<{ url: string; expires_in: string }>('/api/session/transfers', {
      method: 'POST',
      body: JSON.stringify({ next_path: nextPath }),
    }),
  projects: () => request<Project[]>('/api/projects'),
  createProject: (name = 'New reel') =>
    request<Project>('/api/projects', { method: 'POST', body: JSON.stringify({ name }) }),
  project: (id: string) => request<ProjectDetail>(`/api/projects/${id}`),
  updateProject: (id: string, values: Partial<Project>) =>
    request<Project>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(values) }),
  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  attachAssets: (projectId: string, assetIds: string[]) =>
    request<{ attached: number }>(`/api/projects/${projectId}/assets`, {
      method: 'POST',
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  reorderAssets: (projectId: string, assetIds: string[]) =>
    request<{ ordered: number }>(`/api/projects/${projectId}/assets/order`, {
      method: 'PUT',
      body: JSON.stringify({ asset_ids: assetIds }),
    }),
  selectAsset: (projectId: string, assetId: string, selected: boolean) =>
    request<{ selected: boolean }>(`/api/projects/${projectId}/assets/${assetId}`, {
      method: 'PATCH',
      body: JSON.stringify({ selected }),
    }),
  removeAsset: (projectId: string, assetId: string) =>
    request<void>(`/api/projects/${projectId}/assets/${assetId}`, { method: 'DELETE' }),
  processProject: (projectId: string, profile: string) =>
    request<{ job_id: string }>(`/api/projects/${projectId}/process`, {
      method: 'POST',
      body: JSON.stringify({ profile }),
    }),
  library: () => request<MediaAsset[]>('/api/library'),
  music: () => request<MusicTrack[]>('/api/music'),
  musicFromAsset: (assetId: string) =>
    request<{ track: MusicTrack; job_id: string }>(`/api/music/from-media/${assetId}`, {
      method: 'POST',
    }),
  generate: (
    projectId: string,
    values: {
      duration: DurationMode
      include_all: boolean
      media_filter: MediaFilter
      media_order: MediaOrder
      random_seed: number
      min_clip_duration: number
      music_track_id: string
      music_start: number | null
    },
  ) =>
    request<PreviewData>(`/api/projects/${projectId}/variants`, {
      method: 'POST',
      body: JSON.stringify(values),
    }),
  preview: (projectId: string) => request<PreviewData>(`/api/projects/${projectId}/preview`),
  debugLog: (projectId: string) =>
    request<Record<string, unknown>>(`/api/projects/${projectId}/debug-log`),
  startExport: (variantId: string) =>
    request<{ job_id: string }>(`/api/variants/${variantId}/exports`, { method: 'POST' }),
  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
}

export function uploadProjectFile(
  projectId: string,
  file: File,
  onProgress: (progress: number) => void,
): Promise<MediaAsset> {
  return uploadFile(`/api/projects/${projectId}/media`, file, onProgress) as Promise<MediaAsset>
}

export function uploadMusicFile(file: File, onProgress: (progress: number) => void) {
  return uploadFile('/api/music', file, onProgress) as Promise<{ track: MusicTrack; job_id: string }>
}

export function downloadFile(
  url: string,
  filename: string,
  onProgress: (progress: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('GET', url)
    xhr.responseType = 'blob'
    xhr.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    })
    xhr.addEventListener('load', () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiError('Could not download the exported video'))
        return
      }
      const objectUrl = URL.createObjectURL(xhr.response)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = filename
      document.body.appendChild(link)
      link.click()
      window.setTimeout(() => {
        link.remove()
        URL.revokeObjectURL(objectUrl)
      }, 60_000)
      resolve()
    })
    xhr.addEventListener('error', () => reject(new ApiError('Video download interrupted')))
    xhr.send()
  })
}

function uploadFile(url: string, file: File, onProgress: (progress: number) => void): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    })
    xhr.addEventListener('load', () => {
      const payload = JSON.parse(xhr.responseText || '{}')
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload)
      else reject(new ApiError(payload.detail || 'Could not upload the file'))
    })
    xhr.addEventListener('error', () => reject(new ApiError('Connection interrupted')))
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}
