import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent, type KeyboardEvent } from 'react'
import type { DreamNode, LucidState, Beat, Note } from './api'
import { clipUrl, frameUrl, previewUrl, downloadUrl, useBeats, useDream, useHero, useStartBeatPreviews, useRefine, useAddNote, useDeleteNote, segment, previewsEnabled, fuse, useEditRevert, type FuseResult } from './api'
import EditPanel from './EditPanel'

// pointer-parallax rect cache — read once per card on mouseenter so mousemove never forces a reflow.
const rectCache = new WeakMap<HTMLElement, DOMRect>()

// Stable empty references. A `?? []` / `= []` default mints a FRESH array every render whenever a query
// has no data (disabled or in-flight); if that array is a dependency of an effect — or the value a state
// setter is called with — React's Object.is check never matches, so the effect re-runs / re-renders every
// render. Shared module-level constants keep those identities stable (was the React #185 gutter-link loop).
const NO_BEATS: Beat[] = []
const NO_PATHS: { key: string; d: string }[] = []

const FPS = 16   // Wan i2v cadence (lucid_engine); a node's `length` is a frame count -> seconds = length / FPS
const fmtDur = (secs: number) => `${Math.floor(secs / 60)}:${String(Math.round(secs % 60)).padStart(2, '0')}`

// ADR-0023 spatial feed-forward: the four moment-NOTE directions. `hold` is the steering primitive (anchor
// the next beat on this exact moment) so it leads; the others nudge direction. Order = how the chips are
// offered. Vocabulary (ADR-0023): a "note" is the thing; these four are its "direction" — the imperative-to-
// the-element form ("it" = the tapped element, or "this moment" frame-wide). The internal `tag` enum is
// unchanged (no migration); only the words the user reads changed.
const TAGS: { tag: Note['tag']; label: string }[] = [
  { tag: 'hold', label: 'Keep this' },
  { tag: 'more', label: 'More of it' },
  { tag: 'less', label: 'Less of it' },
  { tag: 'change', label: 'Change it' },
]
const tagLabel = (t: Note['tag']) => TAGS.find((x) => x.tag === t)?.label ?? t

// The note as ONE human clause — for the Shot Card row's accessible name and the history list. The on-frame
// numbered pin is the spatial referent (we don't name the object — that would need a caption pass), so the
// sentence stays deictic; the optional text is the user's own words, appended after an em-dash.
function noteSentence(n: Note): string {
  const base = n.tag === 'hold' ? 'Keep this moment'
    : n.tag === 'more' ? 'More of this'
      : n.tag === 'less' ? 'Less of this'
        : 'Change this'
  const t = (n.text || '').trim()
  return t ? `${base} — ${t}` : base
}

// next-segment length options (frames @16fps -> seconds). Mirrors lucid_engine MIN_LEN/MAX_LEN; the
// engine clamps anything off-list, so this is purely the UI offer (folded in from the old <Choice>).
const LENGTHS: { f: number; s: string }[] = [
  { f: 17, s: '1s' }, { f: 33, s: '2s' }, { f: 49, s: '3s' }, { f: 65, s: '4s' }, { f: 81, s: '5s' },
]

// The default length is a STICKY personal preference (progressive disclosure: the per-moment picker the
// user already touches IS the default-setter — no separate settings surface). The last length picked is
// remembered (localStorage) and seeds the picker for every later beat AND every new dream. The built-in
// fallback is 33f (~2s), matching lucid_engine DEFAULT_LEN. A stale/garbage stored value can't break the
// picker — it's validated against the offered frames — and the engine clamps it again regardless.
const LEN_KEY = 'lucid.defaultLen'
function loadDefaultLen(): number {
  try {
    const v = parseInt(localStorage.getItem(LEN_KEY) || '', 10)
    if (LENGTHS.some((o) => o.f === v)) return v
  } catch { /* localStorage unavailable (private-mode quota / disabled) — fall through to the built-in */ }
  return 33
}
function saveDefaultLen(f: number) {
  try { localStorage.setItem(LEN_KEY, String(f)) } catch { /* non-fatal: the in-session pick still applies */ }
}

// The develop hero: the aurora forms while the GPU generates, then — when the finished frame arrives
// (`posterSrc`) — that frame blooms IN over the aurora, in the SAME box, under the held serif caption,
// and `onResolved` fires after the dissolve so the real <video> takes over in place (ADR-0014's "the
// clip developing"). A child component so its `posterIn` state re-arms by unmounting between beats.
function DevelopHero({ caption, posterSrc, onResolved, canReview = false, onReview }:
  { caption: string | null; posterSrc: string | null; onResolved: () => void; canReview?: boolean; onReview?: () => void }) {
  const [posterIn, setPosterIn] = useState(false)
  function onPosterLoad() {
    setPosterIn(true)
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.setTimeout(onResolved, reduce ? 0 : 950)   // after the bloom completes, hand off to the player
  }
  return (
    <div className={'stage' + (posterIn ? ' poster-in' : '')} aria-busy="true">
      <div className="aurora"><i /><i /><i /></div>
      <div className="grain" />
      {posterSrc && <img className="poster-reveal" src={posterSrc} alt="" onLoad={onPosterLoad} />}
      <div className="stage-vig" />
      <div className="cap">
        {/* a generating hero (no poster) wears a live "forming" badge so a curated pick — which otherwise shows
            only its label — still reads as "working", and the cue is up the instant the click commits. */}
        {!posterSrc && caption && <span className="forming-k">✦ forming…</span>}
        <p className="beat-q" style={caption ? undefined : { opacity: 0.6 }}>
          {caption ? `“${caption}”` : 'the next moment is forming…'}
        </p>
      </div>
      {/* a beat takes minutes — let the viewer rewatch the dream so far during the wait without losing this
          forming view. Only while genuinely forming (no poster = not the reveal handoff) and there are clips. */}
      {canReview && onReview && !posterSrc && (
        <button type="button" className="peek-watch" onClick={onReview}>
          <span aria-hidden="true">▷</span> Watch the dream so far
        </button>
      )}
    </div>
  )
}

// ---- the dream-tree geometry (the chain as a git-graph; now PURE history/branch-map — the "what
// happens next" choices moved onto the cinematic stage's side gutters, ADR-0023 council brief) ----
const NW = 40, NH = 71, DX = 58, DY = 52, X0 = 8, Y0 = 12

// "Your dream so far" is ONE instrument: a cinematic 9:16 player whose colour spills behind it, with the
// "what happens next" choices in the empty side GUTTERS beside the portrait clip (never over the image),
// and a git-graph of the chain below (lit checked-out path, dim alternate takes). `revealing` is App's
// reveal hold; the develop hero's poster dissolve (onResolved) is what releases it.
export default function Chain({ state, revealing = false, onLatestReady }:
  { state: LucidState; revealing?: boolean; onLatestReady?: () => void }) {
  const nodes = state.chain!.nodes
  const dreaming = state.turn.phase === 'dreaming'
  const caption = state.turn.label && state.turn.label !== 'custom' ? state.turn.label : null

  const playable = nodes.filter((n) => n.clip)
  const latest = playable.length ? playable[playable.length - 1].id : nodes[nodes.length - 1].id
  const tipId = nodes[nodes.length - 1].id
  // "Play all" plays THE DREAM — the lit story path root→tip (the same spine the tree lights and Download
  // stitches), NOT nodes.filter(clip): that flat list is every clip ever made and, on a BRANCHED dream,
  // interleaves abandoned alternate takes in id-order (so play-all would play a dead take out of sequence).
  // Walk parent pointers from the tip back to the root, reverse, keep the ones with a clip — mirrors
  // lucid_stitch.clip_spine + the tree's litSet, so Play-all, Download, and the lit path stay one sequence.
  const playSpine = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n]))
    const line: DreamNode[] = []
    const seen = new Set<number>()
    let cur = byId.get(tipId)
    while (cur && !seen.has(cur.id)) { line.push(cur); seen.add(cur.id); cur = cur.parent == null ? undefined : byId.get(cur.parent) }
    return line.reverse().filter((n) => n.clip)
  }, [nodes, tipId])
  const [selId, setSelId] = useState<number>(latest)
  const [playAll, setPlayAll] = useState(false)
  const [repeat, setRepeat] = useState(true)
  const [downloading, setDownloading] = useState(false)   // stitching the whole dream into one MP4
  const [dwell, setDwell] = useState(false)   // clip has played once at a choice moment → choices available + the clip LOOPS while you choose
  const treeRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)   // stage + choices; the connector measure spans both
  const videoRef = useRef<HTMLVideoElement>(null)
  // ADR-0032: the tag overlay must sit on the VIDEO CONTENT rect, not the stage — the clip is letterboxed
  // within the stage (object-fit:contain, centered), and its aspect may not match, so a stage-relative tap
  // (and the mask) would land wrong. `mediaBox` is the displayed content rect as % of the stage; we size the
  // .tag-aim overlay to it so a tap maps straight to frame-normalized coords and the silhouette aligns.
  const [mediaBox, setMediaBox] = useState<{ l: number; t: number; w: number; h: number } | null>(null)
  const [paused, setPaused] = useState(false)   // clip paused (so you can tag a still moment) — synced to the <video>

  // Follow the newest clip as the story advances — but only if the user is already watching the tip;
  // if they've clicked back to review an earlier beat, don't yank them to the new clip mid-watch.
  const prevLatest = useRef(latest)
  const branched = useRef(false)   // set when we fire a take; clears by jumping to the new beat once it lands
  useEffect(() => {
    if (selId === prevLatest.current) setSelId(latest)                            // watching the tip → follow it
    else if (branched.current && latest !== prevLatest.current) setSelId(latest)  // our new take landed → jump to it
    if (latest !== prevLatest.current) branched.current = false
    prevLatest.current = latest
  }, [latest, selId])
  const sel = nodes.find((n) => n.id === selId) ?? nodes[nodes.length - 1]
  const idx = nodes.findIndex((n) => n.id === sel.id)
  const atHead = sel.id === tipId
  const viewingLatestClip = sel.id === latest && !!sel.clip
  useEffect(() => {
    if (!viewingLatestClip) onLatestReady?.()   // nothing to wait for — release the reveal hold immediately
  }, [viewingLatestClip, sel.id, onLatestReady])

  // ---- futures (the "what happens next" beats — now rendered in the stage gutters) ----
  const canDream = state.readiness.can_dream
  // `draftOpen` (the tag panel) is declared up here so it can GATE the futures: while you're ACTIVELY
  // TAGGING a moment, the "what happens next" choices/connectors clear off the stage so the whole clip is
  // tappable (ADR-0032 — they were covering the lower clip, leaving only the top reachable).
  const [draftOpen, setDraftOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)   // ADR-0040: the prompt-guided keyframe-edit panel
  const editEnabled = state.edit_enabled !== false   // default available (legacy state w/o the flag)
  // Measure the displayed video/still CONTENT rect (object-fit:contain within the .vid element box) as % of
  // the stage, so the tag overlay lands exactly on the clip. Re-measures on resize, metadata-load, and when
  // the draft opens. Only runs while tagging (the overlay is only shown then).
  useEffect(() => {
    if (!draftOpen) return
    const measure = () => {
      const s = stageRef.current
      const m = s?.querySelector('.vid, .still') as HTMLVideoElement | HTMLImageElement | null
      if (!s || !m) { setMediaBox(null); return }
      const sb = s.getBoundingClientRect(), mb = m.getBoundingClientRect()
      if (sb.width < 1 || sb.height < 1 || mb.width < 1) return
      const iw = (m as HTMLVideoElement).videoWidth || (m as HTMLImageElement).naturalWidth || mb.width
      const ih = (m as HTMLVideoElement).videoHeight || (m as HTMLImageElement).naturalHeight || mb.height
      const scale = Math.min(mb.width / iw, mb.height / ih)          // object-fit:contain content rect
      const cw = iw * scale, ch = ih * scale
      const cl = mb.left + (mb.width - cw) / 2, ct = mb.top + (mb.height - ch) / 2
      setMediaBox({ l: ((cl - sb.left) / sb.width) * 100, t: ((ct - sb.top) / sb.height) * 100,
        w: (cw / sb.width) * 100, h: (ch / sb.height) * 100 })
    }
    measure()
    const s = stageRef.current, m = s?.querySelector('.vid, .still')
    const ro = new ResizeObserver(measure)
    if (s) ro.observe(s)
    if (m) ro.observe(m)
    const v = videoRef.current
    v?.addEventListener('loadedmetadata', measure)
    window.addEventListener('resize', measure)
    return () => { ro.disconnect(); v?.removeEventListener('loadedmetadata', measure); window.removeEventListener('resize', measure) }
  }, [draftOpen, sel.id, sel.clip])
  // pause/resume the clip while tagging so you can mark up a still moment (the native controls are under the
  // tag overlay). `paused` stays in sync via the <video> onPlay/onPause handlers.
  function togglePlay() {
    const v = videoRef.current; if (!v) return
    if (v.paused) v.play().catch(() => {}); else { v.pause(); setDraftT(v.currentTime) }
  }
  // futures grow from the SELECTED beat: at the tip it's "continue", at an earlier beat it's "branch a new take"
  const showFutures = !dreaming && !revealing && canDream && !draftOpen && !editOpen
  const branchingFrom = sel.id !== tipId
  const dream = useDream()
  const hero = useHero()                          // ADR-0033: re-render the selected beat at HD (the same shot)
  const revert = useEditRevert()                  // ADR-0040: undo an in-place edit (restore the backed-up shot)
  const startBeatPreviews = useStartBeatPreviews()   // ADR-0023: render per-choice "potential path" stills on dwell
  const [own, setOwn] = useState('')
  // ADR-0023 juncture refine: ✨ sharpens the typed idea into a vivid, frame-grounded next beat (model
  // proposes; the red-line gate disposes), filled BACK into this same input — still fully editable before
  // you dream it. `refineMsg` is the honest inline status (refined / a calm reason); `ownRef` returns focus
  // to the input so the refined text is immediately editable.
  const refine = useRefine()
  const [refineMsg, setRefineMsg] = useState<string | null>(null)
  const ownRef = useRef<HTMLInputElement>(null)
  // ADR-0023 the Shot Card readback ("how Lucid reads this"): when the selected beat carries notes, /api/fuse
  // assembles the EXACT prompt the next beat would run (notes decomposed + subject folded in) so the user can
  // SEE and EDIT it before committing minutes of generation. `fuseRes` is the server reading (held by digest);
  // `fusedEdit` is the user's edit of it (null = unedited → run the server reading verbatim). The fuse fires
  // during the dwell off a debounced copy of the typed line (a VLM call — never per-keystroke). `hotNote`
  // is the hover/focus link between an on-frame numbered pin and its composite row.
  const [fuseRes, setFuseRes] = useState<FuseResult | null>(null)
  const [fusedEdit, setFusedEdit] = useState<string | null>(null)
  const [fuseBusy, setFuseBusy] = useState(false)
  const [ownDeb, setOwnDeb] = useState('')
  const [hotNote, setHotNote] = useState<string | null>(null)
  const fuseSeq = useRef(0)   // ignore a stale fuse response if a newer one was fired meanwhile
  // ADR-0023 "the reset is the quiet hero" (delight): ↺ doesn't just snap the edit away — Lucid's words
  // BLOOM back (a re-keyed sc-settle) and a brief warm echo confirms the revert is safe, not a cold value-snap.
  const [reflow, setReflow] = useState(0)            // bump to re-bloom the reading on reset (re-keys the textarea)
  const [resetEcho, setResetEcho] = useState(false)  // the transient "back to Lucid's reading" line
  // §1.4 (audit): a typed custom prompt is original user work with no second copy. Stash the last
  // fired text so a fail-open turn (skipped/error/refused) can restore it into the input instead of
  // losing it; cleared once a beat actually lands (the per-turn reset below).
  const lastCustom = useRef<string | null>(null)
  const [len, setLen] = useState(loadDefaultLen)   // seeded from the sticky default; built-in ~2s on first use
  // picking a length is also how the user SETS their default — every pick is remembered (legible via the
  // "your default" tag), so a preferred cadence never has to be re-chosen beat after beat.
  const chooseLen = (f: number) => { setLen(f); saveDefaultLen(f) }
  const [flash, setFlash] = useState('')
  const [committed, setCommitted] = useState(false)
  // the just-committed beat's label — shown as the forming hero's caption to BRIDGE the gap between the click
  // and the 2.5–5s poll flipping the server turn to 'dreaming' (without it, a click read as a no-op).
  const [pendingLabel, setPendingLabel] = useState<string | null>(null)
  const busy = dream.isPending || hero.isPending || committed
  // While a beat generates (minutes) the DevelopHero owns the whole stage, so the dream-so-far isn't
  // playable. `peeking` lets the viewer rewatch DURING the wait: the stage swaps back to the player (tap any
  // beat in the tree, or "Watch the dream so far" on the hero), one tap returns to the forming beat, and it
  // auto-exits when the new beat lands (the per-turn reset below). Distinct from `revealing` (App's reveal
  // hold) — peeking is gated OFF during the reveal handoff so the poster bloom is never interrupted.
  const [peeking, setPeeking] = useState(false)
  // peeking only takes effect WHILE generating (not the brief reveal handoff) and only when the selected
  // beat has a clip to play — then the stage renders the player instead of the forming hero.
  const reviewWhileDreaming = peeking && (dreaming || busy) && !revealing && !!sel.clip
  // ADR-0033: the clip actually played is the finalized HERO re-render when present, else the draft. Used
  // for the <video> src/key so a finalize swaps in the HD shot (the ?v= ref changes → the browser refetches).
  const playClip = sel.hero_clip ?? sel.clip
  const isHero = !!sel.hero_clip
  // offer "Finalize in HD" on a rendered, not-yet-finalized beat (never the clip-less opening) when idle
  const canFinalize = !!sel.clip && !isHero && sel.id !== 0 && !busy && !dreaming && !revealing
  // ADR-0023 council S2 ("the path you didn't take is remembered, never wasted"): when you pick a beat,
  // the options you DIDN'T pick are stashed per node — so a branched-from beat keeps them, one click from
  // blooming. Reversibility (ADR-0005) made generative. Zero-GPU (a label + the on-disk conditioning still).
  // Session-scoped for now; durable persistence is a small tree.json field (backend follow-up).
  const [ghosts, setGhosts] = useState<Record<number, Beat[]>>({})

  // ---- ADR-0023 moment tags (spatial feed-forward): annotate THIS clip; steers the next beat ----
  const addNote = useAddNote()
  const delNote = useDeleteNote()
  // the inline tag draft: the captured time, the selected intent (defaults to the `hold` primitive), and
  // optional text. `draftOpen` (declared above, so it can gate the futures) toggles the panel.
  const [draftT, setDraftT] = useState(0)
  const [draftTag, setDraftTag] = useState<Note['tag']>('hold')
  const [draftText, setDraftText] = useState('')
  // ADR-0025: an OPTIONAL spatial point (normalized 0..1 over the clip) saying WHERE the tag applies. null
  // = a frame-wide note (legacy). Placed by tapping the clip while the draft is open; the engine turns it
  // into a soft-disc attention mask. Radius is the server default — one tap is the whole gesture.
  const [draftPt, setDraftPt] = useState<{ x: number; y: number } | null>(null)
  // ADR-0032: the tap is segmented — SAM2 returns the tapped OBJECT'S mask, shown as a highlight. `draftMask`
  // is the stored mask ref saved on the note (the precise silhouette); `draftPreview` is its inline data-URL
  // for the overlay; `segmenting` drives the honest "finding the object…" state. Fail-open: if segmentation
  // is unavailable (cold/contended GPU) the tap falls back to the bare point (draftPt) — a frame region note.
  const [draftMask, setDraftMask] = useState<string | null>(null)
  const [draftPreview, setDraftPreview] = useState<string | null>(null)
  const [segmenting, setSegmenting] = useState(false)
  const segSeq = useRef(0)   // ignore a stale segment response if the user re-tapped meanwhile
  // Reset transient per-beat / per-turn UI when the selected beat or the turn phase changes — adjusted
  // DURING render (React-docs "you might not need an effect"), not via a set-state-in-effect that cascades
  // an extra render. Each guard flips its own prev-tracker so it fires once per change, and every target is
  // idempotent (draft closed / dwell off / menu unlocked), so there's no loop. (phase covers `dreaming`,
  // which is just phase === 'dreaming'.)
  const [prevSelId, setPrevSelId] = useState(sel.id)
  const [prevPhase, setPrevPhase] = useState(state.turn.phase)
  if (sel.id !== prevSelId) { setPrevSelId(sel.id); setDraftOpen(false); setEditOpen(false); setDwell(false); setDraftMask(null); setDraftPreview(null); setSegmenting(false); setFuseRes(null); setFusedEdit(null); setHotNote(null) }   // new beat: close any open draft/edit, end the dwell, drop the segmentation + the stale readback
  if (state.turn.phase !== prevPhase) { setPrevPhase(state.turn.phase); setCommitted(false); setDwell(false); setPendingLabel(null); setPeeking(false) }   // turn rolled over: unlock the menu, end the dwell, drop the optimistic caption, leave review (the new beat takes over)
  // §1.4: when the turn settles, restore a fired custom prompt if it FAILED OPEN (the user's typed sentence
  // is their original work — don't lose it), or clear the stash on a successful landing. In an effect (not the
  // render-time reset above) because it reads/writes a ref, which render must not touch.
  useEffect(() => {
    const p = state.turn.phase
    if (!lastCustom.current) return
    if (p === 'done') lastCustom.current = null
    else if (p === 'skipped' || p === 'error' || p === 'refused') { setOwn(lastCustom.current); lastCustom.current = null }
  }, [state.turn.phase])
  function openDraft() {
    videoRef.current?.pause()                          // freeze the moment so it's tappable (a still to mark up)
    setDraftT(videoRef.current?.currentTime ?? 0)      // clip-less opening still -> t=0
    setDraftTag('hold'); setDraftText(''); setDraftPt(null); setDraftMask(null); setDraftPreview(null)
    setSegmenting(false); setAiming(false); setKbCursor({ x: 0.5, y: 0.5 }); setDraftOpen(true)
  }
  // Place a normalized point AND segment the object under it (ADR-0032). The mask highlight replaces the bare
  // dot when SAM2 succeeds; on any failure (cold/contended GPU, empty/degenerate mask) we keep the point and
  // the note saves as a frame region (fail-open). A re-place supersedes an in-flight segmentation.
  function placeAt(x: number, y: number) {
    setDraftPt({ x, y }); setDraftMask(null); setDraftPreview(null); setSegmenting(true)
    const seq = ++segSeq.current
    segment({ node: sel.id, t: draftT, x, y })
      .then((res) => {
        if (seq !== segSeq.current) return            // a newer place won — ignore this stale result
        setSegmenting(false)
        // gate the saved mask on the PREVIEW (one source of truth): the overlay + aria-live + the saved
        // value must agree — never persist a mask the UI shows/announces as a bare point.
        if (res.ok && res.mask && res.preview) { setDraftMask(res.mask); setDraftPreview(res.preview) }
      })
      .catch(() => { if (seq === segSeq.current) setSegmenting(false) })   // fail-open -> keep the point
  }
  // Map a viewport tap to FRAME-normalized coords using the displayed video content rect (object-fit:contain
  // within the .vid box) — so a tap anywhere on the stage lands on the right pixel; a tap in the letterbox
  // returns null (ignored). Same math as the mediaBox measure, applied to the click.
  function mediaContentPoint(clientX: number, clientY: number) {
    const m = stageRef.current?.querySelector('.vid, .still') as HTMLVideoElement | HTMLImageElement | null
    if (!m) return null
    const b = m.getBoundingClientRect()
    const iw = (m as HTMLVideoElement).videoWidth || (m as HTMLImageElement).naturalWidth || b.width
    const ih = (m as HTMLVideoElement).videoHeight || (m as HTMLImageElement).naturalHeight || b.height
    const scale = Math.min(b.width / iw, b.height / ih)
    const cw = iw * scale, ch = ih * scale
    const x = (clientX - (b.left + (b.width - cw) / 2)) / cw, y = (clientY - (b.top + (b.height - ch) / 2)) / ch
    return (x < 0 || x > 1 || y < 0 || y > 1) ? null : { x, y }
  }
  function placePoint(e: MouseEvent<HTMLDivElement>) {
    const p = mediaContentPoint(e.clientX, e.clientY)
    if (p) placeAt(p.x, p.y)            // a tap in the letterbox margin is ignored (not part of the frame)
  }
  // KEYBOARD/switch path (WCAG 2.1.1): the aim surface is focusable; arrows nudge a crosshair (Shift = coarse),
  // Enter/Space segments at it — so segmentation is never pointer-only. (The chip-based frame-wide note stays
  // an equal first-class route for a note with no point.)
  const [kbCursor, setKbCursor] = useState({ x: 0.5, y: 0.5 })
  const [aiming, setAiming] = useState(false)
  function aimKey(e: KeyboardEvent<HTMLDivElement>) {
    const s = e.shiftKey ? 0.1 : 0.03
    const nudge = (dx: number, dy: number) => {
      e.preventDefault()
      setKbCursor((c) => ({ x: Math.min(1, Math.max(0, c.x + dx)), y: Math.min(1, Math.max(0, c.y + dy)) }))
    }
    if (e.key === 'ArrowLeft') nudge(-s, 0)
    else if (e.key === 'ArrowRight') nudge(s, 0)
    else if (e.key === 'ArrowUp') nudge(0, -s)
    else if (e.key === 'ArrowDown') nudge(0, s)
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); placeAt(kbCursor.x, kbCursor.y) }
  }
  function saveNote() {
    addNote.mutate({ node: sel.id, t: draftT, tag: draftTag, text: draftText.trim() || undefined,
      x: draftPt?.x, y: draftPt?.y,                  // x/y omitted when no point → a frame-wide note (legacy)
      mask: draftMask ?? undefined })                // the precise object silhouette when segmentation landed
    setDraftOpen(false)
  }
  // carry indicator: remember which node a beat was fired from, so while dreaming we can tell the user the
  // next beat is steered by the notes they left on it. State (not a ref): the carry line renders from this,
  // and reading a ref during render is non-reactive (react-hooks/refs). Set once in `fire`, an event handler.
  const [firedFrom, setFiredFrom] = useState<number | null>(null)
  const carryNode = dreaming && firedFrom != null ? nodes.find((n) => n.id === firedFrom) : undefined
  const carryCount = carryNode?.notes?.length ?? 0
  // Beats are HELD per chain tip (lucid_linear.beats_for_tip): the menu is pinned to THIS frame.
  const { data: beatsData, isLoading, isFetching } =
    useBeats(canDream && !dreaming && !revealing, state.session, sel.id, dwell)
  // NO_BEATS (a stable ref), not `= []`: `beats` is a dep of the gutter-link layout effect below, and a
  // fresh `[]` each render would churn that dep → setLinkPaths → re-render → infinite loop (React #185).
  const beats = beatsData ?? NO_BEATS
  const loadingBeats = isLoading || (isFetching && beats.length === 0)
  // ADR-0023: when the dwell opens at a choice moment and the held menu still lacks per-choice previews, ask the
  // server ONCE to render the "potential path" stills — it fills them in progressively (the /api/beats dwell poll
  // swaps each card from the seed still to its own preview). Fired from an effect, not onEnded, so it only runs
  // after the menu has loaded; guarded per-node so a re-render / a poll arrival doesn't re-trigger. Server-side is
  // fully fail-open (cold lease / busy GPU -> no-op), so a miss just leaves the seed still — never an error path.
  // PRIVACY (consult 2026-06-21): only when the user has OPTED IN (path previews are off by default) and NOT for a
  // private dream — the server enforces both too, but we don't even fire the call otherwise (no wasted GPU/heat).
  const previewFiredFor = useRef<number | null>(null)
  useEffect(() => {
    if (!dwell || state.private || !previewsEnabled()) return
    if (!beats.length || !beats.some((b) => !b.preview)) return
    if (previewFiredFor.current === sel.id) return
    previewFiredFor.current = sel.id
    startBeatPreviews.mutate({ node: sel.id })
    // startBeatPreviews is a stable mutation handle (excluded on purpose; adding it would not change behaviour)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dwell, sel.id, beats, state.private])
  // A pick is a commitment: lock the menu the instant it's in flight so a second beat can't fire and the
  // held suggestions don't read as still-choosable. The lock clears when the phase turns over (the poll
  // swaps in the dreaming hero, then a fresh tip rolls new beats) — mirrors the old <Choice> unmount; the
  // unlock now lives in the per-turn reset block above (was a set-state-in-effect on state.turn.phase).
  function showFlash(m: string) { setFlash(m); window.setTimeout(() => setFlash(''), 6000) }
  // `review` (ADR-0023, compose path only): the Shot Card readback the user saw — the EXACT prompt to run
  // (decompose + subject) plus the notes_digest it derived from. When present, the server runs it verbatim
  // (no re-decompose, no subject re-prefix) and the digest catches a stale edit (notes changed since). A
  // curated-beat pick never carries it — that stays a one-tap commitment with the notes folded in server-side.
  async function fire(prompt: string, label: string, review?: { fused_edited: string; notes_digest: string }) {
    setFiredFrom(sel.id)   // remember the node we grew from so the carry line can read its notes count
    setPendingLabel(label && label !== 'custom' ? label : null)   // optimistic caption for the forming hero (cleared when the turn rolls over)
    const notTaken = beats.filter((b) => !(b.label === label && b.prompt === prompt))   // S2: remember the rest
    if (notTaken.length) setGhosts((g) => ({ ...g, [sel.id]: notTaken }))
    try {
      const j = await dream.mutateAsync({ prompt, label, length: len, parent: sel.id, ...(review ?? {}) })
      if (j?.error) showFlash(j.error)
      else { setCommitted(true); branched.current = true }   // jump to the new beat once it generates
    } catch {
      showFlash('Could not reach Lucid — try again.')
    }
  }

  // Dream the composed direction (your words + your notes). When the selected beat carries notes and the
  // Shot Card readback is ready, send that reading (edited or not) so WHAT YOU SAW IS WHAT RUNS; otherwise
  // send the bare typed line and let the server fuse (legacy / readback-not-ready). The fast path — no notes
  // — is unchanged: the typed line fires straight through.
  function composeSubmit() {
    const text = own.trim()
    // block while the reading is recomputing (a note just changed) — a stale edit would hit the server's
    // notes-digest refusal; the readback shows "reading…" in that window (mirrors the Dream-it disable).
    if (!text || composeBusy || (selNotes.length > 0 && fuseBusy)) return
    lastCustom.current = text
    const reading = (selNotes.length > 0 && fuseRes?.ok && fuseRes.notes_digest)
      ? { fused_edited: (fusedEdit ?? fuseRes.fused ?? '').trim(), notes_digest: fuseRes.notes_digest }
      : undefined
    fire(text, 'custom', reading && reading.fused_edited ? reading : undefined)
    setOwn(''); setRefineMsg(null)
  }

  // ↺ reset: drop the edit and let Lucid's reading BLOOM back (re-key the textarea so sc-settle replays),
  // with a brief warm echo so the revert feels safe — undo-as-delight, reduced-motion-safe, zero GPU.
  const resetEchoT = useRef<number | null>(null)
  function resetReading() {
    setFusedEdit(null)
    setReflow((n) => n + 1)
    setResetEcho(true)
    if (resetEchoT.current) window.clearTimeout(resetEchoT.current)
    resetEchoT.current = window.setTimeout(() => setResetEcho(false), 1800)
  }

  // Sharpen the typed idea into a vivid, frame-grounded next beat WITHOUT firing it — the refined text
  // lands back in the same input for one more edit (model proposes; you, and the red-line gate, dispose).
  // Grounded on THIS beat's frame + the dream's premise (node: sel.id). Fails honest: a calm inline reason,
  // and the user's original text is left untouched so a model hiccup never costs them their idea.
  async function doRefine() {
    const text = own.trim()
    if (!text || busy || refine.isPending) return
    setRefineMsg(null)
    try {
      const j = await refine.mutateAsync({ text, node: sel.id })
      if (j?.ok && j.refined) { setOwn(j.refined); setRefineMsg('Refined — edit it, or dream it.'); ownRef.current?.focus() }
      else setRefineMsg(j?.reason || 'Couldn’t refine that — send your own.')
    } catch {
      setRefineMsg('Couldn’t reach Lucid — send your own, or try again.')
    }
  }

  // Download the whole dream as one stitched MP4. Fetched as a blob (not a bare <a href>) so the
  // button can show "Preparing…" and a re-encode that takes a few seconds doesn't read as a no-op —
  // and a second click can't kick off a duplicate stitch. The backend names the file; we mirror it
  // for the blob link. The stitch runs CPU-only server-side, so it never preempts a generating beat.
  async function downloadDream() {
    if (downloading) return
    // A private dream is RAM-only and auto-burned on logout. Saving it to the device crosses that
    // boundary permanently — make it an informed choice, never a silent one (privacy review).
    if (state.private && !window.confirm(
      'This is a private dream. Downloading saves it permanently to your device — outside Lucid’s ' +
      'private storage, and it will not be auto-burned when you log out. Continue?')) return
    setDownloading(true)
    try {
      const res = await fetch(downloadUrl(state.session))
      if (!res.ok) throw new Error(await res.text().catch(() => ''))
      const blob = await res.blob()
      // Guard the failure mode that looks like success: a stale backend (route not loaded yet) returns
      // the SPA index.html with a 200, which would otherwise be saved as a ~1KB "dream.mp4". Only a
      // real video response is a real download — anything else surfaces as an honest error.
      if (!blob.type.startsWith('video/')) throw new Error('not a video response')
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = (state.name || 'dream').replace(/[^\w.-]+/g, '-').replace(/^[-_.]+|[-_.]+$/g, '').slice(0, 60) + '.mp4'
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 10000)
    } catch {
      showFlash('Could not prepare the download — try again in a moment.')
    } finally {
      setDownloading(false)
    }
  }

  // ---- the "what happens next" choices are a HOVER / keyboard-focus affordance ----
  // They no longer auto-appear when a clip ends (the old "dwell" choice-moment read as the panel "showing
  // up a lot without the mouse-over"). A finished clip just loops on `repeat`; the choices reveal on hover
  // or when you tab into them (CSS), and a clip-less opening still shows them (there's nothing to watch).
  // (dwell is cleared on beat/phase change by the per-turn reset block above — not a set-state-in-effect)
  // while choosing, the looping clip plays at 0.1× — a slow, dreamy backdrop behind the choices. Reset to
  // 1× when the dwell ends (a new beat remounts the <video> anyway, but this also covers a same-clip exit).
  useEffect(() => { const v = videoRef.current; if (v) v.playbackRate = dwell ? 0.1 : 1 }, [dwell, sel.id])
  // Drive playback on the STABLE <video> (see its note): when the selected clip changes — a Play-all advance,
  // a beat switch, or a hero finalize swapping in the HD shot — the `src` changes, but autoPlay won't re-fire on
  // an already-played element, so start it here. Keeping ONE element (not a key remount) is what lets Play-all run
  // THROUGH fullscreen without the browser dropping out of it each segment. The clip is muted, so play() is allowed.
  useEffect(() => { const v = videoRef.current; if (v && sel.clip) v.play().catch(() => {}) }, [sel.id, playClip, sel.clip])
  // reveal the gutter choices only when there's nothing to watch (a clip-less still/opening). Otherwise
  // they stay hidden until hover / keyboard-focus (CSS). Mobile stacks them below the clip regardless.
  const choicesRevealed = !sel.clip

  // "Play all" — watch the dream end-to-end along the LIT SPINE (root→tip); advance on each segment's
  // `ended`, wrap if Repeat is on. Sequencing is over playSpine, not the flat clip list, so a branched
  // dream plays its story path in order instead of interleaving abandoned alternate takes.
  function togglePlayAll() {
    if (playAll) return setPlayAll(false)
    if (!playSpine.length) return
    setSelId(playSpine[0].id); setPlayAll(true)
  }
  function onEnded() {
    if (playAll) {
      const i = playSpine.findIndex((n) => n.id === sel.id)
      const next = playSpine[i + 1]
      if (next) setSelId(next.id)
      else if (repeat) setSelId(playSpine[0].id)
      else setPlayAll(false)
    }
    // not playing-all: the clip loops on `repeat` (the loop attr); the choices no longer auto-appear here.
  }
  const pos = playAll ? playSpine.findIndex((n) => n.id === sel.id) + 1 : 0
  const totalSecs = playSpine.reduce((a, n) => a + (n.length ? n.length / FPS : 0), 0)
  const go = (j: number) => { if (nodes[j]) setSelId(nodes[j].id) }

  // ---- moment-tag derivations for the selected node ----
  const selNotes = sel.notes ?? []
  const selDur = sel.length ? sel.length / FPS : 0   // 0 for a still -> no marker track
  const markerLeft = (t: number) => selDur > 0 ? Math.min(100, Math.max(0, (t / selDur) * 100)) : 0
  // the tag button surfaces only on a real, settled node (a clip OR the opening still), never mid-dream
  const canTag = !dreaming && !revealing && !busy && (!!sel.clip || idx === 0)   // !busy: no tap during the fire→dream bridge (ADR-0032)

  // ---- ADR-0023 the Shot Card composite (notes + words → "how Lucid reads this") ----
  // The notes that carry a place (a tapped point/mask): they get a 1-based PIN ①②③ on the frame, echoed as
  // the leading badge of their composite row — a number is the spatial↔textual link (survives a same-hue
  // frame + colour-blindness + a screen reader, unlike a colour). pinOf maps a note id to its pin number.
  const regionNotes = selNotes.filter((n) => n.x != null)
  const pinOf = (id: string) => { const i = regionNotes.findIndex((n) => n.id === id); return i < 0 ? null : i + 1 }
  // a note's effect on the ACTIVE engine, in plain words (never plumbing): on 10eros a placed region ALSO
  // steers the picture (the pixel guide); on wan it only shapes the description; a `hold` anchors either way.
  // Read the LIVE engine first (state.engine), not the engine stamped on the last reading — so a wan↔10eros
  // toggle mid-compose flips the footer/effect copy immediately (the council's engine-toggle honesty seam);
  // the decomposed text itself is engine-independent, so no re-fuse is needed to stay honest.
  const ltx = (state.engine?.active ?? fuseRes?.engine) === '10eros'
  const noteEffect = (n: Note): string =>
    n.tag === 'hold' ? 'grows from here'
      : (n.x != null || n.mask) ? (ltx ? 'steers the picture here' : 'shapes the words')
        : ''
  // debounce the typed line for the fuse trigger (the fuse is a VLM call — never per-keystroke). 900ms is
  // imperceptible against a minutes-long beat; the readback's purpose is review, not live preview.
  useEffect(() => { const id = window.setTimeout(() => setOwnDeb(own), 900); return () => window.clearTimeout(id) }, [own])
  // a stable signature of the selected node's notes — re-fuse when a note is added / removed / edited / re-tapped
  const notesSig = selNotes.map((n) => `${n.id}:${n.tag}:${n.text ?? ''}:${n.t}:${n.x ?? ''},${n.y ?? ''}:${n.mask ?? ''}`).join('|')
  // EAGER readback: when the selected beat carries notes and the choices are up, assemble "how Lucid reads
  // this" so it's ready (or close) by the time the user looks. Re-fires on a note change or a settled typed
  // line. Fail-open: an unreachable narrator returns the deterministic suffix reading (source:'suffix'); any
  // transport error just leaves the last reading. NOT during a dream / reveal (showFutures already gates that).
  useEffect(() => {
    // clearing on no-notes is idempotent (null===null bails out, no cascade); the disable matches the
    // measurement-effect convention already used for the gutter-link layout effect below.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!showFutures || selNotes.length === 0) { setFuseRes(null); setFusedEdit(null); return }
    const seq = ++fuseSeq.current
    setFuseBusy(true)
    fuse({ parent: sel.id, prompt: ownDeb })
      .then((res) => { if (seq !== fuseSeq.current) return; setFuseBusy(false); setFuseRes(res); setFusedEdit(null) })
      .catch(() => { if (seq === fuseSeq.current) setFuseBusy(false) })
    // selId/notesSig/ownDeb drive a re-fuse; showFutures gates it off during a dream. (fuse is a stable import.)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showFutures, selId, notesSig, ownDeb])

  // keep the selected node in view as the story advances (own scroll only; honour reduced-motion).
  useEffect(() => {
    const wrap = treeRef.current
    const el = wrap?.querySelector('.node.cur') as HTMLElement | null
    if (!wrap || !el) return
    const visible = el.offsetLeft >= wrap.scrollLeft && el.offsetLeft + el.clientWidth <= wrap.scrollLeft + wrap.clientWidth
    if (visible) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    wrap.scrollTo({ left: el.offsetLeft - wrap.clientWidth / 2 + el.clientWidth / 2, behavior: reduce ? 'auto' : 'smooth' })
  }, [selId, playAll, showFutures])

  // keyboard transport — Space play/pause, ←/→ step a beat, Home/End jump, P play-all, R repeat.
  // Bails while typing (the compose input lives in the tree) so a custom prompt is never eaten.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (dreaming || revealing) return
      const el = document.activeElement
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return
      const onVideo = el === videoRef.current
      const onButton = el instanceof HTMLButtonElement || el instanceof HTMLAnchorElement
      const pl = nodes.filter((n) => n.clip)
      switch (e.key) {
        case ' ': case 'k': {
          if (onVideo || onButton) return
          const v = videoRef.current; if (!v) return
          e.preventDefault()
          if (v.paused) v.play().catch(() => {}); else v.pause()
          break
        }
        case 'ArrowLeft': if (onVideo) return; if (idx > 0) { e.preventDefault(); setSelId(nodes[idx - 1].id) } break
        case 'ArrowRight': if (onVideo) return; if (idx < nodes.length - 1) { e.preventDefault(); setSelId(nodes[idx + 1].id) } break
        case 'Home': if (onVideo) return; e.preventDefault(); setSelId(nodes[0].id); break
        case 'End': if (onVideo) return; e.preventDefault(); setSelId(tipId); break
        case 'p': case 'P':
          if (playAll) setPlayAll(false)
          else if (pl.length > 1) { setSelId(pl[0].id); setPlayAll(true) }
          break
        case 'r': case 'R': setRepeat((v) => !v); break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nodes, idx, playAll, dreaming, revealing, tipId])

  // ---- the git-graph layout from the parent pointers (history only now) ----
  // Memoized on the node identities + tip: a 2.5s/5s poll that returns an unchanged chain skips the whole
  // recompute (two Maps + a per-node depth walk + a lane visit + edges). A one-pass children Map makes the
  // lane visit O(1)-per-lookup instead of the old O(n) filter-in-recursion (was O(n^2)).
  const { px, py, edges, litSet, dById, maxLane, maxDepth } = useMemo(() => {
    const byId = new Map(nodes.map((n) => [n.id, n] as const))
    const kids = new Map<number, DreamNode[]>()
    for (const n of nodes) { const a = kids.get(n.parent); if (a) a.push(n); else kids.set(n.parent, [n]) }
    for (const a of kids.values()) a.sort((x, y) => x.id - y.id)
    const childrenOf = (id: number) => kids.get(id) ?? []
    const depthOf = (n: DreamNode): number => { let d = 0; let p = byId.get(n.parent); while (p) { d++; p = byId.get(p.parent) } return d }
    const laneById = new Map<number, number>()
    let maxLane = 0, nextLane = 0
    const visit = (n: DreamNode, ln: number) => {
      laneById.set(n.id, ln); if (ln > maxLane) maxLane = ln
      childrenOf(n.id).forEach((k, i) => visit(k, i === 0 ? ln : ++nextLane))
    }
    nodes.filter((n) => !byId.has(n.parent)).forEach((r, i) => visit(r, i === 0 ? 0 : ++nextLane))
    const dById = new Map(nodes.map((n) => [n.id, depthOf(n)] as const))
    const px = (id: number) => X0 + (dById.get(id) ?? 0) * DX
    const py = (id: number) => Y0 + (laneById.get(id) ?? 0) * DY
    const maxDepth = nodes.reduce((m, n) => Math.max(m, dById.get(n.id) ?? 0), 0)
    // lit "checked-out" path = root -> tip (the story's line); everything else is a dim alternate take.
    const litSet = new Set<number>()
    { let t: DreamNode | undefined = byId.get(tipId); while (t) { litSet.add(t.id); t = byId.get(t.parent) } }
    const edges = nodes.flatMap((n) => {
      const p = byId.get(n.parent); if (!p) return []
      const lit = litSet.has(n.id) && litSet.has(p.id)
      const ax = px(p.id) + NW, ay = py(p.id) + NH / 2, bx = px(n.id), by = py(n.id) + NH / 2
      return [{ id: n.id, lit, d: `M${ax} ${ay} C${ax + DX * 0.5} ${ay},${bx - DX * 0.5} ${by},${bx} ${by}` }]
    })
    return { px, py, edges, litSet, dById, maxLane, maxDepth }
  }, [nodes, tipId])

  // the forming node (during dreaming) grows from the selected beat in the tree
  const headX = px(sel.id), headY = py(sel.id)
  const edgeTo = (tx: number, ty: number) => {
    const sx = headX + NW, sy = headY + NH / 2, mx = sx + (tx - sx) * 0.5
    return `M${sx} ${sy} C${mx} ${sy},${mx} ${ty},${tx} ${ty}`
  }
  const forming = dreaming ? { x: headX + DX, y: headY } : null
  const W = Math.max(286, X0 + maxDepth * DX + NW, forming ? forming.x + NW : 0) + 16
  const H = Math.max(NH + 30, Y0 + maxLane * DY + NH + 16, forming ? forming.y + NH + 16 : 0)

  // ---- the gutter choices: futures split left/right beside the portrait clip ----
  const gutterBeats: (Beat & { side: 'L' | 'R' })[] =
    showFutures ? beats.map((b, i) => ({ ...b, side: i % 2 === 0 ? 'L' : 'R' })) : []
  // connector lines from the clip toward each gutter card — DRAW IN ONCE (~700ms) then settle to a static
  // hairline (no infinite march; reuses the dream-tree future-edge grammar). Measured after layout.
  const [linkPaths, setLinkPaths] = useState<{ key: string; d: string }[]>([])
  useLayoutEffect(() => {
    // NO_PATHS (a stable ref) for the empty cases: a fresh `[]` here re-renders even when already empty
    // (Object.is([], []) === false), which is the other half of the #185 loop the old `setLinkPath('')`
    // string state was accidentally immune to. The shared const makes empty→empty a no-op.
    // This is a measurement layout-effect: setState after (or instead of) reading DOM geometry is the
    // documented useLayoutEffect pattern, and NO_PATHS keeps the empty case a true no-op, so it can't cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!showFutures || !gutterBeats.length) { setLinkPaths(NO_PATHS); return }
    function draw() {
      const s = stageRef.current; if (!s) return
      if (window.innerWidth <= 820) { setLinkPaths(NO_PATHS); return }   // narrow: gutters collapse below the clip, no connectors
      const sr = s.getBoundingClientRect()
      const cards = wrapRef.current?.querySelectorAll('.gchoice') ?? []
      if (!cards.length) { setLinkPaths(NO_PATHS); return }
      const cx = sr.width / 2, cy = sr.height / 2
      const paths: { key: string; d: string }[] = []
      cards.forEach((c, i) => {
        const el = c as HTMLElement
        const cr = el.getBoundingClientRect()
        const tx = (cr.left - sr.left) + cr.width / 2, ty = (cr.top - sr.top) + cr.height * 0.42
        const mx = (cx + tx) / 2
        // one path PER card, keyed by its data-gkey, so the hovered card can light its own connector
        paths.push({ key: el.dataset.gkey ?? String(i), d: `M${cx} ${cy} C${mx} ${cy},${mx} ${ty},${tx} ${ty}` })
      })
      setLinkPaths(paths)
    }
    draw()
    // coalesce resize into one layout+measure per frame — getBoundingClientRect over the stage + every
    // card on each raw resize event thrashes layout on a GPU already saturated by ComfyUI + Ollama.
    let raf = 0
    const onResize = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(draw) }
    window.addEventListener('resize', onResize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }
  }, [showFutures, beats, selId, gutterBeats.length])   // gutterBeats.length is derived from showFutures+beats (already deps), so it adds no churn; dropped inert deps: dwell (opacity-only) + nodes.length (only matters via showFutures)

  // hover focus + pointer parallax (shared by every card): enter lights this card's connector (others
  // recede) and caches the rect; move drives the still's parallax via --px/--py; leave resets both.
  // Reduced-motion neutralises the parallax in CSS; the connector focus is colour/opacity only, so it stays.
  const [hotKey, setHotKey] = useState<string | null>(null)
  function cardEnter(e: MouseEvent<HTMLElement>) {
    const el = e.currentTarget; rectCache.set(el, el.getBoundingClientRect()); setHotKey(el.dataset.gkey ?? null)
  }
  function cardMove(e: MouseEvent<HTMLElement>) {
    const el = e.currentTarget; const r = rectCache.get(el); if (!r) return
    el.style.setProperty('--px', ((e.clientX - r.left) / r.width - 0.5).toFixed(3))
    el.style.setProperty('--py', ((e.clientY - r.top) / r.height - 0.5).toFixed(3))
  }
  function cardLeave(e: MouseEvent<HTMLElement>) {
    const el = e.currentTarget; rectCache.delete(el)
    el.style.removeProperty('--px'); el.style.removeProperty('--py'); setHotKey(null)
  }

  function gutterCard(f: Beat & { side: 'L' | 'R' }) {
    // The thumb is this choice's own "potential path" PREVIEW once the dwell worker has rendered it (ADR-0023);
    // until then it's the zero-GPU conditioning still (the frame this beat would continue FROM). Every path
    // starts at the same still, and a card that never gains a preview is a legitimate resting state, not a
    // failure. The eyebrow word-shifts from "a glimpse" to "this path" when the preview lands — a NON-COLOUR
    // ready tell (the a11y cap). Accessible name = the choice itself; the decorative eyebrow and the duplicated
    // visible prompt are hidden from the name so a screen reader doesn't lead with framing text or hear the
    // prompt twice (it was the button's text content AND the `title`). `title` stays a tooltip.
    const hasPreview = !!f.preview
    const src = hasPreview ? previewUrl(f.preview as string) : frameUrl(sel.id, sel.out_frame)
    const ariaName = `${branchingFrom ? 'Branch a new take: ' : ''}${f.label} — ${f.prompt}`
    return (
      <button key={f.label + f.prompt} className="gchoice" disabled={busy} aria-label={ariaName}
        data-gkey={f.label + f.prompt} onMouseEnter={cardEnter} onMouseMove={cardMove} onMouseLeave={cardLeave}
        onClick={() => fire(f.prompt, f.label)} title={f.prompt}>
        {/* key={src} remounts the <img> when the seed→preview swap happens so the bloom-in re-fires cleanly */}
        <span className={'gthumb' + (hasPreview ? ' is-real' : ' is-seed')}>
          <img key={src} src={src} alt="" aria-hidden="true" loading="lazy" /></span>
        <span className="geyebrow" aria-hidden="true">{hasPreview ? 'this path' : 'a glimpse — not the final beat'}</span>
        <span className="glabel">{f.label}</span>
        <span className="gprompt">{f.prompt}</span>
      </button>
    )
  }
  // compose-your-own now lives WITH the curated beats in the gutters (not demoted to the tree) — the two
  // halves of "what happens next" are one surface; it reveals/hides with the choices and stacks on mobile.
  const composeBusy = busy || refine.isPending
  // The Shot Card: with no notes it is exactly the old compose card (the fast path is untouched). When the
  // selected beat carries notes it GROWS — the notes become composite rows (each with its on-frame pin + an
  // honest per-engine effect badge), and a "how Lucid reads this" tier shows the EXACT prompt the next beat
  // will run, editable before you commit (ADR-0023; "model proposes, code disposes" made visible).
  const hasNotes = selNotes.length > 0
  const readbackText = fusedEdit ?? fuseRes?.fused ?? ''
  const readbackEdited = fusedEdit != null && fusedEdit !== (fuseRes?.fused ?? '')
  const composeCard = (
    <div className={'gchoice compose' + (hasNotes ? ' shot-card' : '')} key="__compose" data-gkey="__compose"
      onMouseEnter={cardEnter} onMouseMove={cardMove} onMouseLeave={cardLeave}>
      <span className="geyebrow">{hasNotes ? 'your direction for the next moment' : 'or describe your own'}</span>

      {hasNotes && (
        <ul className="sc-notes" aria-label="Your notes for the next moment">
          {selNotes.map((n) => {
            const pin = pinOf(n.id)
            const eff = noteEffect(n)
            return (
              <li key={n.id} className={'sc-row note-' + n.tag + (hotNote === n.id ? ' lit' : hotNote ? ' dim' : '')}
                onMouseEnter={() => setHotNote(n.id)} onMouseLeave={() => setHotNote(null)}>
                <span className={'sc-pin' + (pin == null ? ' frame' : '')} aria-hidden="true">{pin ?? '▢'}</span>
                <span className="sc-ic" aria-hidden="true" />
                <b className="sc-tag">{tagLabel(n.tag)}</b>
                <span className="sc-t">{n.t.toFixed(1)}s</span>
                {n.text && <span className="sc-txt">{n.text}</span>}
                {eff && <span className="sc-eff" aria-hidden="true">{eff}</span>}
                <button type="button" className="nc-x" disabled={delNote.isPending}
                  aria-label={`Remove note — ${noteSentence(n)}`}
                  onClick={() => delNote.mutate({ node: sel.id, id: n.id })}>×</button>
              </li>
            )
          })}
        </ul>
      )}

      <form className="gc-form" onSubmit={(e) => { e.preventDefault(); composeSubmit() }}>
        <input ref={ownRef} type="text" value={own} placeholder={hasNotes ? 'your words for the next moment…' : 'the next moment…'}
          disabled={composeBusy} aria-label="Compose your own next moment"
          onChange={(e) => { setOwn(e.target.value); if (refineMsg) setRefineMsg(null) }} />
        <div className="gc-actions">
          {/* ✦ Refine: the model PROPOSES a sharper, frame-grounded rewrite of your words; you still edit + dispose.
              Secondary to "Dream it" so the primary commit stays clear. Disabled until there's something to refine.
              While it works it wears a "Sharpening…" label + a light-sweep (.is-sharpening) — honest + aria-busy. */}
          <button className={'future-refine' + (refine.isPending ? ' is-sharpening' : '')} type="button"
            disabled={composeBusy || !own.trim()} onClick={doRefine}
            aria-label="Refine this into a vivid, filmable beat" aria-busy={refine.isPending}
            title="Sharpen this into a vivid, filmable beat — you can still edit it">
            <span className="fr-spark" aria-hidden="true">✦</span>
            <span className="fr-label">{refine.isPending ? 'Sharpening…' : 'Refine'}</span>
          </button>
          {/* while the reading is recomputing (a note just changed), block the commit so a click can't fire a
              stale edit into the server's notes-digest refusal — the readback says "reading…" in that window. */}
          <button className="future-go" type="submit" disabled={composeBusy || (hasNotes && fuseBusy)} aria-label="Dream this">
            <span className="fg-label">Dream it</span><span className="fg-arrow" aria-hidden="true">→</span>
          </button>
        </div>
      </form>
      {refineMsg && <span className="gc-msg" role="status" aria-live="polite">{refineMsg}</span>}

      {/* HOW LUCID READS THIS — the EXACT prompt the next beat will run (notes decomposed + subject folded
          in), editable before you commit. Only appears with notes; mounts as the reading arrives, fail-open
          to the deterministic suffix reading. Editing it sets the run-verbatim text; ↺ restores Lucid's. */}
      {hasNotes && (
        <div className="sc-fuse-wrap">
          <div className={'sc-fuse-k' + (fuseBusy && !readbackText ? ' reading' : '')}>
            <span className="sc-fuse-spark" aria-hidden="true">✦</span>
            {fuseBusy && !readbackText ? 'reading your direction…' : 'how Lucid reads this'}
          </div>
          {/* the reading sits in a relative box so a calm light-sweep can pass over it WHILE Lucid composes
              (the "is being written" cue, reusing the Refine sweep grammar), and the finished text SETTLES
              in rather than popping — keyed on the server reading so a NEW reading re-animates but typing
              (which changes only the edit) never remounts/loses the cursor. Reduced-motion → it just appears. */}
          {/* the settle gesture plays ONLY when the model genuinely composed — the deterministic floor (suffix)
              path gets a plain, instant appearance (.plain), so the gesture means "Lucid wrote this", honestly. */}
          <div className={'sc-fuse-box' + (fuseBusy && !readbackText ? ' is-reading' : '')}>
            {/* keyed on the server reading AND `reflow` — a NEW reading OR a ↺ reset re-mounts the textarea so
                sc-settle replays (Lucid's words bloom back); typing changes only the edit, so it never remounts. */}
            <textarea key={(fuseRes?.fused ?? 'pending') + ':' + reflow}
              className={'sc-fuse' + (fuseRes?.source === 'suffix' ? ' plain' : '')} value={readbackText} disabled={composeBusy}
              rows={3} spellCheck={false} placeholder={fuseBusy ? '' : 'Lucid will compose this when you dream it.'}
              aria-label="How Lucid will make the next moment — edit it, or leave it"
              onChange={(e) => setFusedEdit(e.target.value)} />
            {fuseBusy && !readbackText && <span className="sc-fuse-sweep" aria-hidden="true" />}
          </div>
          <div className="sc-fuse-note" role="status" aria-live="polite">
            {ltx
              ? (regionNotes.length > 0
                ? 'Your marked regions steer the picture; these words set the mood.'
                : 'Your tagged moments guide the picture; these words shape it.')
              : 'Lucid dreams from these words — your tags shape them.'}
            {/* the suffix path is reached on an unreachable narrator OR a red-lined model fusion — so the
                disclosure is cause-agnostic + honest (NOT "offline", which is false on the red-line branch). */}
            {fuseRes && fuseRes.source === 'suffix' && fuseRes.ok && ' (A plain reading — Lucid kept to your exact notes.)'}
            {readbackEdited && (
              <button type="button" className="sc-reset" onClick={resetReading}>↺ reset to Lucid’s reading</button>
            )}
            {resetEcho && !readbackEdited && <span className="sc-reset-echo">↺ back to Lucid’s reading</span>}
          </div>
        </div>
      )}
    </div>
  )

  const treeTitle = dreaming || busy ? 'The dream grows…' : 'Your dream so far'
  const t = state.turn
  // BRIDGE the click→poll gap: the instant a beat is committed (busy), show the forming hero with the
  // clicked label as its caption — so an option click / prompt submit reads as "started", not a no-op.
  const bridging = busy && !dreaming
  const heroCaption = bridging ? pendingLabel : caption

  return (
    <div>
      {flash && <div className="flash" role="alert">{flash}</div>}

      {/* ---- the cinematic player + the on-stage choice gutters ---- */}
      {/* while generating, the forming hero owns the stage — UNLESS the viewer has chosen to peek back at the
          dream so far (reviewWhileDreaming), which renders the player below instead. The reveal handoff
          (`revealing`) always keeps the hero so the poster bloom is never interrupted. */}
      {(dreaming || revealing || busy) && !reviewWhileDreaming ? (
        <DevelopHero caption={heroCaption} posterSrc={revealing ? frameUrl(latest, nodes.find((n) => n.id === latest)?.out_frame) : null}
          onResolved={() => onLatestReady?.()} canReview={playable.length > 0} onReview={() => setPeeking(true)} />
      ) : (
       <div className={'stage-wrap' + (choicesRevealed ? ' revealed' : '')} ref={wrapRef}>
        {/* peeking at the dream so far mid-generation: a pulsing pill back to the still-forming beat */}
        {reviewWhileDreaming && (
          <button type="button" className="peek-back" onClick={() => setPeeking(false)}>
            <span className="pb-spark" aria-hidden="true">✦</span> Still forming — back to it
          </button>
        )}
        <div className={'stage' + (dwell ? ' dwell' : '') + (showFutures ? ' has-choices' : '')} ref={stageRef}>
          <img className="spill" src={frameUrl(sel.id, sel.out_frame)} alt="" aria-hidden="true" />
          <div className="clipwrap">
            {sel.clip ? (
              <video
                // ONE STABLE element (constant key) — never remounted on a Play-all advance or a beat switch.
                // Remounting a <video> that's in FULLSCREEN drops the browser out of fullscreen (and restarts the
                // load), which is why advancing used to minimize fullscreen on every segment. Instead we keep the
                // element and only swap its `src` — the ?id= differs per node, so the URL changes even when two
                // beats share a clip filename (the private-dream collision). A useEffect calls play() on each change
                // (autoPlay only fires once per element), so Play-all advances and plays THROUGH fullscreen.
                key="stage-clip" ref={videoRef} className="vid"
                src={clipUrl(sel.id, playClip)} poster={frameUrl(sel.id, sel.out_frame)}
                aria-label={`Dream clip: ${sel.label || 'opening'}`}
                autoPlay muted playsInline controls
                loop={!playAll && repeat}
                onEnded={onEnded} onLoadedData={onLatestReady} onError={onLatestReady}
                onPlay={() => setPaused(false)} onPause={() => setPaused(true)}
              />
            ) : (
              <img key={sel.out_frame || sel.id} className="still" src={frameUrl(sel.id, sel.out_frame)} alt={sel.label || 'opening frame'} />
            )}
          </div>
          <div className="stage-vig" />
          {/* ADR-0025/0032: while tagging, a tap on the clip segments the OBJECT under it (SAM2) and shows
              its silhouette as a highlight — figure/ground, not hue: a tag-tinted boundary on the object PLUS
              a darkening scrim on everything else (so it reads on any frame), the tag also named in the draft
              chip below. Fail-open: no mask -> the bare point (a frame region). Sits above the video. */}
          {canTag && draftOpen && (
            // The CLICK surface is the WHOLE stage (inset:0) so there are no dead zones; placePoint maps a
            // tap to frame-normalized coords via the video content rect (a tap in the letterbox is ignored).
            // The VISUAL overlay (scrim/mask/dot/cross) lives in an inner box sized to that content rect so
            // it aligns with the clip regardless of any measurement slop.
            <div className="tag-aim" onClick={placePoint} tabIndex={0} role="application"
              onKeyDown={aimKey} onFocus={() => setAiming(true)} onBlur={() => setAiming(false)}
              aria-label="Aim at an object to steer the next beat — click it, or use the arrow keys then Enter">
              <div className="tag-aim-media"
                style={mediaBox ? { left: mediaBox.l + '%', top: mediaBox.t + '%', width: mediaBox.w + '%', height: mediaBox.h + '%' } : { inset: 0 }}>
                {draftPreview && (
                  <>
                    <div className="aim-scrim" />
                    <div className={'aim-mask aim-' + draftTag} aria-hidden="true"
                      style={{ WebkitMaskImage: `url(${draftPreview})`, maskImage: `url(${draftPreview})` }} />
                  </>
                )}
                {draftPt && !draftPreview && (
                  <span className={'aim-dot aim-' + draftTag + (segmenting ? ' seg' : '')}
                    style={{ left: draftPt.x * 100 + '%', top: draftPt.y * 100 + '%' }} />
                )}
                {aiming && !draftPreview && (   /* keyboard aiming crosshair (shown while focused, pre-point) */
                  <span className="aim-cross" aria-hidden="true"
                    style={{ left: kbCursor.x * 100 + '%', top: kbCursor.y * 100 + '%' }} />
                )}
              </div>
            </div>
          )}
          {/* honest, calm status across the segmentation lifecycle — announced to assistive tech */}
          {canTag && draftOpen && (
            <div className="seg-status" role="status" aria-live="polite">
              {segmenting ? 'finding what you tapped…'
                : draftPreview ? 'got it — save the note to keep this'
                : draftPt ? 'couldn’t trace the edges just now — saved where you tapped' : ''}
            </div>
          )}
          {/* existing notes that carry a place: NUMBERED PINS on the frame (①②③…) — the spatial half of the
              spatial↔textual link. The same number leads its composite row in the Shot Card; hovering either
              side lights the pair (hotNote) and dims the rest. Pins are the colour-blind/same-hue-safe link. */}
          {!draftOpen && regionNotes.length > 0 && (
            <div className="aim-marks">
              {regionNotes.map((n, i) => (
                <span key={n.id} className={'aim-pin aim-' + n.tag + (hotNote === n.id ? ' lit' : hotNote ? ' dim' : '')}
                  style={{ left: (n.x as number) * 100 + '%', top: (n.y as number) * 100 + '%' }}
                  onMouseEnter={() => setHotNote(n.id)} onMouseLeave={() => setHotNote(null)}
                  aria-hidden="true">{i + 1}</span>
              ))}
            </div>
          )}
          {/* moment-tag markers: dots along the clip's timeline (spatial feed-forward). Skip on a still. */}
          {selDur > 0 && selNotes.length > 0 && (
            <div className="mk-track" aria-hidden="true">
              {selNotes.map((n) => (
                <span key={n.id} className={'mk-dot mk-' + n.tag} style={{ left: markerLeft(n.t) + '%' }}
                  title={`${n.t.toFixed(1)}s · ${tagLabel(n.tag)}${n.text ? ' — ' + n.text : ''}`} />
              ))}
            </div>
          )}

          {/* connector lines overlay the stage; the choice cards live in the .choices sibling below so they
              can flow BELOW the clip on narrow screens (gutters collapse) instead of cramping beside it */}
          {showFutures && (
            <svg className="gutter-links" width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">
              {linkPaths.map((p) => (
                <path key={p.key} className={'gl-draw' + (hotKey ? (p.key === hotKey ? ' hot' : ' dim') : '')} d={p.d} />
              ))}
            </svg>
          )}
          {/* discoverability cue (fine-pointer desktop only, while the choices are hidden) — without it a
              hidden primary action is a trap. Fades out as the choices reveal. CSS hides it on touch/mobile. */}
          {showFutures && (
            <div className="choices-hint" aria-hidden="true">
              ✦ {branchingFrom ? 'branch a new take' : 'what happens next'} — hover, or when the clip ends
            </div>
          )}

          {(sel.prompt || sel.caption) && (
            <div className="cap">
              <div className="eyebrow">
                <span>{sel.label || 'opening'}</span>
                {sel.rating === 'mature' && <span className="tag tag-mature">mature</span>}
                {isHero && <span className="tag tag-hd" title="This beat has been finalized in HD (the 20-step hero render)">HD</span>}
                {sel.length ? <span style={{ opacity: 0.7 }}>· {fmtDur(sel.length / FPS)}</span> : null}
                {canTag && !draftOpen && (
                  <button type="button" className="tag-btn" disabled={busy} onClick={openDraft}
                    aria-label="Leave a note on this moment to steer the next moment">
                    <span className="ic" aria-hidden="true">✦</span> Note this moment
                  </button>
                )}
                {/* ADR-0040: prompt-guided keyframe edit — direct the action by editing this frame, then animate from it */}
                {canTag && editEnabled && !draftOpen && !editOpen && (
                  <button type="button" className="tag-btn" disabled={busy} onClick={() => setEditOpen(true)}
                    aria-label="Edit this frame with a prompt to direct the action, then animate from it">
                    <span className="ic" aria-hidden="true">✎</span> Edit the action
                  </button>
                )}
                {sel.edited && !editOpen && (
                  <button type="button" className="tag-btn" disabled={busy || dreaming || revert.isPending}
                    onClick={() => revert.mutate({ node: sel.id }, {
                      onSuccess: (r: { reverted?: boolean; reason?: string }) =>
                        showFlash(r && r.reverted ? 'Edit reverted — original restored.' : (r && r.reason) || 'Nothing to revert.'),
                      onError: () => showFlash('Couldn’t revert — try again.'),
                    })}
                    aria-label="Undo the edit to this shot — restore the original">
                    <span className="ic" aria-hidden="true">↺</span> Revert edit
                  </button>
                )}
                {/* ADR-0033: finalize THIS beat in HD — re-renders the same shot on the 20-step lane (minutes). */}
                {canFinalize && (
                  <button type="button" className="tag-btn" onClick={() => hero.mutate({ node: sel.id })}
                    aria-label="Finalize this beat in HD — re-renders the same shot at higher quality (takes a few minutes)">
                    <span className="ic" aria-hidden="true">✦</span> Finalize in HD
                  </button>
                )}
              </div>
              <p className="beat-q">{`“${sel.prompt || sel.caption}”`}</p>
              <span className="stage-ix beat-ix">{idx + 1} / {nodes.length}</span>
              {canTag && draftOpen && (
                <div className="tag-draft" role="group" aria-label="Leave a note on this moment">
                  <div className="tag-draft-head">
                    {sel.clip && (
                      <button type="button" className="tag-play" onClick={togglePlay}
                        aria-label={paused ? 'Play the clip' : 'Pause the clip to mark up this moment'}>
                        <span aria-hidden="true">{paused ? '▶' : '⏸'}</span> {paused ? 'Play' : 'Pause'}
                      </button>
                    )}
                    A note at <b>{draftT.toFixed(1)}s</b> — steers the next moment
                    <span className="aim-hint">{draftPt ? ' · marked ✓ (tap to move it)' : ' · tap what this is about'}</span>
                  </div>
                  <div className="tagchips" role="radiogroup" aria-label="How should it change?">
                    {TAGS.map((o) => (
                      <button key={o.tag} type="button" className={'tagchip' + (draftTag === o.tag ? ' on' : '')}
                        role="radio" aria-checked={draftTag === o.tag} disabled={busy}
                        onClick={() => setDraftTag(o.tag)}>{o.label}</button>
                    ))}
                  </div>
                  {/* the optional text detail reveals (animates in) only AFTER a location is placed —
                      the tap is the primary gesture; the words are about WHAT YOU POINTED AT (ADR-0032) */}
                  {draftPt && (
                    <input type="text" className="tag-text reveal" value={draftText} disabled={busy}
                      placeholder="say more (optional)…" aria-label="Add a detail to this note (optional)"
                      onChange={(e) => setDraftText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveNote() }
                        else if (e.key === 'Escape') { e.preventDefault(); setDraftOpen(false) } }} />
                  )}
                  <div className="tag-draft-row">
                    <button type="button" className="tag-save" disabled={busy} onClick={saveNote}>Save note</button>
                    <button type="button" className="tag-cancel" onClick={() => setDraftOpen(false)}>Cancel</button>
                  </div>
                </div>
              )}
              {canTag && editOpen && (
                <EditPanel node={sel.id} canReplace={sel.id !== 0 && !!sel.clip} disabled={busy}
                  onClose={() => setEditOpen(false)} />
              )}
            </div>
          )}
        </div>

        {/* the choices: side gutters on desktop (overlaid into the stage's empty margins), stacked BELOW
            the clip on mobile where the gutters collapse (responsive — never cramped beside a portrait) */}
        {showFutures && (
          // one labelled group for BOTH gutters — the left/right split is presentational, so a single
          // "what happens next" group is announced (was: only the left gutter was a labelled group).
          <div className={'choices' + (draftOpen ? ' tagging' : '')} role="group"
            aria-label={branchingFrom ? 'Branch a new take — choose a path' : 'What happens next — choose a branch'}>
            {/* MOBILE-only serif lede naming the fork (decorative; the group's aria-label covers AT). CSS shows it
                only in the ≤820px stack, where there's no desktop hover-hint / connector to set the moment. */}
            <p className="choices-lede" aria-hidden="true">{branchingFrom ? 'branch a new take' : 'what happens next'}</p>
            <div className="gutter left">
              {gutterBeats.filter((b) => b.side === 'L').map(gutterCard)}
            </div>
            <div className="gutter right">
              {/* curated beats keep their even vertical spread (.gutter-beats grows to fill the top);
                  compose sits below them in normal flow so a beat card can never overlap the input */}
              <div className="gutter-beats">
                {gutterBeats.filter((b) => b.side === 'R').map(gutterCard)}
              </div>
              {composeCard}
            </div>
            {loadingBeats && <div className="gutter-loading">considering the next moves…</div>}
            {/* honest line for AT — announce when the choices become AVAILABLE (not only at the dwell), the
                screen-reader equivalent of the visual discoverability hint */}
            <div className="sr" role="status" aria-live="polite">
              {dwell
                ? `This beat is finished. ${branchingFrom ? 'Branch a new take' : 'Choose what happens next'} — ${beats.length} option${beats.length === 1 ? '' : 's'}, or compose your own.`
                : beats.length ? `Your next-beat choices are ready — ${beats.length} option${beats.length === 1 ? '' : 's'}, or compose your own.` : ''}
            </div>
          </div>
        )}
       </div>
      )}

      {/* ---- the dream tree: now pure history/branch-map (lit path / dim alternate takes) ---- */}
      <div className="tree">
        {((!dreaming && !revealing) || reviewWhileDreaming) && playable.length > 0 && (
          <div className="player-bar" role="group" aria-label="Playback"
            aria-keyshortcuts="Space ArrowLeft ArrowRight Home End P R">
            <span className="sr" role="status">{playAll ? 'Playing the whole dream' : ''}</span>
            {nodes.length > 1 && (
              <>
                <button className="pbtn" aria-label="Previous beat" disabled={idx <= 0} onClick={() => go(idx - 1)}>
                  <span className="ic" aria-hidden="true">‹</span> Prev
                </button>
                <button className="pbtn" aria-label="Next beat" disabled={idx >= nodes.length - 1} onClick={() => go(idx + 1)}>
                  Next <span className="ic" aria-hidden="true">›</span>
                </button>
              </>
            )}
            {playSpine.length > 1 && (
              <button className={'pbtn' + (playAll ? ' on' : '')} aria-pressed={playAll} onClick={togglePlayAll}>
                <span className="ic" aria-hidden="true">{playAll ? '⏸' : '▶'}</span>
                {playAll ? 'Stop' : 'Play all'}
              </button>
            )}
            <button className={'pbtn' + (repeat ? ' on' : '')} aria-pressed={repeat} onClick={() => setRepeat((v) => !v)}
              title={playAll ? 'Loop the whole dream' : 'Loop this clip'}>
              <span className="ic" aria-hidden="true">🔁</span> Repeat {repeat ? 'on' : 'off'}
            </button>
            <button className="pbtn" onClick={downloadDream} disabled={downloading}
              aria-busy={downloading} aria-label="Download the whole dream as one MP4 video file"
              title="Download the whole dream stitched into one MP4">
              <span className="ic" aria-hidden="true">⤓</span> {downloading ? 'Preparing…' : 'Download'}
            </button>
            {playAll && playSpine.length > 1 && (
              <span className="player-pos" aria-hidden="true">Playing {Math.max(1, pos)} of {playSpine.length}</span>
            )}
          </div>
        )}

        <div className="tree-head">
          <div className="tree-title">{treeTitle}</div>
          {showFutures && !busy && (
            <div className="seglen" style={{ margin: 0 }}>
              <span className="seglen-label">
                Length of the next moment
                <span className="seglen-default"><span aria-hidden="true">✓ </span>your default</span>
              </span>
              <div className="seglen-opts" role="group" aria-label="Next segment length (your pick is remembered as the default)">
                {LENGTHS.map((o) => (
                  <button key={o.f} type="button" className={'lenbtn' + (len === o.f ? ' on' : '')}
                    aria-pressed={len === o.f} disabled={busy} onClick={() => chooseLen(o.f)}>{o.s}</button>
                ))}
              </div>
              <span className="seglen-hint">Sticks as your default · tap any length to change</span>
            </div>
          )}
        </div>
        <div className="tree-note">
          {nodes.length} beat{nodes.length === 1 ? '' : 's'}{totalSecs > 0 ? ` · ${fmtDur(totalSecs)}` : ''}
          {' · click any beat to jump'}
          {showFutures && branchingFrom ? ' — your choices grow from this beat; your other take is kept' : ''}
        </div>


        {/* your notes steering the next moment — the at-a-glance HISTORY view. Hidden while composing
            (!showFutures): the Shot Card in the gutter already shows them as composable rows, so this would
            duplicate. Shown when reviewing a beat without composing, so a node's notes are never invisible. */}
        {!dreaming && !revealing && !showFutures && selNotes.length > 0 && (
          <div className="notes-row" role="group" aria-label="Your notes steering the next moment">
            <span className="notes-k">Your notes for the next moment</span>
            <ul className="notes-list">
              {selNotes.map((n) => (
                <li key={n.id} className={'notechip note-' + n.tag}>
                  <span className="nc-t">{n.t.toFixed(1)}s</span>
                  <b className="nc-tag">{tagLabel(n.tag)}</b>
                  {n.text && <span className="nc-text">{n.text}</span>}
                  <button type="button" className="nc-x" disabled={delNote.isPending}
                    aria-label={`Remove note — ${noteSentence(n)}, at ${n.t.toFixed(1)} seconds`}
                    onClick={() => delNote.mutate({ node: sel.id, id: n.id })}>×</button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* carry indicator: the dream now forming is steered by the notes left on the moment it grew from */}
        {dreaming && carryCount > 0 && (
          <div className="notes-carry" role="status">
            Guided by your {carryCount} note{carryCount === 1 ? '' : 's'} on the last moment
          </div>
        )}

        {/* S2 — the paths you didn't take from THIS beat (only once you've branched from it), kept faint
            and one click from blooming. Nothing you considered is lost. */}
        {!dreaming && !revealing && (ghosts[sel.id]?.length ?? 0) > 0 && nodes.some((n) => n.parent === sel.id) && (
          <div className="ghosts" role="group" aria-label="Paths you didn't take from this beat">
            <span className="ghosts-k">Paths not taken from here</span>
            <ul className="ghosts-list">
              {ghosts[sel.id].map((gb) => (
                <li key={gb.label + gb.prompt}>
                  <button type="button" className="ghostchip" disabled={busy} title={gb.prompt}
                    onClick={() => fire(gb.prompt, gb.label)}>
                    <span className="gh-dot" aria-hidden="true" />
                    <b>{gb.label}</b>
                    <span className="gh-bloom" aria-hidden="true">bloom →</span>
                    <span className="sr">— grow this path you didn't take</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* honest outcome of the last turn, inline (folded in from <Choice>) */}
        {/* §1.3: surface the outcome where the user FIRED FROM, not only at the tip — a branch-fire fail-open
            (sel.id !== tipId) used to snap back silently. §2.1/1.1: render the server's HONEST reason (t.error)
            when present (a structural "another app holds the GPU" / an OOM / a backend-down), not a false calm. */}
        {(atHead || sel.id === firedFrom) && t.phase === 'skipped' && <div className="banner">{t.error || 'That beat was skipped — the graphics card was needed elsewhere. Your desktop is untouched.'} Nothing was lost.</div>}
        {(atHead || sel.id === firedFrom) && t.phase === 'error' && <div className="banner bad">{t.error || "That clip didn't come through."} Your desktop is untouched.</div>}
        {(atHead || sel.id === firedFrom) && t.phase === 'refused' && <div className="banner">That direction isn't something Lucid can make. Try a different turn.</div>}
        {!canDream && atHead && <div className="note" style={{ margin: '4px 0 10px' }}>Choosing what happens next switches on once everything above is ready.</div>}

        <div className="tree-scroll" ref={treeRef} role="group" aria-label="Dream tree — click a beat to jump">
          <div className="tree-canvas" style={{ width: W, height: H }}>
            <svg className="tree-edges" width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
              {edges.map((e) => (
                <path key={e.id} d={e.d} fill="none" strokeLinecap="round"
                  stroke={e.lit ? 'var(--inst-blue)' : 'rgba(138,144,160,.32)'}
                  strokeWidth={e.lit ? 2.2 : 1.3} strokeDasharray={e.lit ? undefined : '3 7'} />
              ))}
              {forming && (
                <path className="flow" d={edgeTo(forming.x, forming.y + NH / 2)} fill="none"
                  stroke="#8A6BDC" strokeWidth={1.7} strokeDasharray="3 7" strokeLinecap="round" />
              )}
            </svg>

            {nodes.map((n) => {
              const lit = litSet.has(n.id), cur = n.id === sel.id
              return (
                <button key={n.id} className={'node' + (cur ? ' cur' : lit ? ' lit' : ' alt')}
                  style={{ left: px(n.id), top: py(n.id) }}
                  onClick={() => { setSelId(n.id); if ((dreaming || busy) && n.clip) setPeeking(true) }}  // tap a beat mid-dream → review it
                  aria-current={cur ? 'true' : undefined}
                  title={n.prompt || n.caption || n.label || 'opening'}>
                  <span className="cell">
                    <img src={frameUrl(n.id, n.out_frame)} alt={n.label || 'frame'} loading="lazy" />
                    {cur && <span className="tri"><i /></span>}
                    {n.rating === 'mature' && <span className="mat" title="mature" />}
                  </span>
                  <span className="lbl">{String((dById.get(n.id) ?? 0) + 1).padStart(2, '0')}</span>
                </button>
              )
            })}

            {forming && (
              <div className="node" style={{ left: forming.x, top: forming.y }} aria-hidden="true">
                <span className="cell forming" /><span className="lbl">··</span>
              </div>
            )}
          </div>
        </div>

        {!atHead && !dreaming && !revealing && (
          <div className="lookback">
            {branchingFrom ? `Branching from beat ${idx + 1} — your latest take is kept.` : `Beat ${idx + 1}.`}
            <button className="latest" onClick={() => setSelId(tipId)}>Latest →</button>
          </div>
        )}
        {busy && !flash && <div className="note" role="status" style={{ marginTop: 10 }}>✦ starting this beat…</div>}
      </div>
    </div>
  )
}
