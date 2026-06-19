import { useState } from 'react'
import { useBurn, useDelete } from './api'
import type { Readiness } from './api'

export function ReadinessCard({ r }: { r: Readiness }) {
  const Dot = ({ on, name }: { on: boolean; name: string }) => (
    <span className="item">
      <span className={'dot ' + (on ? 'on' : 'off')} aria-hidden="true" />
      <span>{name}</span>
      <span className="sr">{on ? 'ready' : 'not responding'}</span>
    </span>
  )
  return (
    <div className="card">
      <div className="ready">
        <Dot on={r.coordinator} name="Graphics turn-taking" />
        <Dot on={r.comfyui} name="Video generator" />
        <Dot on={r.ollama} name="Story suggestions" />
      </div>
      {!r.can_dream && (
        <div className="banner">
          Can't dream right now — {r.why.join('; ')}. When a piece is missing, Lucid steps back and leaves your wallpaper untouched.
          <div className="note" style={{ marginTop: 4 }}>Lucid keeps checking — this turns back on by itself when the piece is back.</div>
        </div>
      )}
    </div>
  )
}

type Outcome = { ok: boolean; text: string } | null

function DangerTwoStep({ label, confirmLabel, onConfirm, busy, outcome }: {
  label: string; confirmLabel: string; onConfirm: () => void; busy?: boolean; outcome: Outcome
}) {
  const [armed, setArmed] = useState(false)
  return (
    <>
      {outcome && <div className={'banner ' + (outcome.ok ? 'good' : 'bad')}>{outcome.text}</div>}
      {armed ? (
        <div className="row">
          <button className="beat danger" disabled={busy} onClick={() => { setArmed(false); onConfirm() }}>{confirmLabel}</button>
          <button className="ghost" onClick={() => setArmed(false)}>Cancel</button>
        </div>
      ) : (
        <button className="beat danger" style={{ marginTop: 10 }} onClick={() => setArmed(true)}>{label}</button>
      )}
    </>
  )
}

export function PrivateCard() {
  const burn = useBurn()
  const [outcome, setOutcome] = useState<Outcome>(null)
  async function doBurn() {
    const j = await burn.mutateAsync()
    setOutcome(j?.failed?.length
      ? { ok: false, text: `Some traces could NOT be wiped and remain on disk: ${j.failed.join('; ')}. Retried at next start; delete by hand to be certain.` }
      : { ok: true, text: `This dream is gone — ${j?.burned ?? 0} location(s) wiped.` })
  }
  return (
    <div className="card private">
      <span className="lock">🔒 Private session</span>
      <div className="note" style={{ marginTop: 4 }}>
        Kept in memory, not in your saved files. Never shown elsewhere, never set as wallpaper. Wiped when you log out — the one frame the renderer must write to disk is sealed and burned with it.
      </div>
      <DangerTwoStep label="🔥 Burn this dream now" confirmLabel="Burn permanently — this can't be undone" onConfirm={doBurn} busy={burn.isPending} outcome={outcome} />
    </div>
  )
}

export function LibraryCard() {
  const del = useDelete()
  const [outcome, setOutcome] = useState<Outcome>(null)
  async function doDelete() {
    const j = await del.mutateAsync()
    if (j?.failed?.length) setOutcome({ ok: false, text: `Some files could NOT be deleted: ${j.failed.join('; ')}. Delete by hand to be certain.` })
    // success -> chain becomes null -> this card unmounts
  }
  return (
    <div className="card">
      <div className="note">Saved on this computer (your dream library) — kept until you delete it.</div>
      <DangerTwoStep label="🗑 Delete this dream" confirmLabel="Delete permanently — this can't be undone" onConfirm={doDelete} busy={del.isPending} outcome={outcome} />
    </div>
  )
}
