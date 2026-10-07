import { Check, Copy, LoaderCircle } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api'

type CopyState = 'idle' | 'copying' | 'copied' | 'error'

export default function SessionTransferButton() {
  const [state, setState] = useState<CopyState>('idle')

  async function copyLoginLink() {
    setState('copying')
    try {
      const nextPath = `${window.location.pathname}${window.location.search}${window.location.hash}`
      const transfer = await api.createSessionTransfer(nextPath)
      const url = new URL(transfer.url, window.location.origin).toString()
      await copyText(url)
      setState('copied')
      window.setTimeout(() => setState('idle'), 2400)
    } catch {
      setState('error')
      window.setTimeout(() => setState('idle'), 2400)
    }
  }

  const label = state === 'copied'
    ? 'Link copied'
    : state === 'error'
      ? 'Could not copy the link'
      : 'Open in another browser'

  return (
    <button
      className="secondary compact session-transfer-button"
      type="button"
      disabled={state === 'copying'}
      title="Copy a one-time login link for another browser"
      onClick={copyLoginLink}
    >
      {state === 'copying' ? <LoaderCircle className="spin" size={15} /> : state === 'copied' ? <Check size={15} /> : <Copy size={15} />}
      <span>{label}</span>
    </button>
  )
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // Fall back to the older API used by some embedded browsers.
    }
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('Clipboard is unavailable')
}
