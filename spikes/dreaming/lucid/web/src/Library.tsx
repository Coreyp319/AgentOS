import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, FormEvent } from 'react'
import {
  useLibrary, useOpenDream, useDelete, useRenameDream, useStash, useStashInit, useStashUnlock,
  useStashLock, useStashOpen, useStashDelete, useStashRename, useStashPassphrase, thumbUrl,
} from './api'
import type { StashStatus } from './api'

// ADR-0028 — "Your dreams": reopen a saved dream, or unlock the encrypted private stash and reopen a
// private one. Each dream is a cinematic 9:16 frame (the player's material), not a generic card; the
// stash is the warm, sealed sibling. Opening either switches the current dream — `onOpened` drops the
// parent back to the dream view.

function ago(ts?: number | null): string {
  if (!ts) return ''
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 90) return 'just now'
  const m = s / 60
  if (m < 90) return `${Math.round(m)} min ago`
  const h = m / 60
  if (h < 36) return `${Math.round(h)} h ago`
  return `${Math.round(h / 24)} d ago`
}

// one frame-tile, used for both saved (with thumbnail) and sealed (encrypted, no thumbnail) dreams.
// `onRename`, when present, adds an inline rename (the caption flips to a small editor).
function Tile({ name, frames, when, thumb, sealed, i, busy, onOpen, onDelete, onRename }: {
  name: string; frames: number; when?: string; thumb?: string; sealed?: boolean; i: number
  busy?: boolean; onOpen: () => void; onDelete: () => void; onRename?: (next: string) => void
}) {
  const [broken, setBroken] = useState(false)
  const [armed, setArmed] = useState(false)
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(name)
  // a11y: when the two-step arms, the button the user activated unmounts — move focus to the SAFE
  // default (Keep) so focus isn't dropped to <body> and an accidental Enter cancels, never deletes.
  const keepRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (armed) keepRef.current?.focus() }, [armed])
  useEffect(() => { if (editing) { setVal(name); inputRef.current?.focus(); inputRef.current?.select() } }, [editing, name])
  const showImg = !sealed && !!thumb && !broken
  const thumbCls = 'lib-thumb' + (!sealed && !showImg ? ' blank' : '')
  function commitRename(e: FormEvent) {
    e.preventDefault()
    const next = val.trim()
    setEditing(false)
    if (next && next !== name) onRename?.(next)
  }
  // a11y: the tile is a plain container. OPEN is a real <button> overlay (named for SR); DELETE/RENAME are
  // sibling buttons ABOVE it — so we never nest interactive controls inside a button/role=button.
  return (
    <div className={'lib-tile' + (sealed ? ' sealed' : '') + (editing ? ' editing' : '')}
      style={{ '--d': `${Math.min(i, 9) * 0.045}s` } as CSSProperties}>
      <div className={thumbCls}>
        {showImg && <img src={thumb} alt="" loading="lazy" onError={() => setBroken(true)} />}
        {sealed && <span className="seal-mark" aria-hidden="true">🔒</span>}
      </div>
      <span className="lib-frames">{frames} {frames === 1 ? 'frame' : 'frames'}</span>
      {editing ? (
        <form className="lib-cap lib-rename" onSubmit={commitRename}>
          <input ref={inputRef} type="text" aria-label={`Rename ${name}`} maxLength={80} value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); setEditing(false) } }} />
          <div className="lib-rename-row">
            <button type="submit" disabled={busy}>Save</button>
            <button type="button" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </form>
      ) : (
        <div className="lib-cap">
          <div className="lib-name" title={name}>{name}</div>
          {when && <div className="lib-sub">{when}</div>}
        </div>
      )}
      {!editing && (
        <button className="lib-open" disabled={busy}
          aria-label={`Open ${name} — ${frames} ${frames === 1 ? 'frame' : 'frames'}`}
          onClick={onOpen} />
      )}
      {!editing && (
        <div className="lib-del">
          {armed ? (
            <>
              <button className="danger" disabled={busy} onClick={() => { setArmed(false); onDelete() }}>Delete</button>
              <button ref={keepRef} onClick={() => setArmed(false)} aria-label={`Keep ${name}`}>Keep</button>
            </>
          ) : (
            <>
              {onRename && <button onClick={() => setEditing(true)} aria-label={`Rename ${name}`}>Rename</button>}
              <button onClick={() => setArmed(true)} aria-label={`Remove ${name}`}>Remove</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// change the stash passphrase — a collapsed, expert action below the sealed grid (re-keys every sealed
// dream; the backend is crash-atomic). Reuses the instrument input/button register; no new chrome.
function ChangePassphrase() {
  const change = useStashPassphrase()
  const [open, setOpen] = useState(false)
  const [cur, setCur] = useState('')
  const [nw, setNw] = useState('')
  const [nw2, setNw2] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setErr(null); setDone(null)
    if (nw.length < 4) { setErr('Use at least 4 characters for the new passphrase.'); return }
    if (nw !== nw2) { setErr('The two new passphrases don’t match.'); return }
    if (nw === cur) { setErr('The new passphrase is the same as the current one.'); return }
    try {
      const j = await change.mutateAsync({ old: cur, new: nw })
      if (!j?.ok) { setErr(j?.error || 'That current passphrase is wrong.'); return }  // keep the new fields — only `cur` was wrong
      setCur(''); setNw(''); setNw2(''); setOpen(false)
      setDone('Passphrase changed — your private dreams are re-sealed under the new one.')
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }

  if (!open) {
    return (
      <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--hairline)' }}>
        {done && <div className="banner good" role="status" style={{ marginBottom: 8 }}>{done}</div>}
        <button className="stash-lock" onClick={() => { setDone(null); setOpen(true) }}>Change passphrase</button>
      </div>
    )
  }
  return (
    <form onSubmit={submit} style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--hairline)' }}>
      <p className="note" id="rekey-warn">
        This re-seals your existing private dreams under a new passphrase — the current one will no longer
        open them, and there’s still no recovery if you forget the new one.
      </p>
      <div className="pp-form">
        <input type="password" aria-label="Current passphrase" aria-describedby="rekey-warn"
          placeholder="Current passphrase" autoComplete="current-password"
          aria-invalid={err ? true : undefined} value={cur} onChange={(e) => setCur(e.target.value)} />
        <input type="password" aria-label="New passphrase" aria-describedby="rekey-warn"
          placeholder="New passphrase" autoComplete="new-password"
          value={nw} onChange={(e) => setNw(e.target.value)} />
        <input type="password" aria-label="Repeat new passphrase" placeholder="Repeat new passphrase"
          autoComplete="new-password" value={nw2} onChange={(e) => setNw2(e.target.value)} />
        <button type="submit" className="beat warm" disabled={change.isPending}>
          {change.isPending ? 'Re-sealing…' : 'Change'}
        </button>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button type="button" className="ghost" onClick={() => { setOpen(false); setErr(null) }}>Cancel</button>
      </div>
      {change.isPending && <div className="note" role="status" style={{ marginTop: 6 }}>Re-sealing your dreams…</div>}
      {err && <div className="banner bad" role="alert" style={{ marginTop: 8 }}>{err}</div>}
    </form>
  )
}

// the stash section: create / unlock / browse the encrypted private dreams
export function StashPanel({ stash, onOpened }: { stash?: StashStatus; onOpened?: () => void }) {
  const q = useStash()
  const init = useStashInit()
  const unlock = useStashUnlock()
  const lock = useStashLock()
  const open = useStashOpen()
  const del = useStashDelete()
  const rename = useStashRename()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [lockArmed, setLockArmed] = useState(false)
  const [wantUnseal, setWantUnseal] = useState(false)   // a successful unlock is pending its gleam
  const [unsealing, setUnsealing] = useState(false)     // the one-shot gleam is playing on the sealed grid

  const exists = q.data?.exists ?? stash?.exists ?? false
  const unlocked = q.data?.unlocked ?? stash?.unlocked ?? false
  const dreams = q.data?.dreams ?? []
  const busy = open.isPending || del.isPending || rename.isPending
  // a stashed dream is the CURRENT dream -> locking will reseal AND burn the open working copy, then
  // drop you to a blank session. Gate Lock behind a confirm only in that case (the common case stays 1-click).
  const stashDreamOpen = !!stash?.saved_id

  // clear transient creds + any stale error whenever the lock state flips (no "wrong passphrase" carried
  // across a lock/unlock cycle), and disarm the Lock confirm.
  useEffect(() => { setPw(''); setPw2(''); setErr(null); setLockArmed(false) }, [exists, unlocked])

  // the unseal: when the sealed grid FIRST paints after a successful unlock, run one warm gleam across it.
  useEffect(() => {
    if (!wantUnseal || !unlocked || q.isLoading) return
    setWantUnseal(false)
    if (dreams.length > 0) setUnsealing(true)
  }, [wantUnseal, unlocked, q.isLoading, dreams.length])
  useEffect(() => {
    if (!unsealing) return
    const t = window.setTimeout(() => setUnsealing(false), 1000)
    return () => window.clearTimeout(t)
  }, [unsealing])

  async function doInit(e: FormEvent) {
    e.preventDefault()
    setErr(null)
    if (pw.length < 4) { setErr('Use at least 4 characters.'); return }
    if (pw !== pw2) { setErr('The two passphrases don’t match.'); return }
    try {
      const j = await init.mutateAsync(pw)
      if (j?.error) setErr(j.error); else { setPw(''); setPw2('') }
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }
  async function doUnlock(e: FormEvent) {
    e.preventDefault()
    setErr(null)
    try {
      const j = await unlock.mutateAsync(pw)
      if (!j?.ok) setErr(j?.error || 'Wrong passphrase.'); else { setPw(''); setWantUnseal(true) }
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }
  async function doOpen(id: string) {
    setErr(null)
    try {
      const j = await open.mutateAsync(id)
      if (j?.error) { setErr(j.error); return }
      onOpened?.()
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }
  async function doDelete(id: string) {
    setErr(null)
    try {
      const j = await del.mutateAsync(id)
      if (j?.error) setErr(j.error)
      else if (!j?.ok) setErr('That dream couldn’t be removed — try again.')
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }
  async function doRename(id: string, next: string) {
    setErr(null)
    try {
      const j = await rename.mutateAsync({ id, name: next })
      if (!j?.ok) setErr('That dream couldn’t be renamed — try again.')
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }

  return (
    <div className="card stash-panel">
      <div className="stash-head">
        <h2 className="lk" data-view-heading tabIndex={-1}>🔒 Private stash</h2>
        {unlocked && (
          stashDreamOpen
            ? (lockArmed
              ? (
                <span className="row" style={{ margin: 0, gap: 8 }}>
                  <button className="stash-lock" style={{ color: 'var(--st-red)' }}
                    aria-label="Lock the stash — this reseals and closes the private dream you have open"
                    onClick={() => { setLockArmed(false); lock.mutate() }}>Reseal &amp; close</button>
                  <button className="stash-lock" onClick={() => setLockArmed(false)}>Cancel</button>
                </span>
              )
              : <button className="stash-lock" onClick={() => setLockArmed(true)}>Lock</button>)
            : <button className="stash-lock" onClick={() => lock.mutate()}>Lock</button>
        )}
      </div>

      {!exists && (
        <form onSubmit={doInit}>
          <p className="stash-intro">
            Keep private dreams <b>encrypted</b> on this computer — sealed with a passphrase, never shown
            elsewhere, never set as wallpaper. Only you, with the passphrase, can reopen them.
          </p>
          {/* the irreversibility is read BEFORE the commit button (it used to sit under it, in the smallest type) */}
          <p className="note" id="stash-norecover" style={{ marginTop: 8 }}>
            There’s no recovery — if you forget this passphrase, the stashed dreams can’t be opened.
          </p>
          <div className="pp-form">
            <input type="password" aria-label="Choose a passphrase" aria-describedby="stash-norecover"
              placeholder="Choose a passphrase" autoComplete="new-password"
              aria-invalid={err ? true : undefined} value={pw} onChange={(e) => setPw(e.target.value)} />
            <input type="password" aria-label="Repeat passphrase" aria-describedby="stash-norecover"
              placeholder="Repeat passphrase" autoComplete="new-password"
              aria-invalid={err ? true : undefined} value={pw2} onChange={(e) => setPw2(e.target.value)} />
            <button type="submit" className="beat warm" disabled={init.isPending}>Create stash</button>
          </div>
        </form>
      )}

      {exists && !unlocked && (
        <form onSubmit={doUnlock}>
          <p className="stash-intro">Locked. Enter your passphrase to reopen your private dreams.</p>
          <div className="pp-form">
            <input type="password" aria-label="Passphrase" placeholder="Passphrase"
              autoComplete="current-password" aria-invalid={err ? true : undefined}
              value={pw} onChange={(e) => setPw(e.target.value)} />
            <button type="submit" className="beat warm" disabled={unlock.isPending}>Unlock</button>
          </div>
        </form>
      )}

      {exists && unlocked && (
        <>
          {q.isLoading ? (
            <p className="stash-intro" aria-busy="true">Opening your private dreams…</p>
          ) : dreams.length === 0 ? (
            <p className="stash-intro">Open and empty. Start a <span className="lock">🔒 private</span> dream, then “Save to private stash”.</p>
          ) : (
            <div className={'lib-grid' + (unsealing ? ' unsealing' : '')} style={{ marginTop: 14 }}>
              {dreams.map((d, i) => (
                <Tile key={d.id} sealed i={i} name={d.name} frames={d.frames} when={ago(d.updated)} busy={busy}
                  onOpen={() => doOpen(d.id)} onDelete={() => doDelete(d.id)} onRename={(n) => doRename(d.id, n)} />
              ))}
            </div>
          )}
          <ChangePassphrase />
        </>
      )}

      {err && <div className="banner bad" role="alert" style={{ marginTop: 10 }}>{err}</div>}
    </div>
  )
}

// the saved (non-private) dream library
export function DreamGallery({ onOpened }: { onOpened?: () => void }) {
  const q = useLibrary()
  const open = useOpenDream()
  const del = useDelete()
  const rename = useRenameDream()
  const [err, setErr] = useState<string | null>(null)
  const dreams = q.data ?? []
  const busy = open.isPending || del.isPending || rename.isPending

  async function doOpen(session: string) {
    setErr(null)
    try {
      const j = await open.mutateAsync(session)
      if (j?.error) { setErr(j.error); return }
      onOpened?.()
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }
  async function doDelete(session: string) {
    setErr(null)
    try {
      const j = await del.mutateAsync(session)
      if (j?.failed?.length) setErr(`Some files could NOT be deleted: ${j.failed.join('; ')}. Delete by hand to be certain.`)
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }
  // ADR-0028 B1b: rename any saved dream by session, without opening it first.
  async function doRename(session: string, next: string) {
    setErr(null)
    try {
      const j = await rename.mutateAsync({ name: next, session })
      if (j?.error) setErr(j.error)
    } catch { setErr('Couldn’t reach Lucid — try again.') }
  }

  return (
    <div className="card">
      <h2 className="lib-head" data-view-heading tabIndex={-1}>
        <span>Your dreams</span>
        {dreams.length > 0 && <span className="count">{dreams.length} saved</span>}
      </h2>
      {q.isLoading ? (
        <p className="lib-empty" aria-busy="true">Loading your dreams…</p>
      ) : dreams.length === 0 ? (
        <p className="lib-empty">No saved dreams yet — start one from <b>New dream</b> and it’s kept here automatically, ready to reopen. You can rename it any time from its card.</p>
      ) : (
        <div className="lib-grid">
          {dreams.map((d, i) => (
            <Tile key={d.session} i={i} name={d.name} frames={d.frames} when={ago(d.updated)} busy={busy}
              thumb={thumbUrl(d.session)} onOpen={() => doOpen(d.session)} onDelete={() => doDelete(d.session)}
              onRename={(n) => doRename(d.session, n)} />
          ))}
        </div>
      )}
      {err && <div className="banner bad" role="alert" style={{ marginTop: 10 }}>{err}</div>}
    </div>
  )
}
