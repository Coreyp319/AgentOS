import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useBurn, useDelete, useRenameDream, useSetEngine, useStashSave } from './api'
import type { Readiness, Engine, StashStatus } from './api'

// ADR-0028: the app's section nav — a calm, wordmark-anchored pill row that replaces the ad-hoc
// showHome toggle / back-link / per-screen engine disclosure. Persistent across screens; the live
// dream (if any) leads as its own named item with an "up" dot. Stays in the instrument register
// (pill family, system voice, reserved-emoji vocab) so it never fights the cinematic stage below it.
export function Nav({ active, hasDream, dreamName, onNavigate }: {
  active: string; hasDream: boolean; dreamName?: string | null; onNavigate: (v: string) => void
}) {
  const items: { key: string; label: string; ic?: string; live?: boolean }[] = [
    ...(hasDream ? [{ key: 'dream', label: dreamName || 'Current dream', live: true }] : []),
    { key: 'new', label: 'New dream', ic: '✦' },
    { key: 'library', label: 'Dreams' },
    { key: 'stash', label: 'Stash', ic: '🔒' },
    { key: 'settings', label: 'Settings' },
  ]
  return (
    <nav className="nav" aria-label="Lucid sections">
      {items.map((it) => (
        <button key={it.key} className={'nav-item' + (active === it.key ? ' on' : '')}
          aria-current={active === it.key ? 'page' : undefined} onClick={() => onNavigate(it.key)}>
          {it.live && <span className="nav-dot" aria-hidden="true" />}
          {it.ic && <span className="nav-ic" aria-hidden="true">{it.ic}</span>}
          <span className="nav-lbl">{it.label}</span>
        </button>
      ))}
    </nav>
  )
}

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
  // a11y: when the confirm arms, the activated button unmounts — land focus on the SAFE default (Cancel)
  // so focus isn't dropped to <body> and an accidental Enter cancels rather than performs the destruction.
  const cancelRef = useRef<HTMLButtonElement>(null)
  useEffect(() => { if (armed) cancelRef.current?.focus() }, [armed])
  return (
    <>
      {outcome && <div className={'banner ' + (outcome.ok ? 'good' : 'bad')}>{outcome.text}</div>}
      {armed ? (
        <div className="row" role="group" aria-label={label}>
          <button className="beat danger" disabled={busy} onClick={() => { setArmed(false); onConfirm() }}>{confirmLabel}</button>
          <button ref={cancelRef} className="ghost" onClick={() => setArmed(false)}>Cancel</button>
        </div>
      ) : (
        <button className="beat danger" style={{ marginTop: 10 }} onClick={() => setArmed(true)}>{label}</button>
      )}
    </>
  )
}

export function PrivateCard({ stash, onGoStash }: { stash?: StashStatus; onGoStash?: () => void }) {
  const burn = useBurn()
  const save = useStashSave()
  const [outcome, setOutcome] = useState<Outcome>(null)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  async function doBurn() {
    const j = await burn.mutateAsync()
    setOutcome(j?.failed?.length
      ? { ok: false, text: `Some traces could NOT be wiped and remain on disk: ${j.failed.join('; ')}. Retried at next start; delete by hand to be certain.` }
      : { ok: true, text: `This dream is gone — ${j?.burned ?? 0} location(s) wiped.` })
  }
  async function doSave() {
    setSaveMsg(null)
    const j = await save.mutateAsync({})
    // plain text (no leading emoji): this rides an aria-live region, where a glyph is read out as its name.
    setSaveMsg(j?.ok ? 'Saved to your private stash — encrypted, reopen with your passphrase.' : (j?.error || 'Could not save.'))
  }
  return (
    <div className="card private">
      <span className="lock">🔒 Private session</span>
      <div className="note" style={{ marginTop: 4 }}>
        Kept in memory, not in your saved files. Never shown elsewhere, never set as wallpaper. Wiped when you log out — the one frame the renderer must write to disk is sealed and burned with it.
      </div>
      {/* ADR-0028: persist a private dream by SEALING it into the encrypted stash (not the open library) */}
      {stash?.unlocked ? (
        <>
          <button className="beat warm" style={{ marginTop: 10 }} disabled={save.isPending} onClick={doSave}>
            {stash.saved_id ? '🔒 Update in private stash' : '🔒 Save to private stash'}
          </button>
          {saveMsg && <div className="note" style={{ marginTop: 4 }} role="status" aria-live="polite">{saveMsg}</div>}
        </>
      ) : (
        <div style={{ marginTop: 8 }}>
          <div className="note" style={{ opacity: 0.8 }}>
            To keep this dream past logout, {stash?.exists ? 'unlock' : 'create'} your encrypted private stash, then return here to save it.
          </div>
          {/* a real control to the RIGHT place — the stash lives under the "Stash" section, not "Your dreams" */}
          <button className="ghost" style={{ marginTop: 8 }} onClick={() => onGoStash?.()}>
            {stash?.exists ? 'Unlock your stash' : 'Create your stash'} →
          </button>
        </div>
      )}
      <DangerTwoStep label="🔥 Burn this dream now" confirmLabel="Burn permanently — this can't be undone" onConfirm={doBurn} busy={burn.isPending} outcome={outcome} />
    </div>
  )
}

export function LibraryCard({ name }: { name?: string | null }) {
  const del = useDelete()
  const rename = useRenameDream()
  const [outcome, setOutcome] = useState<Outcome>(null)
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (editing) { inputRef.current?.focus(); inputRef.current?.select() } }, [editing])
  async function doDelete() {
    const j = await del.mutateAsync()
    if (j?.failed?.length) setOutcome({ ok: false, text: `Some files could NOT be deleted: ${j.failed.join('; ')}. Delete by hand to be certain.` })
    // success -> chain becomes null -> this card unmounts
  }
  async function doRename(e: FormEvent) {
    e.preventDefault()
    const next = val.trim()
    setEditing(false)
    if (next && next !== (name || '')) await rename.mutateAsync({ name: next })
  }
  return (
    <div className="card">
      {editing ? (
        <form className="row" style={{ marginTop: 0 }} onSubmit={doRename}>
          <input ref={inputRef} type="text" aria-label="Rename this dream" maxLength={80}
            value={val} onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') setEditing(false) }}
            style={{ flex: '1 1 200px', marginTop: 0 }} />
          <button type="submit" className="beat" style={{ width: 'auto', margin: 0 }} disabled={rename.isPending}>Save</button>
          <button type="button" className="ghost" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <div className="note" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span>Saved on this computer (your dream library) — kept until you delete it.</span>
          <button className="ghost" style={{ padding: '4px 12px' }} onClick={() => { setVal(name || ''); setEditing(true) }}>Rename</button>
        </div>
      )}
      <DangerTwoStep label="🗑 Delete this dream" confirmLabel="Delete permanently — this can't be undone" onConfirm={doDelete} busy={del.isPending} outcome={outcome} />
    </div>
  )
}
