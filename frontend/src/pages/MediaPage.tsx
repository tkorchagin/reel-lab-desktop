import { ArrowLeft, Film, FolderPlus, GripVertical, Image as ImageIcon, Images, LoaderCircle, Play, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, uploadProjectFile } from '../api'
import ProgressBar from '../components/ProgressBar'
import ProjectSteps from '../components/ProjectSteps'
import SessionTransferButton from '../components/SessionTransferButton'
import type { Job, MediaAsset, ProjectDetail } from '../types'

type PendingFile = {
  key: string
  file: File
  previewUrl: string
  mediaType: 'photo' | 'video'
  selected: boolean
  progress: number
  status: 'pending' | 'uploading' | 'ready' | 'error'
  serverAsset?: MediaAsset
  error?: string
}

type LocalPreview = {
  previewUrl: string
  mediaType: PendingFile['mediaType']
}

export default function MediaPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const pendingRef = useRef<PendingFile[]>([])
  const localPreviewsRef = useRef<Record<string, LocalPreview>>({})
  const videoRefs = useRef(new Map<string, HTMLVideoElement>())
  const focusedAssetRef = useRef<string | null>(null)
  const autoProcessKeyRef = useRef<string | null>(null)
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [pending, setPending] = useState<PendingFile[]>([])
  const [busy, setBusy] = useState(false)
  const [playingPreview, setPlayingPreview] = useState<string | null>(null)
  const [localPreviews, setLocalPreviews] = useState<Record<string, LocalPreview>>({})
  const [draggingPendingKey, setDraggingPendingKey] = useState<string | null>(null)
  const [dragOverPendingKey, setDragOverPendingKey] = useState<string | null>(null)
  const [draggingAssetId, setDraggingAssetId] = useState<string | null>(null)
  const [dragOverAssetId, setDragOverAssetId] = useState<string | null>(null)
  const [draggingFiles, setDraggingFiles] = useState(false)
  const [orderSaving, setOrderSaving] = useState(false)
  const [error, setError] = useState('')
  const [clock, setClock] = useState(0)
  const focusedAssetId = searchParams.get('asset')
  const returnToResults = searchParams.get('return') === 'results'

  function setProjectDetail(next: ProjectDetail) {
    setLocalPreviews((current) => {
      let changed = false
      const previews = { ...current }
      for (const asset of next.assets) {
        const localPreview = previews[asset.id]
        const hasServerPreview = asset.media_type === 'video'
          ? Boolean(asset.thumbnail_url)
          : Boolean(asset.proxy_url || asset.thumbnail_url)
        if (!localPreview || !hasServerPreview) continue
        URL.revokeObjectURL(localPreview.previewUrl)
        delete previews[asset.id]
        changed = true
      }
      return changed ? previews : current
    })
    setDetail(next)
  }

  async function refresh() {
    const next = await api.project(projectId)
    setProjectDetail(next)
    return next
  }

  useEffect(() => {
    api.project(projectId).then(async (loaded) => {
      let next = loaded
      if (next.project.processing_profile !== 'maximum') {
        await api.updateProject(projectId, { processing_profile: 'maximum' })
        next = await api.project(projectId)
      }
      setProjectDetail(next)
    }).catch((reason) => setError(reason.message))
  }, [projectId])
  useEffect(() => {
    if (folderInput.current) folderInput.current.setAttribute('webkitdirectory', '')
  }, [])
  useEffect(() => {
    pendingRef.current = pending
  }, [pending])
  useEffect(() => {
    localPreviewsRef.current = localPreviews
  }, [localPreviews])
  useEffect(() => () => {
    pendingRef.current.forEach((item) => URL.revokeObjectURL(item.previewUrl))
    Object.values(localPreviewsRef.current).forEach((item) => URL.revokeObjectURL(item.previewUrl))
  }, [])
  useEffect(() => {
    if (detail?.project.status !== 'processing') return
    const timer = window.setInterval(() => {
      api.project(projectId).then((next) => {
        setProjectDetail(next)
      }).catch(() => {})
    }, 700)
    return () => window.clearInterval(timer)
  }, [detail?.project.status, projectId])
  useEffect(() => {
    if (detail?.project.status !== 'processing') return
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [detail?.project.status])
  useEffect(() => {
    if (!focusedAssetId || focusedAssetRef.current === focusedAssetId || !detail?.assets.some((asset) => asset.id === focusedAssetId)) return
    const frame = window.requestAnimationFrame(() => {
      const element = document.getElementById(`asset-${focusedAssetId}`)
      if (!element) return
      element.scrollIntoView({ behavior: 'smooth', block: 'center' })
      element.focus({ preventScroll: true })
      focusedAssetRef.current = focusedAssetId
    })
    return () => window.cancelAnimationFrame(frame)
  }, [detail?.assets, focusedAssetId])
  useEffect(() => {
    if (!detail || pending.length || busy || detail.project.status !== 'draft') return
    const assetIds = detail.assets
      .filter((asset) => asset.selected && asset.analysis.status !== 'ready')
      .map((asset) => asset.id)
      .sort()
    if (!assetIds.length) return
    const key = `${projectId}:${assetIds.join(',')}`
    if (autoProcessKeyRef.current === key) return
    autoProcessKeyRef.current = key
    setBusy(true)
    setError('')
    api.processProject(projectId, 'maximum')
      .then(() => api.project(projectId))
      .then((next) => setProjectDetail(next))
      .catch((reason) => {
        setError((reason as Error).message)
      })
      .finally(() => setBusy(false))
  }, [busy, detail, pending.length, projectId])

  const selectedExisting = detail?.assets.filter((asset) => asset.selected) || []
  const allReady = selectedExisting.length > 0 && selectedExisting.every((asset) => asset.analysis.status === 'ready')
  const selectedCount = selectedExisting.length + pending.filter((item) => item.selected).length
  const totalCount = (detail?.assets.length || 0) + pending.length
  const overall = detail?.job?.progress || 0
  const processingItems = useMemo(() => new Map(detail?.job?.items.map((item) => [item.asset_id, item]) || []), [detail?.job])
  const isProcessing = detail?.project.status === 'processing'
  const selectedPending = pending.filter((item) => item.selected && item.status !== 'error')
  const rawUploadProgress = selectedPending.length
    ? Math.round(selectedPending.reduce((sum, item) => sum + uploadDisplayProgress(item), 0) / selectedPending.length)
    : 0
  const uploadProgress = selectedPending.some((item) => item.status !== 'ready')
    ? Math.min(99, rawUploadProgress)
    : rawUploadProgress
  const isWorking = busy || isProcessing
  const actionProgress = isProcessing ? overall : uploadProgress
  const uploadComplete = selectedPending.filter((item) => item.status === 'ready').length
  const actionStage = isProcessing
    ? detail?.job?.stage || 'Processing media'
    : selectedPending.some((item) => item.status !== 'ready')
      ? `Uploading ${uploadComplete} of ${selectedPending.length} · keep this page open`
      : 'Starting processing'
  const processingEta = useMemo(() => estimateProcessingEta(detail?.job || null, clock), [clock, detail?.job])

  function addFiles(files: FileList | null) {
    if (!files) return
    setPending((current) => {
      const existingKeys = new Set(current.map((item) => item.key))
      const additions = Array.from(files).sort(compareFilesByName).flatMap((file) => {
        if (!isSupportedMediaFile(file)) return []
        const key = `${file.name}-${file.size}-${file.lastModified}`
        if (existingKeys.has(key)) return []
        existingKeys.add(key)
        return [{
          key,
          file,
          previewUrl: URL.createObjectURL(file),
          mediaType: file.type.startsWith('video/') ? 'video' as const : 'photo' as const,
          selected: true,
          progress: 0,
          status: 'pending' as const,
        }]
      })
      return [...current, ...additions]
    })
  }

  function pickFiles(event: ChangeEvent<HTMLInputElement>) {
    addFiles(event.target.files)
    event.target.value = ''
  }

  function openFilePicker() {
    fileInput.current?.click()
  }

  function hasDraggedFiles(event: DragEvent<HTMLElement>) {
    return Array.from(event.dataTransfer.types).includes('Files')
  }

  function dragFilesOverGrid(event: DragEvent<HTMLElement>) {
    if (!hasDraggedFiles(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    setDraggingFiles(true)
  }

  function leaveFileDropZone(event: DragEvent<HTMLElement>) {
    const nextTarget = event.relatedTarget
    if (nextTarget instanceof Node && event.currentTarget.contains(nextTarget)) return
    setDraggingFiles(false)
  }

  function dropFilesOnGrid(event: DragEvent<HTMLElement>) {
    if (!hasDraggedFiles(event)) return
    event.preventDefault()
    setDraggingFiles(false)
    addFiles(event.dataTransfer.files)
  }

  function updatePending(key: string, values: Partial<PendingFile>) {
    setPending((current) => current.map((item) => item.key === key ? { ...item, ...values } : item))
  }

  async function uploadAndProcess() {
    setBusy(true)
    setError('')
    const queue = pending.filter((item) => item.selected && item.status !== 'ready')
    const uploadedKeys = new Set<string>()
    const uploadedAssets = new Map<string, MediaAsset>()
    let cursor = 0
    async function worker() {
      while (cursor < queue.length) {
        const item = queue[cursor++]
        updatePending(item.key, { status: 'uploading', progress: 0 })
        try {
          const serverAsset = await uploadProjectFile(projectId, item.file, (progress) => updatePending(item.key, { progress }))
          updatePending(item.key, { status: 'ready', progress: 100, serverAsset })
          uploadedAssets.set(item.key, serverAsset)
          setLocalPreviews((current) => ({
            ...current,
            [serverAsset.id]: { previewUrl: item.previewUrl, mediaType: item.mediaType },
          }))
          uploadedKeys.add(item.key)
        } catch (reason) {
          updatePending(item.key, { status: 'error', error: (reason as Error).message })
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(3, Math.max(1, queue.length)) }, () => worker()))
    const existingAssetIds = detail?.assets.map((asset) => asset.id) || []
    const uploadedAssetIds = pendingRef.current
      .filter((item) => uploadedKeys.has(item.key))
      .flatMap((item) => uploadedAssets.get(item.key)?.id || [])
    if (uploadedAssetIds.length) {
      await api.reorderAssets(projectId, [...new Set([...existingAssetIds, ...uploadedAssetIds])])
    }
    setPending((current) => current.filter((item) => {
      if (!uploadedKeys.has(item.key)) return true
      return false
    }))
    await refresh()
    try {
      await api.processProject(projectId, 'maximum')
      await refresh()
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function toggleAsset(asset: MediaAsset) {
    await api.selectAsset(projectId, asset.id, !asset.selected)
    await refresh()
  }

  async function removeAsset(asset: MediaAsset) {
    if (playingPreview === asset.id) setPlayingPreview(null)
    forgetLocalPreview(asset.id)
    await api.removeAsset(projectId, asset.id)
    await refresh()
  }

  function forgetLocalPreview(assetId: string) {
    setLocalPreviews((current) => {
      const localPreview = current[assetId]
      if (!localPreview) return current
      URL.revokeObjectURL(localPreview.previewUrl)
      const next = { ...current }
      delete next[assetId]
      return next
    })
  }

  function beginPendingDrag(event: DragEvent<HTMLSpanElement>, key: string) {
    if (isWorking) return
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('application/x-reel-lab-pending', key)
    event.dataTransfer.setData('text/plain', key)
    setDraggingPendingKey(key)
  }

  function dragOverPending(event: DragEvent<HTMLElement>, key: string) {
    if (!draggingPendingKey || draggingPendingKey === key || isWorking) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDragOverPendingKey(key)
  }

  function dropPending(event: DragEvent<HTMLElement>, targetKey: string) {
    event.preventDefault()
    const sourceKey = event.dataTransfer.getData('application/x-reel-lab-pending') || draggingPendingKey
    setDraggingPendingKey(null)
    setDragOverPendingKey(null)
    if (!sourceKey || sourceKey === targetKey || isWorking) return
    setPending((current) => {
      const sourceIndex = current.findIndex((item) => item.key === sourceKey)
      const targetIndex = current.findIndex((item) => item.key === targetKey)
      if (sourceIndex < 0 || targetIndex < 0) return current
      const reordered = [...current]
      const [moved] = reordered.splice(sourceIndex, 1)
      reordered.splice(targetIndex, 0, moved)
      return reordered
    })
  }

  function beginAssetDrag(event: DragEvent<HTMLSpanElement>, assetId: string) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('application/x-reel-lab-asset', assetId)
    event.dataTransfer.setData('text/plain', assetId)
    setDraggingAssetId(assetId)
  }

  function dragOverAsset(event: DragEvent<HTMLElement>, assetId: string) {
    if (!draggingAssetId || draggingAssetId === assetId) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setDragOverAssetId(assetId)
  }

  async function dropAsset(event: DragEvent<HTMLElement>, targetAssetId: string) {
    event.preventDefault()
    const sourceAssetId = event.dataTransfer.getData('application/x-reel-lab-asset') || draggingAssetId
    setDraggingAssetId(null)
    setDragOverAssetId(null)
    if (!sourceAssetId || sourceAssetId === targetAssetId || !detail) return

    const reordered = [...detail.assets]
    const sourceIndex = reordered.findIndex((asset) => asset.id === sourceAssetId)
    const targetIndex = reordered.findIndex((asset) => asset.id === targetAssetId)
    if (sourceIndex < 0 || targetIndex < 0) return
    const [moved] = reordered.splice(sourceIndex, 1)
    reordered.splice(targetIndex, 0, moved)
    const assets = reordered.map((asset, sortOrder) => ({ ...asset, sort_order: sortOrder }))
    setDetail((current) => current ? { ...current, assets } : current)
    setOrderSaving(true)
    setError('')
    try {
      await api.reorderAssets(projectId, assets.map((asset) => asset.id))
    } catch (reason) {
      setError((reason as Error).message)
      await refresh()
    } finally {
      setOrderSaving(false)
    }
  }

  function removePending(item: PendingFile) {
    if (playingPreview === item.key) setPlayingPreview(null)
    URL.revokeObjectURL(item.previewUrl)
    setPending((items) => items.filter((current) => current.key !== item.key))
  }

  function registerVideo(key: string, node: HTMLVideoElement | null) {
    if (node) videoRefs.current.set(key, node)
    else videoRefs.current.delete(key)
  }

  function startPreview(key: string) {
    videoRefs.current.forEach((video, currentKey) => {
      if (currentKey !== key) video.pause()
    })
    const video = videoRefs.current.get(key)
    if (video) {
      video.muted = false
      video.play().catch(() => setError('The browser could not play the video'))
    }
    setPlayingPreview(key)
  }

  async function selectAll(selected: boolean) {
    setPending((items) => items.map((item) => ({ ...item, selected })))
    const changed = detail?.assets.filter((asset) => asset.selected !== selected) || []
    await Promise.all(changed.map((asset) => api.selectAsset(projectId, asset.id, selected)))
    if (changed.length) await refresh()
  }

  async function sortByName() {
    setPending((items) => [...items].sort(comparePendingByName))
    if (!detail?.assets.length) return
    const assets = [...detail.assets]
      .sort(compareAssetsByName)
      .map((asset, sortOrder) => ({ ...asset, sort_order: sortOrder }))
    setDetail((current) => current ? { ...current, assets } : current)
    setOrderSaving(true)
    setError('')
    try {
      await api.reorderAssets(projectId, assets.map((asset) => asset.id))
    } catch (reason) {
      setError((reason as Error).message)
      await refresh()
    } finally {
      setOrderSaving(false)
    }
  }

  const canProcess = selectedCount > 0 && detail?.project.status !== 'processing'
  const resultsReady = allReady && !pending.some((item) => item.selected) && detail?.project.status !== 'processing'
  const needsProcessing = pending.some((item) => item.selected) || selectedExisting.some((asset) => asset.analysis.status !== 'ready')
  const showProcessAction = (canProcess && needsProcessing) || isWorking
  const showResultsAction = resultsReady && !showProcessAction

  return (
    <main className={`project-page${isProcessing ? ' processing-layout' : ''}`}>
      <div className="project-page-header results-header">
        <div className="project-title-group">
          <button className="icon-button" type="button" title={returnToResults ? 'Back to results' : 'Back to projects'} onClick={() => navigate(returnToResults ? `/projects/${projectId}/results` : '/')}><ArrowLeft size={18} /></button>
          <strong className="results-project-name">{detail?.project.name || 'Reel'}</strong>
        </div>
        <div className="project-header-actions">
          <SessionTransferButton />
          <ProjectSteps active={0} projectId={projectId} resultsEnabled={resultsReady} />
        </div>
      </div>
      <div className="media-toolbar">
        <div className="selection-summary"><strong>{selectedCount}</strong><span>selected of {totalCount}</span></div>
        <div className="row-actions">
          <label className="secondary file-button"><Plus size={17} />Add files<input ref={fileInput} type="file" multiple accept="image/*,video/*" onChange={pickFiles} /></label>
          <label className="secondary file-button"><FolderPlus size={17} />Add folder<input ref={folderInput} type="file" multiple accept="image/*,video/*" onChange={pickFiles} /></label>
          <button className="secondary" type="button" onClick={() => navigate(`/library?project=${projectId}`)}><Images size={17} />From library</button>
        </div>
      </div>
      <div className="bulk-row">
        <button className="text-button" type="button" onClick={() => selectAll(true)}>Select all</button>
        <button className="text-button" type="button" onClick={() => selectAll(false)}>Deselect all</button>
        <button className="text-button" type="button" disabled={isWorking || orderSaving || !totalCount} onClick={sortByName}>Sort by name</button>
        {orderSaving ? <span className="overall-progress">Saving order</span> : detail?.project.status === 'processing' ? <span className="overall-progress">{detail.job?.stage}<strong>{overall}%</strong></span> : null}
      </div>
      {detail?.project.status === 'processing' ? <ProgressBar value={overall} /> : null}
      <div
        className={`media-card-grid${draggingFiles ? ' file-drag-over' : ''}`}
        onDragEnter={dragFilesOverGrid}
        onDragOver={dragFilesOverGrid}
        onDragLeave={leaveFileDropZone}
        onDrop={dropFilesOnGrid}
      >
        {pending.map((item) => {
          const itemWorking = busy && item.selected && item.status !== 'ready' && item.status !== 'error'
          const previewUrl = item.serverAsset?.proxy_url || item.serverAsset?.thumbnail_url || item.previewUrl
          const posterUrl = item.serverAsset?.thumbnail_url || undefined
          return (
          <article
            className={`media-card${item.selected ? '' : ' unselected'}${itemWorking ? ' processing' : ''}${draggingPendingKey === item.key ? ' dragging' : ''}${dragOverPendingKey === item.key ? ' drag-over' : ''}`}
            key={item.key}
            onDragOver={(event) => dragOverPending(event, item.key)}
            onDragLeave={() => setDragOverPendingKey((current) => current === item.key ? null : current)}
            onDrop={(event) => dropPending(event, item.key)}
          >
            <div className="media-card-preview">
              {item.mediaType === 'video' ? <video ref={(node) => registerVideo(item.key, node)} src={previewUrl} poster={posterUrl} controls={playingPreview === item.key} muted={playingPreview !== item.key} playsInline preload="auto" onEnded={() => setPlayingPreview(null)} /> : <img src={previewUrl} alt="" />}
              {item.mediaType === 'video' && playingPreview !== item.key ? <button className="media-play-button" type="button" title={`Play ${item.file.name}`} onClick={() => startPreview(item.key)}><Play size={20} fill="currentColor" /></button> : null}
              {itemWorking ? <span className="media-processing-indicator" role="status" aria-label="Processing file"><LoaderCircle size={24} /></span> : null}
              <label className="media-select" title={item.selected ? 'Exclude media' : 'Select media'}><input type="checkbox" checked={item.selected} onChange={() => updatePending(item.key, { selected: !item.selected })} aria-label={`Select ${item.file.name}`} /></label>
              <span className="media-drag-handle" draggable={!isWorking} role="button" tabIndex={0} title="Drag to reorder" aria-label={`Reorder ${item.file.name}`} aria-disabled={isWorking} onDragStart={(event) => beginPendingDrag(event, item.key)} onDragEnd={() => { setDraggingPendingKey(null); setDragOverPendingKey(null) }}><GripVertical size={17} /></span>
              <button className="icon-button media-remove" type="button" title="Remove from selection" onClick={() => removePending(item)}><Trash2 size={16} /></button>
            </div>
            <div className="media-card-copy"><div className="media-card-title">{item.mediaType === 'video' ? <Film size={14} /> : <ImageIcon size={14} />}<strong>{item.file.name}</strong></div><span>{formatBytes(item.file.size)} · {pendingFileStage(item)}</span><ProgressBar value={uploadDisplayProgress(item)} error={item.status === 'error'} /></div>
          </article>
          )
        })}
        {detail?.assets.map((asset) => {
          const item = processingItems.get(asset.id)
          const progress = item?.progress ?? asset.analysis.progress
          const stage = item?.stage ?? asset.analysis.stage
          const itemWorking = isProcessing && asset.selected && Boolean(item) && item?.status !== 'ready' && item?.status !== 'error'
          const eta = processingEta.items.get(asset.id)
          const stageWithEta = eta ? `${stage} · ≈ ${formatEta(eta)}` : stage
          const localPreview = localPreviews[asset.id]
          const localVideoUrl = localPreview?.mediaType === 'video' ? localPreview.previewUrl : undefined
          const videoUrl = asset.thumbnail_url
            ? asset.proxy_url || localVideoUrl || asset.source_url
            : localVideoUrl || asset.proxy_url || asset.source_url
          const posterUrl = asset.thumbnail_url || (localPreview?.mediaType === 'photo' ? localPreview.previewUrl : undefined) || undefined
          const imageUrl = asset.proxy_url || asset.thumbnail_url || (localPreview?.mediaType === 'photo' ? localPreview.previewUrl : undefined)
          return (
            <article
              id={`asset-${asset.id}`}
              className={`media-card${asset.selected ? '' : ' unselected'}${itemWorking ? ' processing' : ''}${draggingAssetId === asset.id ? ' dragging' : ''}${dragOverAssetId === asset.id ? ' drag-over' : ''}${focusedAssetId === asset.id ? ' focused' : ''}`}
              key={asset.id}
              tabIndex={focusedAssetId === asset.id ? -1 : undefined}
              onDragOver={(event) => dragOverAsset(event, asset.id)}
              onDragLeave={() => setDragOverAssetId((current) => current === asset.id ? null : current)}
              onDrop={(event) => dropAsset(event, asset.id)}
            >
              <div className="media-card-preview">
                {asset.media_type === 'video' && videoUrl ? <video ref={(node) => registerVideo(asset.id, node)} src={videoUrl} poster={posterUrl} controls={playingPreview === asset.id} muted={playingPreview !== asset.id} playsInline preload="metadata" onEnded={() => setPlayingPreview(null)} /> : asset.media_type === 'photo' && imageUrl ? <img src={imageUrl} alt="" /> : null}
                {asset.media_type === 'video' && videoUrl && playingPreview !== asset.id ? <button className="media-play-button" type="button" title={`Play ${asset.name}`} onClick={() => startPreview(asset.id)}><Play size={20} fill="currentColor" /></button> : null}
                {itemWorking ? <span className="media-processing-indicator" role="status" aria-label={`${asset.name} is processing`}><LoaderCircle size={24} /></span> : null}
                <label className="media-select" title={asset.selected ? 'Exclude media' : 'Select media'}><input type="checkbox" checked={asset.selected} onChange={() => toggleAsset(asset)} aria-label={`Select ${asset.name}`} /></label>
                <span className="media-drag-handle" draggable={!orderSaving} role="button" tabIndex={0} title="Drag to reorder" aria-label={`Reorder ${asset.name}`} aria-disabled={orderSaving} onDragStart={(event) => beginAssetDrag(event, asset.id)} onDragEnd={() => { setDraggingAssetId(null); setDragOverAssetId(null) }}><GripVertical size={17} /></span>
                <button className="icon-button media-remove" type="button" title="Remove from project" onClick={() => removeAsset(asset)}><Trash2 size={16} /></button>
              </div>
              <div className="media-card-copy"><div className="media-card-title">{asset.media_type === 'video' ? <Film size={14} /> : <ImageIcon size={14} />}<strong>{asset.name}</strong></div><span className={asset.analysis.status === 'error' ? 'error-text' : ''}>{formatBytes(asset.size)} · {stageWithEta}</span><ProgressBar value={progress} error={asset.analysis.status === 'error'} /></div>
            </article>
          )
        })}
        {!pending.length && !detail?.assets.length ? <button className="empty-state compact-empty media-drop-card" type="button" onClick={openFilePicker}><strong>Add photos and videos</strong><span>Click or drop files here</span></button> : null}
      </div>
      {showProcessAction ? <div className="processing-footer process-footer">
        {error ? <span className="error-text">{error}</span> : null}
        <button className={`primary process-button${isWorking ? ' active' : ''}`} type="button" disabled={isWorking} onClick={uploadAndProcess}>
          {isWorking ? <>
            <span className="process-button-progress" style={{ width: `${actionProgress}%` }} />
            <span className="process-button-copy"><span>{actionStage}</span><strong>{actionProgress}%{processingEta.total ? ` · ≈ ${formatEta(processingEta.total)}` : ''}</strong></span>
          </> : 'Process media'}
        </button>
      </div> : null}
      {showResultsAction ? <div className="processing-footer process-footer"><button className="primary" type="button" onClick={() => navigate(`/projects/${projectId}/results`)}>View results</button></div> : null}
    </main>
  )
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function isSupportedMediaFile(file: File) {
  return file.type.startsWith('image/') || file.type.startsWith('video/')
}

function compareFilesByName(left: File, right: File) {
  return compareNames(fileSortName(left), fileSortName(right))
}

function fileSortName(file: File) {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
}

function comparePendingByName(left: PendingFile, right: PendingFile) {
  return compareNames(fileSortName(left.file), fileSortName(right.file))
}

function compareAssetsByName(left: MediaAsset, right: MediaAsset) {
  return compareNames(left.name, right.name)
}

function compareNames(left: string, right: string) {
  return left.localeCompare(right, 'en-US', { numeric: true, sensitivity: 'base' })
}

function uploadDisplayProgress(item: PendingFile) {
  if (item.status === 'ready') return 100
  if (item.status === 'uploading') return Math.min(99, item.progress)
  return item.progress
}

function pendingFileStage(item: PendingFile) {
  if (item.status === 'pending') return 'Ready to upload'
  if (item.status === 'ready') return 'Uploaded'
  if (item.status === 'error') return item.error
  return item.progress >= 100 ? 'Saving on the server' : `Uploading ${item.progress}%`
}

function estimateProcessingEta(job: Job | null, now: number) {
  const items = new Map<string, number>()
  if (!job || job.status === 'ready' || !job.items.length) return { total: 0, items }
  const startedAt = Date.parse(job.created_at)
  const elapsed = (now - startedAt) / 1000
  const completedUnits = job.items.reduce((sum, item) => sum + Math.min(100, Math.max(0, item.progress)) / 100, 0)
  if (!Number.isFinite(elapsed) || elapsed < 8 || completedUnits < 0.08) return { total: 0, items }

  const secondsPerItem = elapsed / completedUnits
  let remaining = 0
  job.items.forEach((item) => {
    if (item.status === 'ready' || item.status === 'error') return
    remaining += Math.max(0, 1 - item.progress / 100) * secondsPerItem
    items.set(item.asset_id, remaining)
  })
  return { total: remaining, items }
}

function formatEta(seconds: number) {
  const minutes = Math.max(1, Math.round(seconds / 60))
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder ? `${hours} h ${remainder} min` : `${hours} h`
}
