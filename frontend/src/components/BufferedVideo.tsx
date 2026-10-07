import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import type { TimelineItem } from '../types'

type Props = {
  currentItem: TimelineItem | undefined
  nextItem: TimelineItem | undefined
  time: number
  playing: boolean
  objectPosition: string
}

type Slot = 0 | 1

export type BufferedVideoHandle = {
  waitUntilReady: () => Promise<void>
}

const BufferedVideo = forwardRef<BufferedVideoHandle, Props>(function BufferedVideo({ currentItem, nextItem, time, playing, objectPosition }, ref) {
  const videos = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null])
  const activeSlotRef = useRef<Slot | null>(null)
  const itemsRef = useRef({ currentItem, nextItem, time })
  itemsRef.current = { currentItem, nextItem, time }

  useImperativeHandle(ref, () => ({
    waitUntilReady: () => waitForPreparedVideos(videos.current, itemsRef.current),
  }), [])

  useEffect(() => {
    if (!currentItem || currentItem.kind !== 'video') {
      activeSlotRef.current = null
      showSlot(videos.current, null)
      videos.current.forEach((video) => video?.pause())
      if (nextItem?.kind === 'video' && videos.current[0]) {
        return prepareVideo(videos.current[0], nextItem, () => nextItem.source_start, false)
      }
      return
    }

    const currentKey = itemKey(currentItem)
    const preparedSlot = videos.current.findIndex((video) => video?.dataset.segment === currentKey)
    const slot = (preparedSlot >= 0 ? preparedSlot : activeSlotRef.current === 0 ? 1 : 0) as Slot
    activeSlotRef.current = slot
    const currentVideo = videos.current[slot]
    const standbySlot = (slot === 0 ? 1 : 0) as Slot
    const standbyVideo = videos.current[standbySlot]
    let cleanupStandby: (() => void) | undefined
    const showCurrent = () => {
      showSlot(videos.current, slot)
      // Keep the previous frame until its replacement is ready, then reuse it.
      if (!cleanupStandby && standbyVideo && nextItem?.kind === 'video') {
        cleanupStandby = prepareVideo(standbyVideo, nextItem, () => nextItem.source_start, false)
      } else if (nextItem?.kind !== 'video') {
        standbyVideo?.pause()
      }
    }
    let cleanupCurrent: (() => void) | undefined
    if (currentVideo) {
      cleanupCurrent = prepareVideo(
        currentVideo,
        currentItem,
        // A source may finish loading after the user has scrubbed again.
        () => currentItem.source_start + Math.max(0, itemsRef.current.time - currentItem.timeline_start),
        playing,
        showCurrent,
      )
    }
    return () => {
      cleanupCurrent?.()
      cleanupStandby?.()
    }
  }, [currentItem, nextItem, playing])

  useEffect(() => {
    const slot = activeSlotRef.current
    const video = slot === null ? null : videos.current[slot]
    if (!video || !currentItem || currentItem.kind !== 'video' || video.readyState < 1) return
    const requestedTime = currentItem.source_start + Math.max(0, time - currentItem.timeline_start)
    syncVideo(video, requestedTime, playing)
  }, [currentItem, time, playing])

  return (
    <>
      {([0, 1] as const).map((slot) => (
        <video
          className="preview-video-buffer"
          key={slot}
          muted
          playsInline
          preload="auto"
          ref={(video) => { videos.current[slot] = video }}
          style={{ objectPosition }}
        />
      ))}
    </>
  )
})

export default BufferedVideo

function prepareVideo(
  video: HTMLVideoElement,
  item: TimelineItem,
  requestedTime: () => number,
  shouldPlay: boolean,
  onReady?: () => void,
) {
  const key = itemKey(item)
  const segmentChanged = video.dataset.segment !== key
  video.dataset.segment = key
  if (segmentChanged) video.dataset.readySegment = ''
  if (!shouldPlay) video.pause()

  const markReady = () => {
    if (video.dataset.segment !== key || video.seeking || video.readyState < 2) return
    video.dataset.readySegment = key
    onReady?.()
    if (shouldPlay) video.play().catch(() => undefined)
  }
  const seek = () => {
    if (video.dataset.segment !== key) return
    const target = mediaTime(video, requestedTime())
    if (Math.abs(video.currentTime - target) <= 0.01) {
      markReady()
      return
    }
    video.dataset.readySegment = ''
    video.currentTime = target
  }

  video.addEventListener('loadedmetadata', seek)
  video.addEventListener('loadeddata', markReady)
  video.addEventListener('seeked', markReady)
  if (video.dataset.source !== item.asset_url) {
    video.pause()
    video.dataset.source = item.asset_url
    video.src = item.asset_url
    video.load()
  } else if (video.readyState >= 1) {
    seek()
  }
  return () => {
    video.removeEventListener('loadedmetadata', seek)
    video.removeEventListener('loadeddata', markReady)
    video.removeEventListener('seeked', markReady)
  }
}

function itemKey(item: TimelineItem) {
  return `${item.asset_url}|${item.candidate_id}|${item.source_start.toFixed(4)}`
}

function showSlot(
  videos: [HTMLVideoElement | null, HTMLVideoElement | null],
  activeSlot: Slot | null,
) {
  videos.forEach((video, slot) => video?.classList.toggle('active', slot === activeSlot))
}

function syncVideo(video: HTMLVideoElement, requestedTime: number, playing: boolean) {
  const target = mediaTime(video, requestedTime)
  if (Math.abs(video.currentTime - target) > (playing ? 0.32 : 0.01)) {
    video.dataset.readySegment = ''
    video.currentTime = target
  }
}

function mediaTime(video: HTMLVideoElement, requestedTime: number) {
  return video.duration > 0 ? requestedTime % video.duration : requestedTime
}

function waitForPreparedVideos(
  videos: [HTMLVideoElement | null, HTMLVideoElement | null],
  items: { currentItem: TimelineItem | undefined; nextItem: TimelineItem | undefined },
) {
  const deadline = performance.now() + 2500
  return new Promise<void>((resolve) => {
    const check = () => {
      const ready = [items.currentItem, items.nextItem].every((item) => {
        if (!item || item.kind !== 'video') return true
        const key = itemKey(item)
        return videos.some((video) => video?.dataset.readySegment === key && video.readyState >= 2)
      })
      if (ready || performance.now() >= deadline) {
        resolve()
        return
      }
      requestAnimationFrame(check)
    }
    check()
  })
}
