type Props = {
  value: number
  error?: boolean
}

export default function ProgressBar({ value, error = false }: Props) {
  const progress = Math.max(0, Math.min(100, value))
  return (
    <div className={`progress-track${error ? ' error' : ''}`} role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ transform: `scaleX(${progress / 100})` }} />
    </div>
  )
}
