import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import type { DreamNode, LucidState, Beat, Note } from './api'
import { clipUrl, frameUrl, useBeats, useDream, useAddNote, useDeleteNote } from './api'

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

// ADR-0023 spatial feed-forward: the four moment-tag intents. `hold` is the steering primitive (anchor the
// next beat on this exact moment) so it leads; the others nudge direction. Order = how the chips are offered.
const TAGS: { tag: Note['tag']; label: string }[] = [
  { tag: 'hold', label: 'Hold here' },
  { tag: 'more', label: 'More like this' },
  { tag: 'less', label: 'Less of this' },
  { tag: 'change', label: 'Change this' },
]
const tagLabel = (t: Note['tag']) => TAGS.find((x) => x.tag === t)?.label ?? t

// next-segment length options (frames @16fps -> seconds). Mirrors lucid_engine MIN_LEN/MAX_LEN; the
// engine clamps anything off-list, so this is purely the UI offer (folded in from the old <Choice>).
const LENGTHS: { f: number; s: string }[] = [
  { f: 17, s: '1s' }, { f: 33, s: '2s' }, { f: 49, s: '3s' }, { f: 65, s: '4s' }, { f: 81, s: '5s' },
]

// The develop hero: the aurora forms while the GPU generates, then — when the finished frame arrives
// (`posterSrc`) — that frame blooms IN over the aurora, in the SAME box, under the held serif caption,
// and `onResolved` fires after the dissolve so the real <video> takes over in place (ADR-0014's "the
// clip developing"). A child component so its `posterIn` state re-arms by unmounting between beats.
function DevelopHero({ caption, posterSrc, onResolved }:
  { caption: string | null; posterSrc: string | null; onResolved: () => void }) {
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
  const [selId, setSelId] = useState<number>(latest)
  const [playAll, setPlayAll] = useState(false)
  const [repeat, setRepeat] = useState(true)
  const [dwell, setDwell] = useState(false)   // clip has played once at a choice moment → choices available + the clip LOOPS while you choose
  const treeRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)   // stage + choices; the connector measure spans both
  const videoRef = useRef<HTMLVideoElement>(null)

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
  // futures grow from the SELECTED beat: at the tip it's "continue", at an earlier beat it's "branch a new take"
  const showFutures = !dreaming && !revealing && canDream
  const branchingFrom = sel.id !== tipId
  const dream = useDream()
  const [own, setOwn] = useState('')
  const [len, setLen] = useState(33)            // default ~2s, matches lucid_engine DEFAULT_LEN
  const [flash, setFlash] = useState('')
  const [committed, setCommitted] = useState(false)
  // the just-committed beat's label — shown as the forming hero's caption to BRIDGE the gap between the click
  // and the 2.5–5s poll flipping the server turn to 'dreaming' (without it, a click read as a no-op).
  const [pendingLabel, setPendingLabel] = useState<string | null>(null)
  const busy = dream.isPending || committed
  // ADR-0023 council S2 ("the path you didn't take is remembered, never wasted"): when you pick a beat,
  // the options you DIDN'T pick are stashed per node — so a branched-from beat keeps them, one click from
  // blooming. Reversibility (ADR-0005) made generative. Zero-GPU (a label + the on-disk conditioning still).
  // Session-scoped for now; durable persistence is a small tree.json field (backend follow-up).
  const [ghosts, setGhosts] = useState<Record<number, Beat[]>>({})

  // ---- ADR-0023 moment tags (spatial feed-forward): annotate THIS clip; steers the next beat ----
  const addNote = useAddNote()
  const delNote = useDeleteNote()
  // the inline tag draft: the captured time, the selected intent (defaults to the `hold` primitive), and
  // optional text. `draftOpen` toggles the panel; closing on a node change keeps it pinned to one moment.
  const [draftOpen, setDraftOpen] = useState(false)
  const [draftT, setDraftT] = useState(0)
  const [draftTag, setDraftTag] = useState<Note['tag']>('hold')
  const [draftText, setDraftText] = useState('')
  // ADR-0025: an OPTIONAL spatial point (normalized 0..1 over the clip) saying WHERE the tag applies. null
  // = a frame-wide note (legacy). Placed by tapping the clip while the draft is open; the engine turns it
  // into a soft-disc attention mask. Radius is the server default — one tap is the whole gesture.
  const [draftPt, setDraftPt] = useState<{ x: number; y: number } | null>(null)
  // Reset transient per-beat / per-turn UI when the selected beat or the turn phase changes — adjusted
  // DURING render (React-docs "you might not need an effect"), not via a set-state-in-effect that cascades
  // an extra render. Each guard flips its own prev-tracker so it fires once per change, and every target is
  // idempotent (draft closed / dwell off / menu unlocked), so there's no loop. (phase covers `dreaming`,
  // which is just phase === 'dreaming'.)
  const [prevSelId, setPrevSelId] = useState(sel.id)
  const [prevPhase, setPrevPhase] = useState(state.turn.phase)
  if (sel.id !== prevSelId) { setPrevSelId(sel.id); setDraftOpen(false); setDwell(false) }                       // new beat: close any open draft, end the dwell
  if (state.turn.phase !== prevPhase) { setPrevPhase(state.turn.phase); setCommitted(false); setDwell(false); setPendingLabel(null) }   // turn rolled over: unlock the menu, end the dwell, drop the optimistic caption
  function openDraft() {
    setDraftT(videoRef.current?.currentTime ?? 0)      // clip-less opening still -> t=0
    setDraftTag('hold'); setDraftText(''); setDraftPt(null); setDraftOpen(true)
  }
  // place the spatial point from a tap on the clip overlay — normalized to the overlay's box, clamped in.
  function placePoint(e: MouseEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))
    setDraftPt({ x, y })
  }
  function saveNote() {
    addNote.mutate({ node: sel.id, t: draftT, tag: draftTag, text: draftText.trim() || undefined,
      x: draftPt?.x, y: draftPt?.y })   // x/y omitted when no point → a frame-wide note (legacy)
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
    useBeats(canDream && !dreaming && !revealing, state.session, sel.id)
  // NO_BEATS (a stable ref), not `= []`: `beats` is a dep of the gutter-link layout effect below, and a
  // fresh `[]` each render would churn that dep → setLinkPaths → re-render → infinite loop (React #185).
  const beats = beatsData ?? NO_BEATS
  const loadingBeats = isLoading || (isFetching && beats.length === 0)
  // A pick is a commitment: lock the menu the instant it's in flight so a second beat can't fire and the
  // held suggestions don't read as still-choosable. The lock clears when the phase turns over (the poll
  // swaps in the dreaming hero, then a fresh tip rolls new beats) — mirrors the old <Choice> unmount; the
  // unlock now lives in the per-turn reset block above (was a set-state-in-effect on state.turn.phase).
  function showFlash(m: string) { setFlash(m); window.setTimeout(() => setFlash(''), 6000) }
  async function fire(prompt: string, label: string) {
    setFiredFrom(sel.id)   // remember the node we grew from so the carry line can read its notes count
    setPendingLabel(label && label !== 'custom' ? label : null)   // optimistic caption for the forming hero (cleared when the turn rolls over)
    const notTaken = beats.filter((b) => !(b.label === label && b.prompt === prompt))   // S2: remember the rest
    if (notTaken.length) setGhosts((g) => ({ ...g, [sel.id]: notTaken }))
    try {
      const j = await dream.mutateAsync({ prompt, label, length: len, parent: sel.id })
      if (j?.error) showFlash(j.error)
      else { setCommitted(true); branched.current = true }   // jump to the new beat once it generates
    } catch {
      showFlash('Could not reach Lucid — try again.')
    }
  }

  // ---- the choice moment: a selected clip plays once clean, then (on `ended`) the choices appear and the
  // clip LOOPS gently behind them — a living backdrop while you choose, not a frozen frame (user's call,
  // overriding the council's frozen-dwell-with-a-breath; the Wan last≠first-frame hard-cut is accepted as
  // the price of motion). EVERY choosable beat is a choice moment, not just the tip: an earlier beat loops
  // into "branch from here" just as the tip loops into "continue".
  const choiceMoment = !!sel.clip && showFutures
  // (dwell is cleared on beat/phase change by the per-turn reset block above — not a set-state-in-effect)
  // while choosing, the looping clip plays at 0.1× — a slow, dreamy backdrop behind the choices. Reset to
  // 1× when the dwell ends (a new beat remounts the <video> anyway, but this also covers a same-clip exit).
  useEffect(() => { const v = videoRef.current; if (v) v.playbackRate = dwell ? 0.1 : 1 }, [dwell, sel.id])
  // desktop: keep the clip clean WHILE it plays — reveal the gutter choices when there's nothing to watch
  // (a still/opening) or when the clip has ended (the dwell, = the choice moment). Hover/keyboard-focus also
  // reveal (CSS). Mobile stacks the choices below the clip, so they stay visible there regardless.
  const choicesRevealed = !sel.clip || dwell

  // "Play all" — watch the dream end-to-end; advance on each segment's `ended`, wrap if Repeat is on.
  function togglePlayAll() {
    if (playAll) return setPlayAll(false)
    if (!playable.length) return
    setSelId(playable[0].id); setPlayAll(true)
  }
  function onEnded() {
    if (playAll) {
      const i = playable.findIndex((n) => n.id === sel.id)
      const next = playable[i + 1]
      if (next) setSelId(next.id)
      else if (repeat) setSelId(playable[0].id)
      else setPlayAll(false)
      return
    }
    if (choiceMoment) {
      setDwell(true)                             // the beat finished and a choice is waiting → choices appear...
      videoRef.current?.play().catch(() => {})   // ...and the clip LOOPS gently behind them (not a frozen frame)
    }
  }
  const pos = playAll ? playable.findIndex((n) => n.id === sel.id) + 1 : 0
  const totalSecs = playable.reduce((a, n) => a + (n.length ? n.length / FPS : 0), 0)
  const go = (j: number) => { if (nodes[j]) setSelId(nodes[j].id) }

  // ---- moment-tag derivations for the selected node ----
  const selNotes = sel.notes ?? []
  const selDur = sel.length ? sel.length / FPS : 0   // 0 for a still -> no marker track
  const markerLeft = (t: number) => selDur > 0 ? Math.min(100, Math.max(0, (t / selDur) * 100)) : 0
  // the tag button surfaces only on a real, settled node (a clip OR the opening still), never mid-dream
  const canTag = !dreaming && !revealing && (!!sel.clip || idx === 0)

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
    // default thumb = the zero-GPU conditioning still (the frame this beat would continue FROM). Every path
    // starts here; the motion glimpse that differentiates them is a gated, opportunistic maybe (council brief).
    // Accessible name = the choice itself, with the branch-vs-continue intent spoken; the decorative eyebrow
    // and the duplicated visible prompt are hidden from the name so a screen reader doesn't lead with framing
    // text or hear the prompt twice (it was the button's text content AND the `title`). `title` stays a tooltip.
    const ariaName = `${branchingFrom ? 'Branch a new take: ' : ''}${f.label} — ${f.prompt}`
    return (
      <button key={f.label + f.prompt} className="gchoice" disabled={busy} aria-label={ariaName}
        data-gkey={f.label + f.prompt} onMouseEnter={cardEnter} onMouseMove={cardMove} onMouseLeave={cardLeave}
        onClick={() => fire(f.prompt, f.label)} title={f.prompt}>
        <span className="gthumb"><img src={frameUrl(sel.id, sel.out_frame)} alt="" aria-hidden="true" loading="lazy" /></span>
        <span className="geyebrow" aria-hidden="true">a glimpse — not the final beat</span>
        <span className="glabel">{f.label}</span>
        <span className="gprompt">{f.prompt}</span>
      </button>
    )
  }
  // compose-your-own now lives WITH the curated beats in the gutters (not demoted to the tree) — the two
  // halves of "what happens next" are one surface; it reveals/hides with the choices and stacks on mobile.
  const composeCard = (
    <div className="gchoice compose" key="__compose" data-gkey="__compose"
      onMouseEnter={cardEnter} onMouseMove={cardMove} onMouseLeave={cardLeave}>
      <span className="geyebrow">or describe your own</span>
      <form className="gc-form"
        onSubmit={(e) => { e.preventDefault(); if (own.trim() && !busy) { fire(own.trim(), 'custom'); setOwn('') } }}>
        <input type="text" value={own} placeholder="the next moment…" disabled={busy}
          aria-label="Compose your own next moment" onChange={(e) => setOwn(e.target.value)} />
        <button className="future-go" type="submit" disabled={busy} aria-label="Dream this">→</button>
      </form>
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
      {dreaming || revealing || busy ? (
        <DevelopHero caption={heroCaption} posterSrc={revealing ? frameUrl(latest, nodes.find((n) => n.id === latest)?.out_frame) : null}
          onResolved={() => onLatestReady?.()} />
      ) : (
       <div className={'stage-wrap' + (choicesRevealed ? ' revealed' : '')} ref={wrapRef}>
        <div className={'stage' + (dwell ? ' dwell' : '') + (showFutures ? ' has-choices' : '')} ref={stageRef}>
          <img className="spill" src={frameUrl(sel.id, sel.out_frame)} alt="" aria-hidden="true" />
          <div className="clipwrap">
            {sel.clip ? (
              <video
                key={sel.clip ?? sel.id} ref={videoRef} className="vid"
                src={clipUrl(sel.id, sel.clip)} poster={frameUrl(sel.id, sel.out_frame)}
                aria-label={`Dream clip: ${sel.label || 'opening'}`}
                autoPlay muted playsInline controls
                loop={!playAll && (dwell || (repeat && !choiceMoment))}
                onEnded={onEnded} onLoadedData={onLatestReady} onError={onLatestReady}
              />
            ) : (
              <img key={sel.out_frame || sel.id} className="still" src={frameUrl(sel.id, sel.out_frame)} alt={sel.label || 'opening frame'} />
            )}
          </div>
          <div className="stage-vig" />
          {/* ADR-0025: while tagging, an overlay turns a tap on the clip into the note's spatial point
              (optional — skip it for a frame-wide note). Sits above the video, below the caption/draft. */}
          {canTag && draftOpen && (
            <div className="tag-aim" onClick={placePoint}
              aria-label="Tap where on the frame this tag applies (optional)">
              {draftPt && (
                <span className={'aim-dot aim-' + draftTag}
                  style={{ left: draftPt.x * 100 + '%', top: draftPt.y * 100 + '%' }} />
              )}
            </div>
          )}
          {/* existing notes that carry a point: faint dots on the frame (in addition to the timeline track) */}
          {!draftOpen && selNotes.some((n) => n.x != null) && (
            <div className="aim-marks" aria-hidden="true">
              {selNotes.filter((n) => n.x != null).map((n) => (
                <span key={n.id} className={'aim-dot static aim-' + n.tag}
                  style={{ left: (n.x as number) * 100 + '%', top: (n.y as number) * 100 + '%' }} />
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
                {sel.length ? <span style={{ opacity: 0.7 }}>· {fmtDur(sel.length / FPS)}</span> : null}
                {canTag && !draftOpen && (
                  <button type="button" className="tag-btn" disabled={busy} onClick={openDraft}
                    aria-label="Tag a moment in this clip to steer the next beat">
                    <span className="ic" aria-hidden="true">✦</span> Tag a moment
                  </button>
                )}
              </div>
              <p className="beat-q">{`“${sel.prompt || sel.caption}”`}</p>
              <span className="stage-ix beat-ix">{idx + 1} / {nodes.length}</span>
              {canTag && draftOpen && (
                <div className="tag-draft" role="group" aria-label="Tag this moment">
                  <div className="tag-draft-head">
                    Tag at <b>{draftT.toFixed(1)}s</b> — steers the next beat
                    <span className="aim-hint">{draftPt ? ' · point set ✓ (tap to move)' : ' · tap the clip to point (optional)'}</span>
                  </div>
                  <div className="tagchips" role="radiogroup" aria-label="What kind of note">
                    {TAGS.map((o) => (
                      <button key={o.tag} type="button" className={'tagchip' + (draftTag === o.tag ? ' on' : '')}
                        role="radio" aria-checked={draftTag === o.tag} disabled={busy}
                        onClick={() => setDraftTag(o.tag)}>{o.label}</button>
                    ))}
                  </div>
                  <input type="text" className="tag-text" value={draftText} disabled={busy}
                    placeholder="add a detail (optional)…" aria-label="Optional note detail"
                    onChange={(e) => setDraftText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveNote() }
                      else if (e.key === 'Escape') { e.preventDefault(); setDraftOpen(false) } }} />
                  <div className="tag-draft-row">
                    <button type="button" className="tag-save" disabled={busy} onClick={saveNote}>Save tag</button>
                    <button type="button" className="tag-cancel" onClick={() => setDraftOpen(false)}>Cancel</button>
                  </div>
                </div>
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
        {!dreaming && !revealing && playable.length > 0 && (
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
            {playable.length > 1 && (
              <button className={'pbtn' + (playAll ? ' on' : '')} aria-pressed={playAll} onClick={togglePlayAll}>
                <span className="ic" aria-hidden="true">{playAll ? '⏸' : '▶'}</span>
                {playAll ? 'Stop' : 'Play all'}
              </button>
            )}
            <button className={'pbtn' + (repeat ? ' on' : '')} aria-pressed={repeat} onClick={() => setRepeat((v) => !v)}
              title={playAll ? 'Loop the whole dream' : 'Loop this clip'}>
              <span className="ic" aria-hidden="true">🔁</span> Repeat {repeat ? 'on' : 'off'}
            </button>
            {playAll && playable.length > 1 && (
              <span className="player-pos" aria-hidden="true">Playing {Math.max(1, pos)} of {playable.length}</span>
            )}
          </div>
        )}

        <div className="tree-head">
          <div className="tree-title">{treeTitle}</div>
          {showFutures && !busy && (
            <div className="seglen" style={{ margin: 0 }}>
              <span className="seglen-label">Length of the next moment</span>
              <div className="seglen-opts" role="group" aria-label="Next segment length">
                {LENGTHS.map((o) => (
                  <button key={o.f} type="button" className={'lenbtn' + (len === o.f ? ' on' : '')}
                    aria-pressed={len === o.f} disabled={busy} onClick={() => setLen(o.f)}>{o.s}</button>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="tree-note">
          {nodes.length} beat{nodes.length === 1 ? '' : 's'}{totalSecs > 0 ? ` · ${fmtDur(totalSecs)}` : ''}
          {' · click any beat to jump'}
          {showFutures && branchingFrom ? ' — your choices grow from this beat; your other take is kept' : ''}
        </div>


        {/* moment tags steering the NEXT beat grown from this node (spatial feed-forward) */}
        {!dreaming && !revealing && selNotes.length > 0 && (
          <div className="notes-row" role="group" aria-label="Moment tags steering the next beat">
            <span className="notes-k">Notes → next beat</span>
            <ul className="notes-list">
              {selNotes.map((n) => (
                <li key={n.id} className={'notechip note-' + n.tag}>
                  <span className="nc-t">{n.t.toFixed(1)}s</span>
                  <b className="nc-tag">{tagLabel(n.tag)}</b>
                  {n.text && <span className="nc-text">{n.text}</span>}
                  <button type="button" className="nc-x" disabled={delNote.isPending}
                    aria-label={`Remove the ${tagLabel(n.tag)} note at ${n.t.toFixed(1)} seconds`}
                    onClick={() => delNote.mutate({ node: sel.id, id: n.id })}>×</button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* carry indicator: the dream now forming is steered by the notes left on the node it grew from */}
        {dreaming && carryCount > 0 && (
          <div className="notes-carry" role="status">
            Guided by your {carryCount} note{carryCount === 1 ? '' : 's'} on the last beat
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
        {atHead && t.phase === 'skipped' && <div className="banner">That beat was skipped — the graphics card was needed elsewhere, so the dream fails open and your desktop is untouched. Choose again when you're ready.</div>}
        {atHead && t.phase === 'error' && <div className="banner bad">That clip didn't come through — your desktop is untouched. Try again.</div>}
        {atHead && t.phase === 'refused' && <div className="banner">That direction isn't something Lucid can make. Try a different turn.</div>}
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
                  style={{ left: px(n.id), top: py(n.id) }} onClick={() => setSelId(n.id)}
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
