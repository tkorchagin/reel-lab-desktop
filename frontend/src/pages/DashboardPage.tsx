import { ArrowRight, Check, Clock3, Film, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { defaultProjectName } from '../projectName'
import type { Project } from '../types'

const statusLabels: Record<string, string> = {
  draft: 'Media',
  processing: 'Processing',
  ready: 'Results',
  preview: 'Results ready',
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draftName, setDraftName] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.projects().then(setProjects).finally(() => setLoading(false))
  }, [])

  async function createProject() {
    const project = await api.createProject(defaultProjectName())
    navigate(`/projects/${project.id}/media`)
  }

  function openProject(project: Project) {
    const section = project.status === 'ready' || project.status === 'preview' ? 'results' : 'media'
    navigate(`/projects/${project.id}/${section}`)
  }

  function startRename(project: Project) {
    setEditingId(project.id)
    setDraftName(project.name)
    setError('')
  }

  async function renameProject(event: FormEvent<HTMLFormElement>, project: Project) {
    event.preventDefault()
    event.stopPropagation()
    const name = draftName.trim()
    if (!name) return
    if (name === project.name) {
      setEditingId(null)
      return
    }
    setBusyId(project.id)
    setError('')
    try {
      const updated = await api.updateProject(project.id, { name })
      setProjects((current) => current.map((item) => item.id === project.id ? { ...item, ...updated } : item))
      setEditingId(null)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  async function deleteProject() {
    if (!deleteTarget) return
    setBusyId(deleteTarget.id)
    setError('')
    try {
      await api.deleteProject(deleteTarget.id)
      setProjects((current) => current.filter((project) => project.id !== deleteTarget.id))
      setDeleteTarget(null)
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <main className="page dashboard-page">
      <div className="page-title-row">
        <div>
          <span className="eyebrow">Workspace</span>
          <h1>Projects</h1>
        </div>
        <button className="primary" type="button" onClick={createProject}>
          <Plus size={18} />
          New reel
        </button>
      </div>
      {error ? <div className="dashboard-error error-text">{error}</div> : null}
      {loading ? <div className="empty-state">Loading projects…</div> : null}
      {!loading && projects.length === 0 ? (
        <div className="empty-state actionable">
          <Film size={30} />
          <strong>No projects yet</strong>
          <button className="primary" type="button" onClick={createProject}>
            <Plus size={18} />
            Create your first reel
          </button>
        </div>
      ) : null}
      <div className="project-list">
        {projects.map((project) => (
          <article
            className="project-row"
            key={project.id}
            role="link"
            tabIndex={editingId === project.id ? -1 : 0}
            aria-label={`Open project ${project.name}`}
            onClick={() => { if (editingId !== project.id) openProject(project) }}
            onKeyDown={(event) => {
              if (editingId !== project.id && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault()
                openProject(project)
              }
            }}
          >
            <span className="project-icon"><Film size={19} /></span>
            {editingId === project.id ? (
              <form className="project-rename" onClick={(event) => event.stopPropagation()} onSubmit={(event) => renameProject(event, project)}>
                <input autoFocus maxLength={160} value={draftName} onChange={(event) => setDraftName(event.target.value)} aria-label="Project name" />
                <button className="icon-button compact-icon" type="submit" title="Save name" disabled={!draftName.trim() || busyId === project.id}><Check size={15} /></button>
                <button className="icon-button compact-icon" type="button" title="Cancel rename" onClick={() => setEditingId(null)}><X size={15} /></button>
              </form>
            ) : (
              <span className="project-copy">
                <strong>{project.name}</strong>
                <span>{project.asset_count || 0} files · {project.duration === 0 ? 'max' : `${project.duration} seconds`}</span>
              </span>
            )}
            <span className={`status-badge ${project.status}`}>{statusLabels[project.status] || project.status}</span>
            <span className="updated"><Clock3 size={14} />{new Date(project.updated_at).toLocaleDateString('en-US')}</span>
            <span className="project-actions">
              {editingId !== project.id ? <button className="icon-button compact-icon" type="button" title="Rename project" onClick={(event) => { event.stopPropagation(); startRename(project) }}><Pencil size={14} /></button> : null}
              {editingId !== project.id ? <button className="icon-button compact-icon danger-icon" type="button" title="Delete project" onClick={(event) => { event.stopPropagation(); setDeleteTarget(project) }}><Trash2 size={14} /></button> : null}
              <ArrowRight className="project-arrow" size={18} />
            </span>
          </article>
        ))}
      </div>
      {deleteTarget ? (
        <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && busyId !== deleteTarget.id) setDeleteTarget(null) }}>
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-project-title">
            <div>
              <span className="eyebrow">Delete project</span>
              <h2 id="delete-project-title">Delete “{deleteTarget.name}”?</h2>
              <p>The project, its versions, and exports will be deleted. Original photos and videos stay in the library.</p>
            </div>
            <div className="dialog-actions">
              <button className="secondary" type="button" disabled={busyId === deleteTarget.id} onClick={() => setDeleteTarget(null)}>Cancel</button>
              <button className="secondary danger-button" type="button" disabled={busyId === deleteTarget.id} onClick={deleteProject}><Trash2 size={16} />{busyId === deleteTarget.id ? 'Deleting…' : 'Delete project'}</button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  )
}
