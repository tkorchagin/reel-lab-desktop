import { ArrowUpRight, Copy, Download, Film, Image as ImageIcon, LoaderCircle, Maximize2, Pause, Play, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, downloadFile } from '../api'
import { copyText } from '../clipboard'
import type { PreviewData } from '../types'
import BufferedVideo from './BufferedVideo'
import type { BufferedVideoHandle } from './BufferedVideo'
import ProgressBar from './ProgressBar'

type Props = {
  data: PreviewData | null
  variantIndex: number
  generating: boolean
  generationMs: number | null
  selectedAssetIds: ReadonlySet<string>
  selectionBusyId: string | null
  recentlyExcluded: { id: string; name: string } | null
  onVariantChange: (index: number) => void
  onOpenFull: () => void
  onOpenAsset: (assetId: string) => void
  onAssetSelectionChange: (assetId: string, selected: boolean, name: string) => void
  onRestoreAsset: () => void
  onDismissExcluded: () => void
  onError: (message: string) => void
  pauseToken?: number
  onPlaybackStart?: () => void
}

export default function LivePreviewPanel({ data, variantIndex, generating, generationMs, selectedAssetIds, selectionBusyId, recentlyExcluded, onVariantChange, onOpenFull, onOpenAsset, onAssetSelectionChange, onRestoreAsset, onDismissExcluded, onError, pauseToken, onPlaybackStart }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const videoRef = useRef<BufferedVideoHandle>(null)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [exportProgress, setExportProgress] = useState(0)
  const [exportLabel, setExportLabel] = useState('Download')
  const [exportVariantId, setExportVariantId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [copyingLog, setCopyingLog] = useState(false)
  const [debugLogLabel, setDebugLogLabel] = useState('Logs')

  const variant = data?.variants[variantIndex] || data?.variants[0]
  const sourceColors = useMemo(() => buildSourceColorMap(variant?.items || []), [variant])
  const isCurrentVariantExport = exportVariantId === variant?.id
  const visibleExportProgress = isCurrentVariantExport ? exportProgress : 0
  const visibleExportLabel = isCurrentVariantExport ? exportLabel : 'Download'
  const itemIndex = useMemo(() => {
    if (!variant) return -1
    const found = variant.items.findIndex((item) => time >= item.timeline_start && time < item.timeline_end)
    return found >= 0 ? found : Math.max(0, variant.items.length - 1)
  }, [time, variant])
  const currentItem = variant?.items[itemIndex]

  const seekTo = useCallback((nextTime: number) => {
    if (!data || !audioRef.current) return
    const local = Math.max(0, Math.min(data.duration, nextTime))
    audioRef.current.currentTime = data.audio_offset + local
    setTime(local)
  }, [data])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !data) return
    audio.pause()
    setPlaying(false)
    setTime(0)
    const alignAudio = () => { audio.currentTime = data.audio_offset }
    if (audio.readyState >= 1) alignAudio()
    else {
      audio.addEventListener('loadedmetadata', alignAudio, { once: true })
      audio.load()
    }
    return () => audio.removeEventListener('loadedmetadata', alignAudio)
  }, [data])

  const togglePlayback = useCallback(async () => {
    const audio = audioRef.current
    if (!audio || !data) return
    onError('')
    if (playing) {
      audio.pause()
      setPlaying(false)
      return
    }
    const localTime = time >= data.duration - 0.05 ? 0 : time
    const expectedAudioTime = data.audio_offset + localTime
    try {
      onPlaybackStart?.()
      await videoRef.current?.waitUntilReady()
      await playAudioFrom(audio, expectedAudioTime)
      setTime(localTime)
      setPlaying(true)
    } catch (reason) {
      console.warn('Preview playback failed', reason)
      onError('The browser could not start the preview')
    }
  }, [data, onError, onPlaybackStart, playing, time])

  useEffect(() => {
    if (pauseToken === undefined) return
    audioRef.current?.pause()
    const timer = window.setTimeout(() => setPlaying(false), 0)
    return () => window.clearTimeout(timer)
  }, [pauseToken])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const stage = target?.closest('.live-stage')
      const blocked = target?.closest('input, textarea, select, [contenteditable="true"]') || (target?.closest('button') && !stage)
      if (event.code !== 'Space' || blocked) return
      event.preventDefault()
      togglePlayback()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [togglePlayback])

  useEffect(() => {
    if (!playing || !data) return
    let request = 0
    const tick = () => {
      const audio = audioRef.current
      if (!audio) return
      const local = audio.currentTime - data.audio_offset
      if (local >= data.duration) {
        audio.pause()
        setTime(data.duration)
        setPlaying(false)
        return
      }
      setTime(Math.max(0, local))
      request = requestAnimationFrame(tick)
    }
    request = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(request)
  }, [data, playing])

  function chooseVariant(index: number) {
    audioRef.current?.pause()
    setPlaying(false)
    onVariantChange(index)
    window.setTimeout(() => seekTo(0), 0)
  }

  async function exportVideo() {
    if (!variant) return
    setExportVariantId(variant.id)
    setExporting(true)
    setExportProgress(2)
    setExportLabel('Starting…')
    try {
      const started = await api.startExport(variant.id)
      let job = await api.job(started.job_id)
      while (job.status !== 'ready' && job.status !== 'error') {
        setExportProgress(Math.min(90, Math.round(job.progress * 0.9)))
        setExportLabel(job.stage)
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        job = await api.job(started.job_id)
      }
      if (job.status === 'error') throw new Error(job.error || 'Could not export the video')
      setExportProgress(92)
      setExportLabel('Downloading…')
      await downloadFile(
        String(job.result.url),
        `reel-${variant.id.slice(0, 8)}.mp4`,
        (progress) => setExportProgress(92 + Math.round(progress * 0.08)),
      )
      setExportProgress(100)
      setExportLabel('Download again')
    } catch (reason) {
      onError((reason as Error).message)
      setExportLabel('Export failed')
    } finally {
      setExporting(false)
    }
  }

  async function copyDebugLog() {
    if (!data) return
    setCopyingLog(true)
    setDebugLogLabel('Copying…')
    try {
      const payload = await api.debugLog(data.project.id)
      await copyText(JSON.stringify(payload, null, 2))
      setDebugLogLabel('Copied')
      window.setTimeout(() => setDebugLogLabel('Logs'), 1800)
    } catch (reason) {
      setDebugLogLabel('Logs')
      onError((reason as Error).message)
    } finally {
      setCopyingLog(false)
    }
  }

  if (!data || !variant) {
    return (
      <aside className="live-preview-panel empty-live-preview">
        <span className={generating ? 'live-status working' : 'live-status'}>{generating ? 'Creating versions…' : 'Choose a ready music track'}</span>
      </aside>
    )
  }

  const position = `${Math.round((currentItem?.focus_x ?? 0.5) * 100)}% ${Math.round((currentItem?.focus_y ?? 0.5) * 100)}%`
  const photoProgress = currentItem ? Math.max(0, Math.min(1, (time - currentItem.timeline_start) / Math.max(0.01, currentItem.timeline_end - currentItem.timeline_start))) : 0
  const photoScale = currentItem?.motion === 'zoom-out' ? 1.1 - photoProgress * 0.1 : 1 + photoProgress * 0.1
  const currentAssetSelected = currentItem ? selectedAssetIds.has(currentItem.asset_id) : false
  const currentAssetBusy = currentItem?.asset_id === selectionBusyId

  return (
    <aside className="live-preview-panel">
      <div className="live-preview-header">
        <div><span className={generating ? 'live-status working' : 'live-status'}>{generating ? 'Updating…' : generationMs === null ? `${data.variants.length} versions` : `Ready in ${formatGenerationTime(generationMs)}`}</span><strong>{variant.name}</strong></div>
        <button className="icon-button" type="button" title="Open full edit preview" onClick={onOpenFull}><Maximize2 size={17} /></button>
      </div>
      <div className="live-stage-wrap">
        <button className="live-stage" type="button" onClick={togglePlayback} title={playing ? 'Pause' : 'Play'}>
          <BufferedVideo ref={videoRef} currentItem={currentItem} nextItem={variant.items[itemIndex + 1]} time={time} playing={playing} objectPosition={position} />
          {currentItem?.kind === 'photo' ? <img className="preview-photo" src={currentItem.asset_url} alt="Reel frame" style={{ objectPosition: position, transformOrigin: position, transform: `scale(${photoScale})` }} /> : null}
          {!playing ? <span className="live-play-mark"><Play size={21} fill="currentColor" /></span> : null}
          <span className="stage-overlay"><span>{currentItem?.source_name}</span><span>{itemIndex + 1} / {variant.items.length}</span></span>
          {generating ? <span className="live-generating-overlay">Updating the edit</span> : null}
        </button>
      </div>
      <div className="live-timeline-area">
        <button
          className="live-waveform"
          type="button"
          aria-label="Music timeline"
          title="Seek through the music"
          onClick={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect()
            seekTo(((event.clientX - bounds.left) / bounds.width) * data.duration)
          }}
        >
          {data.energy.map((point, index) => <span className="live-energy-bar" style={{ height: `${Math.max(3, point.value * 32)}px` }} key={`${point.time}-${index}`} />)}
          {data.beats.map((beat) => <span className="live-beat-marker" style={{ left: `${(beat / data.duration) * 100}%` }} key={`beat-${beat}`} />)}
          {data.downbeats.map((beat) => <span className="live-beat-marker downbeat" style={{ left: `${(beat / data.duration) * 100}%` }} key={`downbeat-${beat}`} />)}
          {data.sections.slice(1).map((section) => <span className="live-section-marker" style={{ left: `${Math.max(0, Math.min(100, (section.start / data.duration) * 100))}%` }} title={section.label} key={`${section.start}-${section.label}`} />)}
        </button>
        <div className="live-timeline" aria-label="Edit timeline">
          {variant.items.map((item, index) => (
            <button
              className={`live-timeline-clip source-color-${sourceColors.get(item.path) ?? 0}${index === itemIndex ? ' active' : ''}`}
              style={{ width: `${((item.timeline_end - item.timeline_start) / data.duration) * 100}%` }}
              type="button"
              title={`${item.source_name} · ${formatPreciseTime(item.timeline_start)}`}
              key={`${item.candidate_id}-${index}`}
              onClick={() => seekTo(item.timeline_start)}
            />
          ))}
        </div>
        <span className="live-timeline-playhead" style={{ left: `${(time / data.duration) * 100}%` }} />
      </div>
      <div className="live-source-tools">
        {currentItem ? <div className="live-source-row">
          <button className="live-source-link" type="button" title={`Open ${currentItem.source_name} in Media`} onClick={() => onOpenAsset(currentItem.asset_id)}>
            {currentItem.kind === 'video' ? <Film size={15} /> : <ImageIcon size={15} />}
            <span><strong>{currentItem.source_name}</strong><small>Open in Media</small></span>
            <ArrowUpRight size={15} />
          </button>
          <div className="live-source-usage">
            <span>Include</span>
            {currentAssetBusy ? <LoaderCircle className="spin" size={18} /> : <label className="switch compact-switch" title={currentAssetSelected ? 'Exclude from the reel' : 'Restore to the reel'}><input type="checkbox" checked={currentAssetSelected} disabled={currentAssetSelected && selectedAssetIds.size <= 1} onChange={(event) => onAssetSelectionChange(currentItem.asset_id, event.target.checked, currentItem.source_name)} aria-label={`Include ${currentItem.source_name}`} /><span /></label>}
          </div>
        </div> : null}
        {recentlyExcluded ? <div className="live-source-undo" role="status">
          <span><strong>{recentlyExcluded.name}</strong> excluded</span>
          <button className="text-button" type="button" disabled={selectionBusyId === recentlyExcluded.id} onClick={onRestoreAsset}>{selectionBusyId === recentlyExcluded.id ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />}Restore</button>
          <button className="icon-button" type="button" title="Dismiss message" onClick={onDismissExcluded}><X size={14} /></button>
        </div> : null}
      </div>
      <div className="live-transport">
        <button className="icon-button transport-play" type="button" title={playing ? 'Pause' : 'Play'} onClick={togglePlayback}>{playing ? <Pause size={17} /> : <Play size={17} />}</button>
        <input className="scrubber" type="range" min={0} max={data.duration} step={0.01} value={time} onChange={(event) => seekTo(Number(event.target.value))} aria-label="Playback position" />
        <span className="time-label">{formatTime(time)} / {formatTime(data.duration)}</span>
      </div>
      <div className="live-variant-grid" aria-label="Edit versions">
        {data.variants.map((entry, index) => <button className={`live-variant-button${index === variantIndex ? ' active' : ''}`} type="button" key={entry.id} onClick={() => chooseVariant(index)}><span>{String(entry.index).padStart(2, '0')}</span><strong>{entry.name}</strong><small>{Math.round(entry.score * 100)}</small></button>)}
      </div>
      <div className="live-export-row">
        <span>{data.bpm.toFixed(1)} BPM · {variant.items.length} clips</span>
        <div className="live-export-actions">
          <button className="secondary compact" type="button" title="Copy edit debug data" disabled={generating || copyingLog} onClick={copyDebugLog}><Copy size={16} />{debugLogLabel}</button>
          <div className="live-download"><button className="primary compact" type="button" disabled={generating || exporting} onClick={exportVideo}><Download size={17} />{visibleExportLabel}</button><ProgressBar value={visibleExportProgress} error={visibleExportLabel === 'Export failed'} /></div>
        </div>
      </div>
      <audio ref={audioRef} src={data.track_url} preload="auto" />
    </aside>
  )
}

function formatGenerationTime(value: number) {
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`
}

function buildSourceColorMap(items: Array<{ path: string }>) {
  const colors = new Map<string, number>()
  for (const item of items) {
    if (!colors.has(item.path)) colors.set(item.path, colors.size % 6)
  }
  return colors
}

function formatTime(value: number) {
  const total = Math.max(0, Math.floor(value))
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function formatPreciseTime(value: number) {
  const minutes = Math.floor(value / 60)
  const seconds = value - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(1).padStart(4, '0')}`
}

async function playAudioFrom(audio: HTMLAudioElement, start: number) {
  if (audio.readyState < 1) audio.load()
  if (audio.readyState < 1) await ensureMetadata(audio)
  if (Number.isFinite(start) && Math.abs(audio.currentTime - start) > 0.05) {
    await seekMedia(audio, start)
  }
  await audio.play()
}

function seekMedia(media: HTMLMediaElement, time: number) {
  return new Promise<void>((resolve) => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      window.clearTimeout(timeout)
      media.removeEventListener('seeked', finish)
      resolve()
    }
    const timeout = window.setTimeout(finish, 1500)
    media.addEventListener('seeked', finish, { once: true })
    media.currentTime = time
    if (!media.seeking && Math.abs(media.currentTime - time) <= 0.02) finish()
  })
}

function ensureMetadata(media: HTMLMediaElement) {
  if (media.readyState >= 1) return Promise.resolve()
  media.load()
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      media.removeEventListener('loadedmetadata', handleLoaded)
      media.removeEventListener('error', handleError)
    }
    const handleLoaded = () => {
      cleanup()
      resolve()
    }
    const handleError = () => {
      cleanup()
      reject(new Error('Media metadata failed'))
    }
    media.addEventListener('loadedmetadata', handleLoaded, { once: true })
    media.addEventListener('error', handleError, { once: true })
  })
}
