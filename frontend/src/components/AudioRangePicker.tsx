import { GripVertical, RotateCcw, SkipForward } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

type EnergyPoint = { time: number; value: number }
type AudioSection = { start: number; end: number; label: string; energy: number }

type AudioRangePickerProps = {
  trackDuration: number
  selectionDuration: number
  start: number
  automatic: boolean
  energy: EnergyPoint[]
  sections: AudioSection[]
  onChange: (start: number) => void
  onReset: () => void
}

export default function AudioRangePicker({
  trackDuration,
  selectionDuration,
  start,
  automatic,
  energy,
  sections,
  onChange,
  onReset,
}: AudioRangePickerProps) {
  const trackRef = useRef<HTMLDivElement>(null)
  const pointerRef = useRef<number | null>(null)
  const dragOffsetRef = useRef(0)
  const [dragging, setDragging] = useState(false)
  const duration = Math.max(0.01, trackDuration)
  const windowDuration = Math.min(selectionDuration, duration)
  const maxStart = Math.max(0, duration - windowDuration)
  const safeStart = clamp(start, 0, maxStart)
  const safeEnd = safeStart + windowDuration
  const left = (safeStart / duration) * 100
  const width = (windowDuration / duration) * 100
  const bars = useMemo(
    () => energy.length ? energy : Array.from({ length: 48 }, (_, index) => ({ time: index, value: 0.12 })),
    [energy],
  )
  const jumpTargets = useMemo(
    () => sections
      .slice(1)
      .map((section) => clamp(section.start, 0, maxStart))
      .filter((value, index, values) => maxStart > 0 && values.indexOf(value) === index)
      .sort((left, right) => left - right),
    [maxStart, sections],
  )

  function timeAt(event: PointerEvent<HTMLDivElement>) {
    const bounds = trackRef.current?.getBoundingClientRect()
    if (!bounds?.width) return 0
    return clamp(((event.clientX - bounds.left) / bounds.width) * duration, 0, duration)
  }

  function updateFromPointer(event: PointerEvent<HTMLDivElement>) {
    if (pointerRef.current !== event.pointerId || maxStart <= 0) return
    const next = clamp(timeAt(event) - dragOffsetRef.current, 0, maxStart)
    onChange(roundToTenth(next))
  }

  function beginDrag(event: PointerEvent<HTMLDivElement>) {
    if (maxStart <= 0) return
    const pointerTime = timeAt(event)
    dragOffsetRef.current = pointerTime >= safeStart && pointerTime <= safeEnd
      ? pointerTime - safeStart
      : windowDuration / 2
    pointerRef.current = event.pointerId
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
    updateFromPointer(event)
  }

  function finishDrag(event: PointerEvent<HTMLDivElement>) {
    if (pointerRef.current !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    pointerRef.current = null
    setDragging(false)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (maxStart <= 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    if (event.key === 'Home') onChange(0)
    else if (event.key === 'End') onChange(roundToTenth(maxStart))
    else {
      const direction = event.key === 'ArrowLeft' ? -1 : 1
      const step = event.shiftKey ? 2 : 0.5
      onChange(roundToTenth(clamp(safeStart + direction * step, 0, maxStart)))
    }
  }

  function jumpToNextSection() {
    if (!jumpTargets.length) return
    const next = jumpTargets.find((value) => value > safeStart + 0.2) ?? jumpTargets[0]
    onChange(roundToTenth(next))
  }

  return (
    <div className="audio-range-picker">
      <div className="audio-range-heading">
        <div>
          <strong>Music section</strong>
          <span>{automatic ? 'Best section' : 'Selected manually'} · {formatClock(safeStart)}–{formatClock(safeEnd)}</span>
        </div>
        <div className="audio-range-actions">
          <button className="text-button" type="button" disabled={!jumpTargets.length} onClick={jumpToNextSection}>
            <SkipForward size={13} />Next section
          </button>
          <button className="text-button" type="button" disabled={automatic} onClick={onReset}>
            <RotateCcw size={13} />Best section
          </button>
        </div>
      </div>
      <div
        ref={trackRef}
        className={`audio-range-track${dragging ? ' dragging' : ''}${maxStart <= 0 ? ' fixed' : ''}`}
        role="slider"
        tabIndex={0}
        aria-label="Music section start"
        aria-valuemin={0}
        aria-valuemax={maxStart}
        aria-valuenow={safeStart}
        aria-valuetext={`${formatClock(safeStart)}–${formatClock(safeEnd)}`}
        onKeyDown={handleKeyDown}
        onPointerDown={beginDrag}
        onPointerMove={updateFromPointer}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
      >
        <div className="audio-range-bars" aria-hidden="true">
          {bars.map((point, index) => (
            <span
              className="audio-range-bar"
              style={{ height: `${Math.max(8, point.value * 100)}%` }}
              key={`${point.time}-${index}`}
            />
          ))}
        </div>
        {sections.slice(1).map((section) => (
          <span
            className="audio-range-section"
            style={{ left: `${clamp((section.start / duration) * 100, 0, 100)}%` }}
            title={section.label}
            key={`${section.start}-${section.label}`}
          />
        ))}
        <span className="audio-range-dim left" style={{ width: `${left}%` }} />
        <span className="audio-range-dim right" style={{ left: `${left + width}%` }} />
        <span className="audio-range-selection" style={{ left: `${left}%`, width: `${width}%` }}>
          {maxStart > 0 ? <GripVertical size={18} /> : null}
        </span>
      </div>
      <div className="audio-range-scale"><span>0:00</span><span>{formatClock(duration)}</span></div>
    </div>
  )
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value))
}

function roundToTenth(value: number) {
  return Math.round(value * 10) / 10
}

function formatClock(value: number) {
  const roundedValue = roundToTenth(Math.max(0, value))
  const minutes = Math.floor(roundedValue / 60)
  const seconds = roundedValue - minutes * 60
  const precise = !Number.isInteger(seconds)
  const formattedSeconds = precise
    ? seconds.toFixed(1).padStart(4, '0')
    : String(Math.round(seconds)).padStart(2, '0')
  return `${minutes}:${formattedSeconds}`
}
