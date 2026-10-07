import { ArrowLeft, CheckSquare, Film, Image as ImageIcon, Square } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { defaultProjectName } from '../projectName'
import type { MediaAsset } from '../types'

export default function LibraryPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const targetProject = searchParams.get('project')
  const [assets, setAssets] = useState<MediaAsset[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [attached, setAttached] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const readyCount = useMemo(() => assets.filter((asset) => asset.analysis.status === 'ready').length, [assets])

  useEffect(() => {
    const projectRequest = targetProject ? api.project(targetProject) : Promise.resolve(null)
    Promise.all([api.library(), projectRequest]).then(([libraryAssets, project]) => {
      setAssets(libraryAssets)
      setAttached(new Set(project?.assets.map((asset) => asset.id) || []))
    })
  }, [targetProject])

  function toggle(id: string) {
    if (attached.has(id)) return
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function useSelected() {
    if (!selected.size) return
    setBusy(true)
    const project = targetProject ? { id: targetProject } : await api.createProject(defaultProjectName())
    await api.attachAssets(project.id, [...selected])
    navigate(`/projects/${project.id}/media`)
  }

  return (
    <main className="page library-page">
      <div className="page-title-row">
        <div>
          <span className="eyebrow">{readyCount} processed</span>
          <h1>Media library</h1>
        </div>
        <div className="row-actions">
          {targetProject ? <button className="secondary" type="button" onClick={() => navigate(`/projects/${targetProject}/media`)}><ArrowLeft size={17} />Back</button> : null}
          <button className="primary" type="button" disabled={!selected.size || busy} onClick={useSelected}>
            <CheckSquare size={17} />
            {targetProject ? `Add ${selected.size}` : `New reel · ${selected.size}`}
          </button>
        </div>
      </div>
      <div className="library-toolbar">
        <span>Selected {selected.size} of {assets.length - attached.size}{attached.size ? ` · in project ${attached.size}` : ''}</span>
        <button className="text-button" type="button" onClick={() => setSelected(new Set(assets.filter((asset) => !attached.has(asset.id)).map((asset) => asset.id)))}>Select all</button>
        <button className="text-button" type="button" onClick={() => setSelected(new Set())}>Deselect all</button>
      </div>
      {assets.length === 0 ? <div className="empty-state">Media will appear here after your first project</div> : null}
      <div className="asset-grid">
        {assets.map((asset) => (
          <button className={`asset-card${selected.has(asset.id) ? ' selected' : ''}${attached.has(asset.id) ? ' attached' : ''}`} type="button" key={asset.id} disabled={attached.has(asset.id)} onClick={() => toggle(asset.id)}>
            <span className="asset-thumb">
              {asset.media_type === 'video' && asset.proxy_url ? <video src={asset.proxy_url} muted playsInline preload="metadata" /> : asset.proxy_url || asset.thumbnail_url ? <img src={asset.proxy_url || asset.thumbnail_url || ''} alt="" /> : asset.media_type === 'photo' ? <ImageIcon /> : <Film />}
              <span className="asset-check">{selected.has(asset.id) || attached.has(asset.id) ? <CheckSquare size={18} /> : <Square size={18} />}</span>
            </span>
            <span className="asset-card-copy">
              <strong>{asset.name}</strong>
              <span>{attached.has(asset.id) ? 'Already in the project' : asset.analysis.status === 'ready' ? 'Ready' : asset.analysis.stage}</span>
            </span>
          </button>
        ))}
      </div>
    </main>
  )
}
