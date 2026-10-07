import { ArrowLeft, Copy, Download, Images, Pause, Play, Settings } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, downloadFile } from '../api'
import { copyText } from '../clipboard'
import BufferedVideo from '../components/BufferedVideo'
import ProgressBar from '../components/ProgressBar'
import type { PreviewData } from '../types'
import type { BufferedVideoHandle } from '../components/BufferedVideo'

export default function PreviewPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const audioRef = useRef<HTMLAudioElement>(null)
  const videoRef = useRef<BufferedVideoHandle>(null)
  const [data, setData] = useState<PreviewData | null>(null)
  const [variantIndex, setVariantIndex] = useState(0)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [exportProgress, setExportProgress] = useState(0)
  const [exportLabel, setExportLabel] = useState('Download')
  const [exportVariantId, setExportVariantId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [copyingLog, setCopyingLog] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.preview(projectId).then(setData).catch((reason) => setError(reason.message))
  }, [projectId])

  const variant = data?.variants[variantIndex]
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
    else audio.addEventListener('loadedmetadata', alignAudio, { once: true })
    return () => audio.removeEventListener('loadedmetadata', alignAudio)
  }, [data])

  const togglePlayback = useCallback(async () => {
    const audio = audioRef.current
    if (!audio || !data) return
    if (playing) {
      audio.pause()
      setPlaying(false)
      return
    }
    const localTime = time >= data.duration - 0.05 ? 0 : time
    const expectedAudioTime = data.audio_offset + localTime
    try {
      await videoRef.current?.waitUntilReady()
      await playAudioFrom(audio, expectedAudioTime)
      setPlaying(true)
    } catch {
      setError('The browser could not start the preview')
    }
  }, [data, playing, time])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (event.code !== 'Space' || target?.closest('input, textarea, select, button, [contenteditable="true"]')) return
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
    setVariantIndex(index)
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
      setError((reason as Error).message)
      setExportLabel('Export failed')
    } finally {
      setExporting(false)
    }
  }

  async function copyDebugLog() {
    setCopyingLog(true)
    try {
      const payload = await api.debugLog(projectId)
      await copyText(JSON.stringify(payload, null, 2))
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setCopyingLog(false)
    }
  }

  if (!data || !variant) return <main className="empty-state fullscreen-empty">{error || 'Loading the preview…'}</main>
  const position = `${Math.round((currentItem?.focus_x ?? 0.5) * 100)}% ${Math.round((currentItem?.focus_y ?? 0.5) * 100)}%`
  const photoProgress = currentItem ? Math.max(0, Math.min(1, (time - currentItem.timeline_start) / Math.max(0.01, currentItem.timeline_end - currentItem.timeline_start))) : 0
  const photoScale = currentItem?.motion === 'zoom-out' ? 1.1 - photoProgress * 0.1 : 1 + photoProgress * 0.1

  return (
    <main className="preview-app">
      <header className="preview-topbar">
        <button className="icon-button" type="button" title="Back to results" onClick={() => navigate(`/projects/${projectId}/results`)}><ArrowLeft size={18} /></button>
        <div className="preview-brand"><strong>{data.project.name}</strong><span>edit preview</span></div>
        <div className="transport">
          <button className="icon-button transport-play" type="button" title={playing ? 'Pause' : 'Play'} onClick={togglePlayback}>{playing ? <Pause size={18} /> : <Play size={18} />}</button>
          <input className="scrubber" type="range" min={0} max={data.duration} step={0.01} value={time} onChange={(event) => seekTo(Number(event.target.value))} aria-label="Playback position" />
          <span className="time-label">{formatTime(time)} / {formatTime(data.duration)}</span>
        </div>
        <div className="preview-meta"><span className="tag">{data.bpm.toFixed(1)} BPM</span><button className="icon-button" type="button" title="Media" onClick={() => navigate(`/projects/${projectId}/media?return=results`)}><Images size={17} /></button><button className="icon-button" type="button" title="Results" onClick={() => navigate(`/projects/${projectId}/results`)}><Settings size={17} /></button><button className="icon-button" type="button" title="Copy edit debug data" disabled={copyingLog} onClick={copyDebugLog}><Copy size={17} /></button><div className="download-control"><button className="primary compact" type="button" disabled={exporting} onClick={exportVideo}><Download size={17} />{visibleExportLabel}</button><ProgressBar value={visibleExportProgress} error={visibleExportLabel === 'Export failed'} /></div></div>
      </header>
      <div className="preview-workspace">
        <aside className="variant-sidebar">
          <h1 className="panel-title">Edit versions</h1>
          <div className="variant-list">{data.variants.map((entry, index) => <button className={`variant-row${index === variantIndex ? ' active' : ''}`} type="button" key={entry.id} onClick={() => chooseVariant(index)}><span>{String(entry.index).padStart(2, '0')}</span><strong>{entry.name}</strong><small>{Math.round(entry.score * 100)}</small></button>)}</div>
        </aside>
        <section className="stage-area">
          <div className="stage-wrap">
            <button className="stage" type="button" onClick={togglePlayback} title="Play or pause">
              <BufferedVideo ref={videoRef} currentItem={currentItem} nextItem={variant.items[itemIndex + 1]} time={time} playing={playing} objectPosition={position} />
              {currentItem?.kind === 'photo' ? <img className="preview-photo" src={currentItem.asset_url} alt="Reel frame" style={{ objectPosition: position, transformOrigin: position, transform: `scale(${photoScale})` }} /> : null}
              <span className="stage-overlay"><span>{currentItem?.source_name}</span><span>{itemIndex + 1} / {variant.items.length}</span></span>
            </button>
          </div>
          <div className="timeline-area">
            <div className="waveform" onClick={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); seekTo(((event.clientX - bounds.left) / bounds.width) * data.duration) }}>{data.energy.map((point, index) => <span className="energy-bar" style={{ height: `${Math.max(5, point.value * 44)}px` }} key={`${point.time}-${index}`} />)}{data.beats.map((beat) => <span className="beat-marker" style={{ left: `${(beat / data.duration) * 100}%` }} key={`beat-${beat}`} />)}{data.downbeats.map((beat) => <span className="beat-marker downbeat" style={{ left: `${(beat / data.duration) * 100}%` }} key={`downbeat-${beat}`} />)}<span className="playhead" style={{ left: `${(time / data.duration) * 100}%` }} /></div>
            <div className="timeline">{variant.items.map((item, index) => <button className={`timeline-clip source-color-${sourceColors.get(item.path) ?? 0}${index === itemIndex ? ' active' : ''}`} style={{ width: `${((item.timeline_end - item.timeline_start) / data.duration) * 100}%` }} type="button" title={`${item.source_name} · ${item.timeline_start.toFixed(2)}s`} key={`${item.candidate_id}-${index}`} onClick={() => seekTo(item.timeline_start)} />)}</div>
          </div>
        </section>
        <aside className="variant-inspector">
          <h1 className="panel-title">Current version</h1>
          <h2>{variant.name}</h2>
          <p>{variant.description}</p>
          <div className="stat-grid"><div><span>Score</span><strong>{Math.round(variant.score * 100)}%</strong></div><div><span>Clips</span><strong>{variant.items.length}</strong></div><div><span>Duration</span><strong>{formatTime(data.duration)}</strong></div><div><span>Current clip</span><strong>{itemIndex + 1}</strong></div></div>
          <div className="section-list">{data.sections.map((section) => <div className="section-row" key={`${section.start}-${section.label}`}><span className="section-dot" /><span>{section.label}</span><span>{formatTime(section.start)}</span></div>)}</div>
          {error ? <span className="error-text">{error}</span> : null}
        </aside>
      </div>
      <audio ref={audioRef} src={data.track_url} preload="metadata" />
    </main>
  )
}

function formatTime(value: number) {
  const total = Math.max(0, Math.floor(value))
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function buildSourceColorMap(items: Array<{ path: string }>) {
  const colors = new Map<string, number>()
  for (const item of items) {
    if (!colors.has(item.path)) colors.set(item.path, colors.size % 6)
  }
  return colors
}

async function playAudioFrom(audio: HTMLAudioElement, start: number) {
  if (audio.readyState < 1) {
    audio.load()
    await ensureMetadata(audio)
  }
  if (Number.isFinite(start) && Math.abs(audio.currentTime - start) > 0.05) {
    await seekMedia(audio, start)
  }
  await audio.play()
}

function ensureMetadata(media: HTMLMediaElement) {
  if (media.readyState >= 1) return Promise.resolve()
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      media.removeEventListener('loadedmetadata', handleLoaded)
      media.removeEventListener('error', handleError)
    }
    const handleLoaded = () => { cleanup(); resolve() }
    const handleError = () => { cleanup(); reject(new Error('Media metadata failed')) }
    media.addEventListener('loadedmetadata', handleLoaded, { once: true })
    media.addEventListener('error', handleError, { once: true })
  })
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
