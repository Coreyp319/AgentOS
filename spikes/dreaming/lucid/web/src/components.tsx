import { useState } from 'react'
import { useBurn, useDelete, useSetEngine } from './api'
import type { Readiness, Engine } from './api'

// ADR-0023: pick the i2v backend that animates each beat. 'wan' = Wan 2.2 GGUF (the default),
// '10eros' = LTX-2.3 / 10Eros. Switching drops the warm GPU lease so the next beat re-admits at the
// new engine's VRAM estimate (10Eros Q6 ~22 GB vs Wan ~17 GB).
const ENGINE_LABELS: Record<string, string> = { wan: 'Wan 2.2', '10eros': '10Eros · LTX-2.3' }
// plain-language gloss so the picker isn't two raw model IDs — the trade-off (look vs VRAM) in a phrase.
const ENGINE_GLOSS: Record<string, string> = { wan: 'Balanced · ~17 GB VRAM', '10eros': 'Sharper, heavier · ~22 GB VRAM' }

// An expert choice, not a hero control: a collapsed disclosure that shows the active engine at a glance
// and only expands to the picker when the user wants it. Recedes under the primary surface in both modes.
export function EngineToggle({ engine }: { engine?: Engine }) {
  const setEngine = useSetEngine()
  if (!engine || !engine.options?.length) return null
  const activeLabel = ENGINE_LABELS[engine.active] ?? engine.active
  const activeGloss = ENGINE_GLOSS[engine.active]
  return (
    <details className="card disc">
      <summary className="disc-sum">
        <span className="disc-k">Dream engine</span>
        <span className="disc-v">{activeLabel}{activeGloss ? ` · ${activeGloss}` : ''}</span>
        <span className="disc-caret" aria-hidden="true">▾</span>
      </summary>
      <div className="disc-body">
        <div className="row" role="group" aria-label="Dream engine">
          {engine.options.map((opt) => (
            <button
              key={opt}
              className={'beat' + (opt === engine.active ? '' : ' ghost')}
              disabled={setEngine.isPending}
              aria-pressed={opt === engine.active}
              onClick={() => { if (opt !== engine.active) setEngine.mutate(opt) }}
            >
              <b>{ENGINE_LABELS[opt] ?? opt}</b>
              {ENGINE_GLOSS[opt] && <small>{ENGINE_GLOSS[opt]}</small>}
            </button>
          ))}
        </div>
        <div className="note" style={{ marginTop: 8, opacity: 0.7 }}>
          Applies on the next beat — switching drops the warm GPU lease so it re-sizes safely.
        </div>
      </div>
    </details>
  )
}

// Ambient readiness: one quiet word by the wordmark when all is well, so the noisy three-dot breakdown
// (ReadinessCard) only appears when a piece is actually down. Healthy = calm; paused = the card explains.
export function ReadyChip({ r }: { r: Readiness }) {
  const ok = r.can_dream
  return (
    <span className="ready-chip">
      <span className={'dot ' + (ok ? 'ok' : 'paused')} aria-hidden="true" />
      <span>{ok ? 'ready' : 'paused'}</span>
      <span className="sr">{ok ? 'Lucid is ready to dream' : 'Lucid is paused — a piece is missing; see below'}</span>
    </span>
  )
}

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
