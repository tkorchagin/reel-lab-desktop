import { ArrowLeft, Dices, Images, Pause, Play } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, MouseEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import AudioRangePicker from '../components/AudioRangePicker'
import LivePreviewPanel from '../components/LivePreviewPanel'
import ProjectSteps from '../components/ProjectSteps'
import SessionTransferButton from '../components/SessionTransferButton'
import type { DurationMode, MediaFilter, MediaOrder, MusicTrack, PreviewData, ProjectDetail } from '../types'

type GenerationSettings = {
  duration: DurationMode
  include_all: boolean
  media_filter: MediaFilter
  media_order: MediaOrder
  random_seed: number
  min_clip_duration: number
  music_track_id: string
  music_start: number | null
}

const GENRE_LABELS: Record<string, string> = {
  pop: 'Pop',
  dance: 'Dance',
  electronic: 'Electronic',
  'hip-hop': 'Hip-hop',
  rock: 'Rock',
  alternative: 'Alternative',
  rnb: 'R&B',
  latin: 'Latin',
  folk: 'Folk',
  soundtrack: 'Soundtrack',
  country: 'Country',
}
const GENRE_ORDER = Object.keys(GENRE_LABELS)
const CHARACTERISTIC_FILTERS = ['energetic', 'calm', 'strong drop', 'dynamic', 'upbeat', 'slow']

export default function SettingsPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [tracks, setTracks] = useState<MusicTrack[]>([])
  const [duration, setDuration] = useState<DurationMode>(30)
  const [includeAll, setIncludeAll] = useState(false)
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>('all')
  const [mediaOrder, setMediaOrder] = useState<MediaOrder>('manual')
  const [randomSeed, setRandomSeed] = useState(20260725)
  const [minClipDuration, setMinClipDuration] = useState(0.6)
  const [trackId, setTrackId] = useState('')
  const [musicStart, setMusicStart] = useState<number | null>(null)
  const [tagFilter, setTagFilter] = useState('all')
  const [previewTrackId, setPreviewTrackId] = useState<string | null>(null)
  const [previewLoadingId, setPreviewLoadingId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [initialized, setInitialized] = useState(false)
  const [previewData, setPreviewData] = useState<PreviewData | null>(null)
  const [variantIndex, setVariantIndex] = useState(0)
  const [generating, setGenerating] = useState(false)
  const [generationMs, setGenerationMs] = useState<number | null>(null)
  const [selectionBusyId, setSelectionBusyId] = useState<string | null>(null)
  const [recentlyExcluded, setRecentlyExcluded] = useState<{ id: string; name: string } | null>(null)
  const [livePauseToken, setLivePauseToken] = useState(0)
  const generationSettingsRef = useRef<GenerationSettings | null>(null)
  const generationVersionRef = useRef(0)
  const generationRunningRef = useRef(false)
  const mountedRef = useRef(true)
  const previewAudioRef = useRef<HTMLAudioElement>(null)

  useEffect(() => {
    Promise.all([api.project(projectId), api.music()]).then(async ([loadedProject, musicData]) => {
      let projectData = loadedProject
      if (projectData.project.processing_profile !== 'maximum') {
        await api.updateProject(projectId, { processing_profile: 'maximum' })
        projectData = await api.project(projectId)
      }
      const selectedAssets = projectData.assets.filter((asset) => asset.selected)
      if (selectedAssets.length && selectedAssets.some((asset) => asset.analysis.status !== 'ready')) {
        navigate(`/projects/${projectId}/media`, { replace: true })
        return
      }
      const presetTracks = musicData.filter((track) => track.is_preset)
      setDetail(projectData)
      setTracks(presetTracks)
      setDuration(projectData.project.duration)
      setIncludeAll(projectData.project.include_all)
      setMediaFilter(projectData.project.media_filter)
      setMediaOrder(projectData.project.media_order)
      setRandomSeed(projectData.project.random_seed)
      setMinClipDuration(projectData.project.min_clip_duration || 0.6)
      const preferred = presetTracks.some((track) => track.id === projectData.project.music_track_id)
        ? projectData.project.music_track_id || ''
        : presetTracks.find((track) => track.status === 'ready')?.id || ''
      setTrackId(preferred)
      setMusicStart(preferred === projectData.project.music_track_id ? projectData.project.music_start : null)
      setInitialized(true)
      if (projectData.project.status === 'preview') api.preview(projectId).then(setPreviewData).catch(() => {})
    }).catch((reason) => setError(reason.message))
  }, [navigate, projectId])
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])
  useEffect(() => {
    if (!tracks.some((track) => track.status === 'processing' || track.status === 'queued')) return
    const timer = window.setInterval(() => api.music().then((items) => setTracks(items.filter((track) => track.is_preset))).catch(() => {}), 800)
    return () => window.clearInterval(timer)
  }, [tracks])

  const selectedTrack = useMemo(() => tracks.find((track) => track.id === trackId), [tracks, trackId])
  const trackRows = useMemo(() => tracks.map((track) => {
    const characteristics = musicCharacteristics(track)
    return { track, characteristics, tags: musicTags(track, characteristics) }
  }), [tracks])
  const musicFilterOptions = useMemo(() => {
    const availableGenres = new Set(tracks.flatMap((track) => track.metadata.genres || []))
    return [
      { value: 'all', label: 'All' },
      ...GENRE_ORDER.filter((genre) => availableGenres.has(genre)).map((genre) => ({
        value: `genre:${genre}`,
        label: GENRE_LABELS[genre],
      })),
      ...CHARACTERISTIC_FILTERS.map((tag) => ({ value: `tag:${tag}`, label: tag })),
    ]
  }, [tracks])
  const visibleTrackRows = useMemo(
    () => tagFilter === 'all' ? trackRows : trackRows.filter((row) => {
      const [kind, value] = tagFilter.split(':', 2)
      return kind === 'genre'
        ? (row.track.metadata.genres || []).includes(value)
        : kind === 'tag' && row.characteristics.includes(value)
    }),
    [tagFilter, trackRows],
  )
  const previewMatchesSelection = previewData?.project.music_track_id === trackId && previewData.project.duration === duration
  const fragmentDuration = duration === 0
    ? Math.min(selectedTrack?.duration || 30, previewMatchesSelection ? previewData.duration : 30)
    : duration
  const highlight = duration === 0 ? undefined : selectedTrack?.highlights[String(duration)]
  const automaticStart = duration === 0 && previewMatchesSelection
    ? previewData.audio_offset
    : highlight?.start ?? selectedTrack?.highlights['30']?.start ?? 0
  const selectionDuration = Math.min(fragmentDuration, selectedTrack?.duration || fragmentDuration)
  const maxMusicStart = Math.max(0, (selectedTrack?.duration || fragmentDuration) - selectionDuration)
  const effectiveMusicStart = Math.min(maxMusicStart, Math.max(0, musicStart ?? automaticStart))
  const selectedAssetIds = useMemo(() => new Set(detail?.assets.filter((asset) => asset.selected).map((asset) => asset.id) || []), [detail?.assets])

  const generateLatest = useCallback(async () => {
    if (generationRunningRef.current) return
    generationRunningRef.current = true
    if (mountedRef.current) setGenerating(true)
    try {
      let completedVersion = -1
      while (completedVersion !== generationVersionRef.current) {
        completedVersion = generationVersionRef.current
        const values = generationSettingsRef.current
        if (!values) break
        const started = performance.now()
        try {
          const next = await api.generate(projectId, values)
          if (completedVersion === generationVersionRef.current && mountedRef.current) {
            setPreviewData(next)
            setVariantIndex((current) => Math.min(current, Math.max(0, next.variants.length - 1)))
            setGenerationMs(Math.round(performance.now() - started))
            setError('')
          }
        } catch (reason) {
          if (completedVersion === generationVersionRef.current && mountedRef.current) {
            setError((reason as Error).message)
            break
          }
        }
      }
    } finally {
      generationRunningRef.current = false
      if (mountedRef.current) setGenerating(false)
    }
  }, [projectId])

  useEffect(() => {
    if (!initialized || !trackId || selectedTrack?.status !== 'ready') return
    generationSettingsRef.current = {
      duration,
      include_all: includeAll,
      media_filter: mediaFilter,
      media_order: mediaOrder,
      random_seed: randomSeed,
      min_clip_duration: minClipDuration,
      music_track_id: trackId,
      music_start: musicStart,
    }
    generationVersionRef.current += 1
    const timer = window.setTimeout(() => { void generateLatest() }, 220)
    return () => window.clearTimeout(timer)
  }, [duration, generateLatest, includeAll, initialized, mediaFilter, mediaOrder, minClipDuration, musicStart, randomSeed, selectedTrack?.status, trackId])

  async function changeAssetSelection(assetId: string, selected: boolean, name: string) {
    if (!detail) return
    const asset = detail.assets.find((item) => item.id === assetId)
    if (!asset || asset.selected === selected) return
    if (!selected && selectedAssetIds.size <= 1) {
      setError('Keep at least one photo or video in the reel')
      return
    }
    setSelectionBusyId(assetId)
    try {
      await api.selectAsset(projectId, assetId, selected)
      setDetail((current) => current ? {
        ...current,
        assets: current.assets.map((item) => item.id === assetId ? { ...item, selected } : item),
      } : current)
      setRecentlyExcluded((current) => selected ? current?.id === assetId ? null : current : { id: assetId, name })
      setError('')
      generationVersionRef.current += 1
      void generateLatest()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setSelectionBusyId(null)
    }
  }

  function changeDuration(value: DurationMode) {
    if (value === duration) return
    setDuration(value)
    setMusicStart(null)
  }

  function changeTrack(value: string) {
    if (value === trackId) return
    setTrackId(value)
    setMusicStart(null)
  }

  function rerollVariants() {
    setRandomSeed(nextRandomSeed)
  }

  const pauseTrackPreview = useCallback(() => {
    previewAudioRef.current?.pause()
    setPreviewTrackId(null)
    setPreviewLoadingId(null)
  }, [])

  function handleTrackKeyDown(event: KeyboardEvent<HTMLDivElement>, track: MusicTrack) {
    if (track.status === 'error' || !['Enter', ' '].includes(event.key)) return
    event.preventDefault()
    changeTrack(track.id)
  }

  async function toggleTrackPreview(track: MusicTrack, event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation()
    const audio = previewAudioRef.current
    if (!audio || track.status !== 'ready') return
    if (previewTrackId === track.id && !audio.paused) {
      audio.pause()
      setPreviewTrackId(null)
      setPreviewLoadingId(null)
      return
    }
    setLivePauseToken((value) => value + 1)
    setPreviewTrackId(track.id)
    setPreviewLoadingId(track.id)
    try {
      const previewUrl = new URL(`/api/music/${track.id}/content`, window.location.href).href
      if (audio.src !== previewUrl) {
        audio.src = previewUrl
        audio.load()
      }
      await seekAndPlay(audio, previewStart(track, duration))
      setError('')
    } catch {
      setPreviewTrackId(null)
      setError('Could not play the track preview')
    } finally {
      setPreviewLoadingId(null)
    }
  }

  return (
    <main className="project-page settings-page">
      <div className="project-page-header results-header">
        <div className="project-title-group">
          <button className="icon-button" type="button" title="Back to Media" onClick={() => navigate(`/projects/${projectId}/media`)}><ArrowLeft size={18} /></button>
          <strong className="results-project-name">{detail?.project.name || 'Reel'}</strong>
        </div>
        <div className="project-header-actions">
          <SessionTransferButton />
          <ProjectSteps active={1} projectId={projectId} />
        </div>
      </div>
      <div className="settings-editor-layout">
        <div className="settings-controls">
          <div className="quick-settings-panel">
            <section className="settings-section duration-section">
              <div className="duration-title-row">
                <h2>Duration</h2>
                <div className="segmented duration-picker">
                  {[
                    { value: 15, label: '15 s' },
                    { value: 30, label: '30 s' },
                    { value: 60, label: '60 s' },
                    { value: 0, label: 'Max' },
                  ].map((item) => (
                    <button className={duration === item.value ? 'active' : ''} type="button" key={item.value} onClick={() => changeDuration(item.value as DurationMode)}>
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
              {duration === 0 ? <span className="duration-hint">Use the strongest moments from your selected media and let the reel find its length.</span> : null}
            </section>
          </div>
          <div className="settings-controls-scroll">
          <section className="settings-section materials-settings-section">
            <div className="section-title-row">
              <div>
                <h2>Media</h2>
                <span className="section-summary">Selected {detail?.assets.filter((asset) => asset.selected).length || 0} of {detail?.assets.length || 0}</span>
              </div>
              <button className="secondary" type="button" onClick={() => navigate(`/projects/${projectId}/media?return=results`)}><Images size={17} />Change selection</button>
            </div>
            <div className="setting-row">
              <span><strong>Clip selection</strong><small>{duration === 0 ? 'Max picks the strongest moments from all selected media' : includeAll ? 'Include every selected file in the reel' : 'Let the algorithm choose the best media'}</small></span>
              <div className="segmented compact-segment">
                <button className={!includeAll && duration !== 0 ? 'active' : ''} type="button" disabled={duration === 0} onClick={() => setIncludeAll(false)}>Best</button>
                <button className={includeAll && duration !== 0 ? 'active' : ''} type="button" disabled={duration === 0} onClick={() => setIncludeAll(true)}>All</button>
              </div>
            </div>
            <div className="setting-row media-filter-setting-row">
              <span><strong>Media type</strong><small>{mediaFilter === 'all' ? 'Mix videos and photos in one reel' : mediaFilter === 'video' ? 'Create versions using videos only' : 'Photos with gentle zooms in and out'}</small></span>
              <div className="segmented media-filter-picker" aria-label="Media type">
                <button className={mediaFilter === 'all' ? 'active' : ''} type="button" onClick={() => setMediaFilter('all')}>Videos + photos</button>
                <button className={mediaFilter === 'video' ? 'active' : ''} type="button" onClick={() => setMediaFilter('video')}>Videos only</button>
                <button className={mediaFilter === 'photo' ? 'active' : ''} type="button" onClick={() => setMediaFilter('photo')}>Photos only</button>
              </div>
            </div>
            <div className="setting-row order-setting-row">
              <span><strong>Media order</strong><small>{mediaOrder === 'capture_date' ? 'Use capture times from file metadata' : mediaOrder === 'manual' ? 'Use the order on the Media screen' : 'Shuffle the media for each version'}</small></span>
              <div className="order-controls">
                <div className="segmented order-picker" aria-label="Media order">
                  <button className={mediaOrder === 'capture_date' ? 'active' : ''} type="button" onClick={() => setMediaOrder('capture_date')}>Capture date</button>
                  <button className={mediaOrder === 'manual' ? 'active' : ''} type="button" onClick={() => setMediaOrder('manual')}>Media order</button>
                  <button className={mediaOrder === 'random' ? 'active' : ''} type="button" onClick={() => setMediaOrder('random')}>Random</button>
                </div>
                <button className="secondary compact dice-button" type="button" disabled={generating} title="Generate new versions" aria-label="Generate new versions" onClick={rerollVariants}>
                  <Dices size={17} />
                </button>
              </div>
            </div>
            <div className="setting-row clip-duration-setting-row">
              <span><strong>Minimum clip length</strong><small>{minClipDuration.toFixed(1)} s · shorter clips mean faster cuts</small></span>
              <div className="segmented compact-segment clip-duration-picker" aria-label="Minimum clip length">
                {[0.2, 0.4, 0.6, 0.9, 1.3].map((value) => (
                  <button className={Math.abs(minClipDuration - value) < 0.01 ? 'active' : ''} type="button" key={value} onClick={() => setMinClipDuration(value)}>
                    {value.toFixed(1)} s
                  </button>
                ))}
              </div>
            </div>
          </section>
          <section className="settings-section music-section">
            <div className="section-title-row">
              <div><h2>Music</h2><span className="section-summary">Catalog genres · mood from tempo and energy</span></div>
            </div>
            {initialized && tracks.length === 0 ? <div className="empty-state compact-empty"><strong>No music tracks yet</strong><span>Add your own songs to <code>backend/data/audio-library/</code>, restart the server, and reload this page.</span></div> : null}
            <div className="music-filter-chips" aria-label="Filter tracks by genre and mood">
              {musicFilterOptions.map((option) => (
                <button className={tagFilter === option.value ? 'active' : ''} type="button" key={option.value} onClick={() => setTagFilter(option.value)}>
                  {option.label}
                </button>
              ))}
            </div>
            <div className="music-list">
              {visibleTrackRows.map(({ track, tags }) => (
                <div
                  className={`music-row${track.id === trackId ? ' active' : ''}${track.status === 'error' ? ' disabled' : ''}${previewTrackId === track.id ? ' previewing' : ''}`}
                  role="button"
                  tabIndex={track.status === 'error' ? -1 : 0}
                  aria-pressed={track.id === trackId}
                  aria-disabled={track.status === 'error'}
                  key={track.id}
                  onClick={() => { if (track.status !== 'error') changeTrack(track.id) }}
                  onKeyDown={(event) => handleTrackKeyDown(event, track)}
                >
                  <button className="track-preview-button" type="button" disabled={track.status !== 'ready'} title={previewTrackId === track.id ? 'Pause' : 'Preview the best section'} onClick={(event) => { void toggleTrackPreview(track, event) }}>
                    {previewTrackId === track.id && previewLoadingId !== track.id ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
                  </button>
                  <span className="music-copy">
                    <strong>{track.title}{track.metadata.version ? ` · ${track.metadata.version}` : ''}</strong>
                    {track.metadata.artist ? <span>{track.metadata.artist}</span> : null}
                    <span>{track.status === 'ready' ? `${track.bpm.toFixed(1)} BPM · ${formatDuration(track.duration)}` : track.stage}</span>
                    {track.status === 'ready' && tags.length ? <span className="music-tags">{tags.map((tag) => <span className="music-tag" key={tag}>{tag}</span>)}</span> : null}
                  </span>
                  {track.status !== 'ready' ? <span className="track-progress">{track.progress}%</span> : <span className="radio-mark" />}
                </div>
              ))}
            </div>
          </section>
          </div>
          {selectedTrack?.status === 'ready' && selectionDuration > 0 ? (
            <div className="music-fragment-dock">
              <AudioRangePicker
                trackDuration={selectedTrack.duration}
                selectionDuration={selectionDuration}
                start={effectiveMusicStart}
                automatic={musicStart === null}
                energy={selectedTrack.energy}
                sections={selectedTrack.sections}
                onChange={setMusicStart}
                onReset={() => setMusicStart(null)}
              />
            </div>
          ) : null}
        </div>
        <div className="settings-live-column">
          <LivePreviewPanel
            key={previewData?.variants[0]?.id || 'empty'}
            data={previewData}
            variantIndex={variantIndex}
            generating={generating}
            generationMs={generationMs}
            selectedAssetIds={selectedAssetIds}
            selectionBusyId={selectionBusyId}
            recentlyExcluded={recentlyExcluded}
            onVariantChange={setVariantIndex}
            onOpenFull={() => navigate(`/projects/${projectId}/preview`)}
            onOpenAsset={(assetId) => navigate(`/projects/${projectId}/media?return=results&asset=${assetId}`)}
            onAssetSelectionChange={changeAssetSelection}
            onRestoreAsset={() => { if (recentlyExcluded) void changeAssetSelection(recentlyExcluded.id, true, recentlyExcluded.name) }}
            onDismissExcluded={() => setRecentlyExcluded(null)}
            onError={setError}
            pauseToken={livePauseToken}
            onPlaybackStart={pauseTrackPreview}
          />
        </div>
      </div>
      {error ? <div className="settings-error error-text">{error}</div> : null}
      <audio ref={previewAudioRef} preload="auto" onEnded={() => setPreviewTrackId(null)} />
    </main>
  )
}

function formatDuration(value: number) {
  const total = Math.max(0, Math.round(value))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function nextRandomSeed(current: number) {
  const values = new Uint32Array(1)
  window.crypto.getRandomValues(values)
  const next = values[0] % 2_147_483_647
  return next === current ? (next + 1) % 2_147_483_647 : next
}

function musicCharacteristics(track: MusicTrack) {
  if (track.status !== 'ready') return []
  const values = track.energy.map((point) => point.value)
  const averageEnergy = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0.5
  const sortedValues = [...values].sort((left, right) => left - right)
  const lowEnergy = sortedValues[Math.floor(sortedValues.length * 0.1)] ?? averageEnergy
  const highEnergy = sortedValues[Math.min(sortedValues.length - 1, Math.floor(sortedValues.length * 0.9))] ?? averageEnergy
  const sectionDuration = track.sections.reduce((sum, section) => sum + Math.max(0, section.end - section.start), 0) || track.duration || 1
  const highEnergyShare = track.sections
    .filter((section) => section.label === 'high_energy')
    .reduce((sum, section) => sum + Math.max(0, section.end - section.start), 0) / sectionDuration
  const isCalm = track.bpm > 0 && track.bpm <= 115 && averageEnergy <= 0.52 && highEnergyShare < 0.18
  const tags: string[] = []
  if (track.bpm > 0) {
    if (track.bpm < 92) tags.push('slow')
    else if (track.bpm < 112) tags.push('mid-tempo')
    else if (track.bpm < 132) tags.push(isCalm ? 'mid-tempo' : 'upbeat')
    else tags.push('fast')
  }
  if (averageEnergy >= 0.56 || highEnergyShare >= 0.18) tags.push('energetic')
  else if (isCalm) tags.push('calm')
  if (strongestSectionLift(track) >= 0.32) tags.push('strong drop')
  if (highEnergy - lowEnergy >= 0.83) tags.push('dynamic')
  if (!tags.includes('dynamic') && highEnergy - lowEnergy <= 0.7) tags.push('steady')
  return tags
}

function musicTags(track: MusicTrack, characteristics: string[]) {
  const genreTags = (track.metadata.genres || []).map((genre) => GENRE_LABELS[genre] || genre)
  const catalogTags = (track.metadata.tags || [])
    .filter((tag) => !['shazam', 'russia-top-200'].includes(tag) && !/^\d{4}-\d{2}-\d{2}$/.test(tag))
  return unique([...genreTags, ...characteristics, ...catalogTags]).slice(0, 4)
}

function unique(values: string[]) {
  return values.filter((value, index) => values.indexOf(value) === index)
}

function strongestSectionLift(track: MusicTrack) {
  return track.sections.slice(1).reduce((best, section, index) => {
    const previous = track.sections[index]
    return Math.max(best, section.energy - previous.energy)
  }, 0)
}

function previewStart(track: MusicTrack, duration: DurationMode) {
  const highlight = track.highlights[String(duration || 30)]
  const start = highlight?.start ?? 0
  return Math.min(Math.max(0, start), Math.max(0, track.duration - 1))
}

function seekAndPlay(audio: HTMLAudioElement, start: number) {
  if (audio.readyState < 1) audio.load()
  return audio.play().then(() => waitForMetadata(audio)).then(() => {
    if (Number.isFinite(start) && Math.abs(audio.currentTime - start) > 0.05) {
      audio.currentTime = start
    }
  })
}

function waitForMetadata(audio: HTMLAudioElement) {
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      audio.removeEventListener('loadedmetadata', handleLoaded)
      audio.removeEventListener('error', handleError)
    }
    const handleLoaded = () => {
      cleanup()
      resolve()
    }
    const handleError = () => {
      cleanup()
      reject(new Error('Audio preview failed'))
    }
    if (audio.readyState >= 1) {
      resolve()
      return
    }
    audio.addEventListener('loadedmetadata', handleLoaded, { once: true })
    audio.addEventListener('error', handleError, { once: true })
  })
}
