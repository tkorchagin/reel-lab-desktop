import { Check } from 'lucide-react'
import { Link } from 'react-router-dom'

const steps = [
  { label: 'Media', path: 'media' },
  { label: 'Results', path: 'results' },
]

export default function ProjectSteps({ active, projectId, resultsEnabled = false }: { active: number; projectId: string; resultsEnabled?: boolean }) {
  return (
    <div className="project-steps" aria-label="Project steps">
      {steps.map((step, index) => {
        if (index < active) {
          return (
            <Link className="project-step complete" to={`/projects/${projectId}/${step.path}${step.path === 'media' ? '?return=results' : ''}`} key={step.label}>
              <span><Check size={14} /></span>
              {step.label}
            </Link>
          )
        }
        if (index > active && resultsEnabled) {
          return (
            <Link className="project-step available" to={`/projects/${projectId}/${step.path}`} key={step.label}>
              <span>{index + 1}</span>
              {step.label}
            </Link>
          )
        }
        return (
          <div className={`project-step${index === active ? ' active' : ''}`} key={step.label}>
            <span>{index + 1}</span>
            {step.label}
          </div>
        )
      })}
    </div>
  )
}
