import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useStart, useOpenings, fileToB64 } from './api'
import type { Opening } from './api'

// The entry "fork in the dark" (Corey's design — claude.ai/design "Lucid Entry", imported 2026-06-23):
// a few model-authored "ways in" arranged around a central compose node you can also describe into or
// drop an image onto. Faithful to that design, bound to the AgentOS instrument tokens for cohesion with
// the dream view it hands off to, with the full safety contract (B2 likeness consent, Private/Mature,
// the standalone red line) re-integrated — the prototype dropped it and the binding privacy consult
// rules it non-negotiable.

// Relocated B2 line (council §5): a gift, shown once a file is chosen — not a watch over an empty form.
const B2_GIFT = 'We check this for real-person likeness and strip its location and camera metadata before anything else.'

// the first-moment length writes the SAME sticky preference the Chain's per-beat picker reads
// (lucid.defaultLen, frames@16fps mirroring lucid_engine MIN/MAX). So choosing here pre-sets the length
// of the first motion beat; the opening still itself has no length.
const LEN_KEY = 'lucid.defaultLen'
const LENGTHS: { f: number; s: string }[] = [
  { f: 17, s: '1s' }, { f: 33, s: '2s' }, { f: 49, s: '3s' }, { f: 65, s: '4s' }, { f: 81, s: '5s' },
]
function loadLen(): number {
  try { const v = parseInt(localStorage.getItem(LEN_KEY) || '', 10); if (LENGTHS.some((o) => o.f === v)) return v } catch { /* storage off */ }
  return 33
}
function saveLen(f: number) { try { localStorage.setItem(LEN_KEY, String(f)) } catch { /* non-fatal */ } }

type PathSeg = { d: string; ax: number; ay: number }

export default function Start({ onStarted }: { onStarted?: () => void }) {
  const start = useStart()
  const openingsQ = useOpenings()
  const openings = openingsQ.data ?? []
  const loadingOpenings = openingsQ.isLoading

  const [priv, setPriv] = useState(false)
  // the user-declared content floor (the per-frame VLM is conservative; without this a dream you INTEND
  // mature can render entirely SFW). The red line (minors / real people) is independent + code-enforced.
  const [mature, setMature] = useState(false)
  const [len, setLen] = useState<number>(loadLen)
  // ADR-0044 wizard handoff: a model-written opening prompt arrives as ?prompt=… Prefill once (≤2000),
  // never auto-start (the B2 gate + mature floor stay intact).
  const [prompt, setPrompt] = useState(() => {
    try { return (new URLSearchParams(location.search).get('prompt') || '').slice(0, 2000) } catch { return '' }
  })
  const [image, setImage] = useState<string | null>(null)   // data-URL preview of a chosen seed image
  const [imageName, setImageName] = useState('')
  const [drag, setDrag] = useState(false)
  const [nudge, setNudge] = useState(false)                  // warm "choose a way in" hint on empty begin
  // single persistent live-region message — starts EMPTY (council §7 A1) so nothing is announced on mount.
  const [msg, setMsg] = useState('')
  const [echo, setEcho] = useState(false)                    // S2: the calm "nothing was lost" cancel settle
  // B2 likeness consent: a real-person seed returns requires_consent → an in-page card (never a native confirm).
  const [consentReason, setConsentReason] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // strip ?prompt= from the URL once so a (possibly mature) opening can't linger in history / a shared screen.
  useEffect(() => {
    try {
      if (new URLSearchParams(location.search).has('prompt'))
        history.replaceState({}, '', location.pathname + location.hash)
    } catch { /* no-op */ }
  }, [])

  function clearConsent() { setConsentReason(null) }
  function pickLen(f: number) { setLen(f); saveLen(f) }

  function readImg(file?: File | null) {
    if (!file || !/^image\//.test(file.type)) return
    clearConsent(); setEcho(false); setNudge(false)
    setImageName(file.name)
    const r = new FileReader()
    r.onload = (ev) => setImage(typeof ev.target?.result === 'string' ? ev.target.result : null)
    r.readAsDataURL(file)
  }
  function removeImage() {
    setImage(null); setImageName(''); clearConsent()
    if (fileRef.current) fileRef.current.value = ''
  }

  // begin from a chosen glimpse seed (text), the typed prompt (text), the uploaded image (B2-gated), or —
  // if all empty — a nudge. `seed` overrides the textarea (a glimpse click). `consent` rides only the
  // explicit consent-card button.
  async function begin(seed?: string, consent = false) {
    const text = (seed ?? prompt).trim()
    const file = fileRef.current?.files?.[0] || null
    if (!text && !file && !image) {     // nothing to dream from → the warm nudge (auto-clears)
      setNudge(true); window.clearTimeout(nudgeT.current); nudgeT.current = window.setTimeout(() => setNudge(false), 2800)
      return
    }
    setConsentReason(null); setEcho(false); setNudge(false)
    const body: { private: boolean; image_b64?: string; text?: string; consent?: boolean; mature?: boolean } =
      { private: priv, mature: mature || undefined }
    if (file && !text) {                // an image seed (only when not overridden by a glimpse/typed text)
      setMsg('Checking your image for real-person likeness…')
      body.image_b64 = await fileToB64(file); body.consent = consent
    } else if (text) {
      setMsg('Painting your opening…'); body.text = text; body.consent = consent
    } else { setMsg('Opening the dream…') }
    let j
    try { j = await start.mutateAsync(body) }
    catch { setMsg('Could not reach Lucid — try again.'); return }
    if (j?.blocked) {
      if (j.requires_consent) { setConsentReason(j.reason); setMsg(''); return }   // overridable likeness → card
      setMsg('🚫 ' + j.reason); return                                            // hard block (e.g. minor) → plain refusal
    }
    if (j?.error) { setMsg(j.error); return }
    onStarted?.()                       // success — the poll flips to the dream view (this unmounts)
  }
  const nudgeT = useRef<number>(0)

  // S2 — Cancel reads as "nothing was lost": clear the staged image, settle a calm echo into the live region.
  function cancelConsent() {
    clearConsent(); removeImage(); setEcho(true); setMsg('Cleared — nothing was uploaded.')
  }

  // ── connector paths: measure the four glimpse cards + the centre node and draw beziers converging on it
  // (faithful to the design's measurePaths). Degrades to nothing if the layout is too narrow/unmeasured —
  // the cards stack on mobile and there is nothing to connect.
  const stageRef = useRef<HTMLDivElement>(null)
  const centerRef = useRef<HTMLDivElement>(null)
  const cardRefs = useRef<(HTMLButtonElement | null)[]>([])
  const [paths, setPaths] = useState<PathSeg[]>([])
  const [vb, setVb] = useState('0 0 1000 600')
  useLayoutEffect(() => {
    const stage = stageRef.current, center = centerRef.current
    if (!stage || !center) return
    let raf = 0
    const measure = () => {
      const sr = stage.getBoundingClientRect(), cr = center.getBoundingClientRect()
      const cards = cardRefs.current.filter(Boolean) as HTMLButtonElement[]
      if (cards.length < 4 || sr.width < 700) { setPaths([]); return }   // narrow → cards stack, no paths
      const cxL = cr.left - sr.left, cxR = cr.right - sr.left, cyc = (cr.top + cr.height / 2) - sr.top
      const n = (v: number) => Math.round(v * 10) / 10
      const segs = cards.map((el) => {
        const r = el.getBoundingClientRect()
        const ccx = (r.left + r.width / 2) - sr.left, ccy = (r.top + r.height / 2) - sr.top
        const left = ccx < sr.width / 2, top = ccy < cyc
        const ax = left ? (r.right - sr.left) : (r.left - sr.left), ay = ccy
        const tx = left ? cxL : cxR, ty = cyc + (top ? -cr.height * 0.17 : cr.height * 0.17)
        const dx = tx - ax
        return { d: `M ${n(ax)} ${n(ay)} C ${n(ax + dx * 0.5)} ${n(ay)} ${n(tx - dx * 0.32)} ${n(ty)} ${n(tx)} ${n(ty)}`, ax: n(ax), ay: n(ay) }
      })
      setVb(`0 0 ${Math.round(sr.width)} ${Math.round(sr.height)}`)
      setPaths(segs)
    }
    const schedule = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => requestAnimationFrame(measure)) }
    schedule()
    const ro = new ResizeObserver(schedule)
    ro.observe(stage)
    if (document.fonts?.ready) document.fonts.ready.then(schedule).catch(() => {})
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [openings.length, image, consentReason])

  const busy = start.isPending
  // the four corner slots, paired with a generated opening (or a skeleton placeholder while they load).
  const slots = [0, 1, 2, 3]

  return (
    <div className="fork">
      {/* immersive ambient — fixed full-viewport, behind everything; reduced-motion/transparency gated */}
      <div className="fork-amb" aria-hidden="true">
        <span className="fork-aurora"><i /><i /><i /></span>
        <span className="fork-grain" />
        <span className="fork-vig" />
        <span className="fork-motes">{[...Array(6)].map((_, i) => <span key={i} className={'mote m' + i} />)}</span>
      </div>

      {/* the heading sits over the ambient, so it carries its own dark scrim (.fork-head::before) to keep
          the small grey eyebrow/sub AA regardless of where the aurora blobs drift (a11y consult) */}
      <div className="fork-head">
        <p className="fork-eyebrow"><span className="spark" aria-hidden="true">✦</span> Begin a dream</p>
        <h1 className="fork-hero" data-view-heading tabIndex={-1}>What will you <span>dream</span> tonight?</h1>
        <p className="fork-sub">Step through one of these openings — or describe the first moment yourself.
          The path is yours; Lucid dreams it into motion.</p>
      </div>

      {/* SR-only announcement of the openings loading state (the shimmer skeleton is visual-only) */}
      <div className="sr" role="status" aria-live="polite">
        {loadingOpenings ? 'Painting four ways in…' : openings.length ? `${openings.length} openings ready — or describe your own.` : ''}
      </div>

      {/* THE STAGE: a fork in the dark — four ways in around the centre compose node */}
      <div className="fork-stage" ref={stageRef} aria-busy={loadingOpenings || undefined}>
        <span className="fork-aurora inner" aria-hidden="true"><i /><i /><i /></span>
        <span className="fork-vig inner" aria-hidden="true" />

        {paths.length > 0 && (
          <svg className="fork-paths" viewBox={vb} preserveAspectRatio="none" aria-hidden="true">
            <g fill="none" stroke="var(--inst-blue)" strokeWidth="1.4" opacity=".3">
              {paths.map((p, i) => <path key={i} d={p.d} />)}
            </g>
            <g fill="none" stroke="#cfe0ff" strokeWidth="1.4" strokeLinecap="round" strokeDasharray="4 11">
              {paths.map((p, i) => <path key={i} className="flowdash" d={p.d} />)}
            </g>
            <g fill="#cfe0ff">
              {paths.map((p, i) => <circle key={i} className="flownode" cx={p.ax} cy={p.ay} r="3" />)}
            </g>
          </svg>
        )}

        {/* glimpse cards (corners) — model-authored openings, or a subtle skeleton while they generate */}
        {slots.map((i) => {
          const o: Opening | undefined = openings[i]
          const cls = 'glimpse g' + i
          // skeleton ONLY while generating (the user groks the UI meanwhile); a loaded-but-empty slot
          // (the endpoint errored) is simply omitted — the compose node still carries the whole flow.
          if (!o) {
            if (!loadingOpenings) return null
            return (
              <div key={i} className={cls + ' skel'} aria-hidden="true">
                <span className="gl-thumb" /><span className="sk sk-eyebrow" /><span className="sk sk-title" /><span className="sk sk-line" />
              </div>
            )
          }
          return (
            <button key={i} ref={(el) => { cardRefs.current[i] = el }} className={cls} disabled={busy}
              onClick={() => begin(o.seed)} title={o.line}>
              <span className="gl-thumb" aria-hidden="true"><span className="gl-tag">glimpse</span></span>
              <span className="gl-eyebrow">A way in</span>
              <span className="gl-title">… {o.title}</span>
              <span className="gl-line">{o.line}</span>
            </button>
          )
        })}

        {/* centre: the convergence node — describe / drop an image / begin */}
        <div className="fork-center" ref={centerRef}
          onDrop={(e) => { e.preventDefault(); setDrag(false); readImg(e.dataTransfer.files?.[0]) }}
          onDragOver={(e) => e.preventDefault()}
          onDragEnter={(e) => { e.preventDefault(); setDrag(true) }}
          onDragLeave={(e) => { e.preventDefault(); setDrag(false) }}>
          {drag && <div className="fork-drop" aria-hidden="true"><span>Drop your image to dream from it</span></div>}

          {image && (
            <div className="fork-chip">
              <img src={image} alt="" className="chip-img" />
              <div className="chip-meta">
                <div className="chip-k">Dreaming from your image</div>
                <div className="chip-name">{imageName}</div>
              </div>
              <button onClick={removeImage} aria-label="Remove image" className="chip-x">×</button>
            </div>
          )}

          <div className="fork-c-eyebrow"><span className="spark" aria-hidden="true">✦</span> Or dream your own</div>
          <textarea className="fork-text" value={prompt} disabled={busy} rows={2}
            aria-label="Describe the first moment"
            onChange={(e) => { setPrompt(e.target.value); clearConsent(); setEcho(false); setNudge(false) }}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !busy && !consentReason) begin() }}
            placeholder="Describe the first moment…" />

          {image && <p className="note fork-gift">{B2_GIFT}</p>}
          {/* honesty cue: text wins — a staged image is only used when the words are empty (privacy consult Low-1) */}
          {image && prompt.trim() && <p className="note fork-gift">Lucid will dream from your words — clear the text to dream from the image instead.</p>}

          <div className="fork-c-row">
            <label className="fork-pick" aria-label="Add an image">
              <input ref={fileRef} type="file" accept="image/*" className="sr"
                onChange={(e) => { readImg(e.target.files?.[0]); e.currentTarget.value = '' }} />
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="4" width="18" height="16" rx="2.5" /><circle cx="8.5" cy="9.5" r="1.6" /><path d="M5 18l4.5-4.5L13 17l3-3 3 3" />
              </svg>
            </label>
            <span className="fork-c-hint">Set the tone — Lucid takes it from here.</span>
            <button className="fork-go" disabled={busy || !!consentReason} aria-label="Begin dreaming" onClick={() => begin()}>
              {busy ? <span className="go-spin" aria-hidden="true" /> : (
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
                  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h13" /><path d="M12 5l7 7-7 7" /></svg>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* B2 consent — the one warm "needs you" moment (copper), in-page, never a native confirm */}
      {consentReason && (
        <div className="consent fork-consent" role="group" aria-label="Real-person likeness check">
          <div className="note">Only continue if this is you, or you hold the right to use this image. A real-person
            dream is never set as wallpaper or shown elsewhere.</div>
          <div className="row">
            <button className="beat warm" disabled={busy} onClick={() => begin(undefined, true)}>
              <b>I have the right to use this image — continue</b>
            </button>
            <button className="ghost" onClick={cancelConsent}>Cancel</button>
          </div>
        </div>
      )}

      {/* persistent live region (council §7 A2), empty at rest; the consent reason / status is announced here.
          There is intentionally NO audible cue anywhere on this screen. */}
      <div className="fork-live" role="status" aria-live="polite">
        {consentReason ? <span className="fork-needs">{consentReason}</span>
          : msg ? <span className={echo ? 'sc-reset-echo' : undefined}>{msg}</span> : null}
      </div>
      {nudge && <div className="fork-nudge">Choose a way in, or describe the first moment to begin.</div>}

      {/* before you begin — declarations + the length of the first moment */}
      <div className="fork-bar">
        <div className="fork-decl">
          <label className="thr-switch">
            <input type="checkbox" role="switch" className="sr" checked={priv} onChange={(e) => setPriv(e.target.checked)} />
            <span className="thr-track" aria-hidden="true"><span className="thr-knob" /></span>
            <span className="thr-switch-text"><span className="thr-switch-name"><span className="lock">🔒 Private session</span></span>
              <span className="note">Kept in memory, never saved, wiped when you log out.</span></span>
          </label>
          <label className="thr-switch">
            <input type="checkbox" role="switch" className="sr" checked={mature} onChange={(e) => setMature(e.target.checked)} />
            <span className="thr-track" aria-hidden="true"><span className="thr-knob" /></span>
            <span className="thr-switch-text"><span className="thr-switch-name"><span className="lock">🔞 Mature dream</span>
              {mature && <span className="tag tag-mature">18+</span>}</span>
              <span className="note">Explicit from the first frame. Off keeps it tasteful.</span></span>
          </label>
        </div>
        <div className="fork-len">
          <span className="fork-len-k">Length of the first moment</span>
          <div className="fork-len-opts" role="group" aria-label="Length of the first moment">
            {LENGTHS.map((o) => (
              <button key={o.f} className={'lenbtn' + (len === o.f ? ' on' : '')} aria-pressed={len === o.f}
                onClick={() => pickLen(o.f)}>{o.s}</button>
            ))}
          </div>
        </div>
      </div>
      {/* the red line — standalone, ALWAYS visible, never nested in a checkbox label (council §2) */}
      <p className="fork-redline">Minors and real people are always blocked.</p>
    </div>
  )
}
