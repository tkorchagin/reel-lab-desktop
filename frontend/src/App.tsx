import { FolderOpen, Images, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api } from './api'
import DashboardPage from './pages/DashboardPage'
import LibraryPage from './pages/LibraryPage'
import MediaPage from './pages/MediaPage'
import PreviewPage from './pages/PreviewPage'
import SettingsPage from './pages/SettingsPage'
import { defaultProjectName } from './projectName'

function AppHeader() {
  const location = useLocation()
  const navigate = useNavigate()
  const [userLabel, setUserLabel] = useState('Local')

  useEffect(() => {
    api.session().then((session) => setUserLabel(session.label)).catch(() => {})
  }, [])

  async function createProject() {
    const project = await api.createProject(defaultProjectName())
    navigate(`/projects/${project.id}/media`)
  }

  return (
    <header className="app-header">
      <Link className="brand" to="/">
        <strong>REEL LAB</strong>
        <span>local video editing</span>
      </Link>
      <nav className="header-nav" aria-label="Main navigation">
        <Link className={location.pathname === '/' ? 'active' : ''} to="/" title="Projects">
          <FolderOpen size={17} />
          <span>Projects</span>
        </Link>
        <Link className={location.pathname === '/library' ? 'active' : ''} to="/library" title="Media library">
          <Images size={17} />
          <span>Media library</span>
        </Link>
      </nav>
      <div className="header-actions">
        <span className="user-label">{userLabel}</span>
        <button className="primary compact" type="button" onClick={createProject}>
          <Plus size={17} />
          New reel
        </button>
      </div>
    </header>
  )
}

export default function App() {
  const location = useLocation()
  const previewMode = location.pathname.endsWith('/preview')
  const settingsMode = location.pathname.endsWith('/results') || location.pathname.endsWith('/settings')
  const projectMode = location.pathname.startsWith('/projects/') && !previewMode
  const libraryPickerMode = location.pathname === '/library' && new URLSearchParams(location.search).has('project')
  const contextMode = projectMode || libraryPickerMode
  return (
    <div className={`app-shell${previewMode ? ' preview-mode' : ''}${settingsMode ? ' settings-mode' : ''}${contextMode ? ' context-mode' : ''}`}>
      {!previewMode && !contextMode ? <AppHeader /> : null}
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/projects/:projectId/media" element={<MediaPage />} />
        <Route path="/projects/:projectId/results" element={<SettingsPage />} />
        <Route path="/projects/:projectId/settings" element={<SettingsRedirect />} />
        <Route path="/projects/:projectId/preview" element={<PreviewPage />} />
      </Routes>
    </div>
  )
}

function SettingsRedirect() {
  const { projectId = '' } = useParams()
  return <Navigate replace to={`/projects/${projectId}/results`} />
}
