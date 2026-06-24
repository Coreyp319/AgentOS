import { useState } from 'react'
import { useQueue, useQueueRetry, useQueueDismiss, useQueueApprove } from './api'
import type { QueueHeld, QueueReview } from './api'

// ADR-0019 G-panel: the durable "reviewable request queue" made visible. A Create-from-image request
// that couldn't run when it arrived (GPU busy / coordinator down / ComfyUI cold) is HELD and retried —
// or escalated to "needs your okay" — never silently dropped. This surface lets the human SEE the held
// requests and dispose them (try-now / cancel / make-it / dismiss). Calm by construction: when nothing
// is waiting it renders nothing. No filesystem paths ever reach here — the server's board() strips them.

// one approximate, non-ticking relative line (these live on a minutes-to-hours scale)
function ago(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} hr ago`
  return `${Math.round(h / 24)} days ago`
}

// the spool's machine cause -> one honest, non-alarming line (the request is never lost; it waits)
function heldStatus(it: QueueHeld): string {
  const when = ago(it.age_s)
  const tries = it.attempts > 0 ? ` · tried ${it.attempts}×` : ''
  switch (it.last_error) {
    case 'gpu-busy':
      return `the graphics card was busy — it'll try again when free${tries} · ${when}`
    case 'preempted':
      return `paused for your live work — it'll resume on its own${tries} · ${when}`
    case 'no-snapshot':
      return `couldn't find the saved image — retrying${tries} · ${when}`
    case 'prompt-blocked':
      return `the safety check held this one — retrying${tries} · ${when}`
    case null:
    case undefined:
      return `waiting its turn · added ${when}`
    default:
      return `waiting to run again${tries} · ${when}`
  }
}

type Muts = {
  retry: ReturnType<typeof useQueueRetry>
  dismiss: ReturnType<typeof useQueueDismiss>
  approve: ReturnType<typeof useQueueApprove>
}

// disable an item's actions only while ITS OWN action is in flight (mutations are shared across rows)
function pendingFor(m: Muts, id: string): boolean {
  return (
    (m.retry.isPending && m.retry.variables === id) ||
    (m.dismiss.isPending && m.dismiss.variables === id) ||
    (m.approve.isPending && m.approve.variables === id)
  )
}

function ReviewItem({ it, m }: { it: QueueReview; m: Muts }) {
  const [msg, setMsg] = useState<string | null>(null)
  const busy = pendingFor(m, it.id)
  async function onApprove() {
    setMsg(null)
    const j = await m.approve.mutateAsync(it.id)
    // possible-minor (and the like) are provably unapprovable — the server returns ok:false and never
    // makes it. Say so honestly rather than pretend the click did something.
    if (j && j.ok === false) setMsg("This one can't be approved — it stays blocked.")
  }
  return (
    <div className="qitem consent">
      <div className="qmain">
        <div className="qtitle">{it.title}</div>
        <div className="note">
          Lucid couldn't confirm this on its own — it needs your okay · {ago(Date.now() / 1000 - it.since)}
        </div>
        {msg && <div className="banner bad" role="status">{msg}</div>}
      </div>
      <div className="row">
        <button className="beat warm" disabled={busy} onClick={onApprove}>Make it</button>
        <button className="ghost" disabled={busy} onClick={() => m.dismiss.mutate(it.id)}>Dismiss</button>
      </div>
    </div>
  )
}

function HeldItem({ it, m }: { it: QueueHeld; m: Muts }) {
  const busy = pendingFor(m, it.id)
  return (
    <div className="qitem">
      <div className="qmain">
        <div className="qtitle">{it.title}</div>
        <div className="note">{heldStatus(it)}</div>
      </div>
      <div className="row">
        <button className="ghost" disabled={busy} onClick={() => m.retry.mutate(it.id)}>Try now</button>
        <button className="ghost" disabled={busy} onClick={() => m.dismiss.mutate(it.id)}>Cancel</button>
      </div>
    </div>
  )
}

export default function QueuePanel() {
  const { data } = useQueue()
  const m: Muts = { retry: useQueueRetry(), dismiss: useQueueDismiss(), approve: useQueueApprove() }
  const held = data?.held ?? []
  const review = data?.needs_review ?? []
  if (held.length === 0 && review.length === 0) return null // calm: nothing waiting -> nothing shown

  const summary = [
    review.length ? `${review.length} need${review.length === 1 ? 's' : ''} your okay` : '',
    held.length ? `${held.length} waiting to be made` : '',
  ].filter(Boolean).join(', ')

  return (
    <section className="card queue" aria-label="Create requests waiting">
      <div className="sr" role="status" aria-live="polite">{summary}</div>
      {review.length > 0 && (
        <div className="queue-group">
          <div className="queue-head">Needs your okay</div>
          {review.map((it) => <ReviewItem key={it.id} it={it} m={m} />)}
        </div>
      )}
      {held.length > 0 && (
        <div className="queue-group">
          <div className="queue-head">Waiting its turn</div>
          {held.map((it) => <HeldItem key={it.id} it={it} m={m} />)}
        </div>
      )}
    </section>
  )
}
