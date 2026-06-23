import { useCallback, useEffect, useRef, useState } from 'react'
import { useLucidState } from './api'
import type { LucidState, TurnPhase } from './api'
import { ReadinessCard, ReadyChip, PrivateCard, LibraryCard, EngineToggle, PreviewToggle, Nav } from './components'
import { DreamGallery, StashPanel } from './Library'
import QueuePanel from './QueuePanel'
import Start from './Start'
import Chain from './Chain'
import Dreaming from './Dreaming'

type View = 'dream' | 'new' | 'library' | 'stash' | 'settings'
const VIEWS: View[] = ['dream', 'new', 'library', 'stash', 'settings']

// the section currently named in the URL hash (e.g. #/library), or null to follow the live dream.
function viewFromHash(): View | null {
  const h = (typeof window !== 'undefined' ? window.location.hash : '').replace(/^#\/?/, '')
  return (VIEWS as string[]).includes(h) ? (h as View) : null
}

// One honest line for assistive tech — the autonomous transitions (dreaming started/finished/skipped)
// are the whole point of the surface, so they must be announced, not just drawn.
function announce(s: LucidState): string {
  const t = s.turn
  if (t.phase === 'dreaming') return 'Dreaming this beat — a few minutes.'
  if (t.phase === 'skipped') return 'That beat was skipped; your desktop is untouched.'
  if (t.phase === 'error') return 'That clip did not come through.'
  if (t.phase === 'refused') return "That direction isn't something Lucid can make."
  if (!s.readiness.can_dream) return 'Cannot dream right now.'
  if (s.chain) return `Ready — ${s.chain.nodes.length} frame${s.chain.nodes.length === 1 ? '' : 's'} so far.`
  return 'Ready to start a dream.'
}

export default function App() {
  const { data: s, isLoading, isError } = useLucidState()

  // --- reveal hold ---------------------------------------------------------------------------------
  // The server flips a turn to `done` the instant the clip is written, but the browser still has to
  // fetch + paint that multi-MB mp4. If we swap the loading indicator for the next options on `done`
  // alone, the Ollama choices flash in over a still-buffering segment. So we HOLD the loading
  // indication past `done` until the freshly generated clip is actually on screen (Chain reports it
  // via onLatestReady) — only then does the next /api/beats roll fire.
  const phase: TurnPhase | undefined = s?.turn.phase
  const tip = s?.chain ? s.chain.nodes[s.chain.nodes.length - 1] : undefined
  const tipId = tip?.id ?? -1
  const [revealTip, setRevealTip] = useState<number | null>(null)
  const [prevPhase, setPrevPhase] = useState<TurnPhase | undefined>(undefined)
  if (phase !== prevPhase) {                    // phase transition, derived during render → no flash of <Choice>
    setPrevPhase(phase)
    if (prevPhase === 'dreaming' && phase === 'done' && tip?.clip) {
      setRevealTip(tipId)                       // a beat just finished: hold until its clip is displayed
    } else if (phase === 'dreaming') {
      setRevealTip(null)                        // a new beat started: drop any stale reveal hold
    }
  }
  const revealing = revealTip !== null && revealTip === tipId
  const clearReveal = useCallback(() => setRevealTip(null), [])
  useEffect(() => {                             // fallback: never strand the user on a clip that won't load
    if (revealTip === null) return
    const id = window.setTimeout(clearReveal, 12000)
    return () => window.clearTimeout(id)
  }, [revealTip, clearReveal])

  // --- section navigation (ADR-0028) ---------------------------------------------------------------
  // One persistent menu drives a section `view`, mirrored to the URL hash so sections are deep-linkable
  // and the browser Back button works. `null` (no hash) = follow the dream: land on the live dream if
  // there is one, else on New. An explicit choice sticks until the dream changes under it (e.g. the
  // current dream is deleted -> we fall back off the now-empty 'dream' view). Starting or opening a
  // dream returns to it.
  const [view, setView] = useState<View | null>(viewFromHash)
  const navigate = useCallback((v: View | null) => {
    setView(v)
    try { window.location.hash = v ? `/${v}` : '' } catch { /* no-op */ }
  }, [])
  useEffect(() => {                             // browser Back / manual hash edits drive the view too
    const onHash = () => setView(viewFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const hasDream = !!s?.chain
  let active: View = view ?? (hasDream ? 'dream' : 'new')
  if (active === 'dream' && !hasDream) active = 'new'
  const goDream = useCallback(() => navigate('dream'), [navigate])
  // On a SECTION change, move focus to that view's heading so screen readers announce the new section
  // and keyboard focus continues inside it (not stranded on the nav). Skip the first render so we don't
  // steal focus on initial load. The cinematic dream view has no managed heading (query returns null).
  const navFirst = useRef(true)
  useEffect(() => {
    if (navFirst.current) { navFirst.current = false; return }
    const h = document.querySelector('[data-view-heading]') as HTMLElement | null
    h?.focus()
  }, [active])

  return (
    // the cinematic dream view gets a roomier width (stage + side-gutter choices); the other sections
    // stay focused at the narrow default column.
    <div className={'wrap' + ((active === 'dream' && hasDream) || active === 'new' ? ' wide' : '')}>
      <div className="brand">
        <span className="mark">Lucid</span>
        <span className="tag">— a dream, one beat at a time</span>
        {/* ambient readiness: a quiet word by the wordmark; the full breakdown only when paused */}
        {s && <ReadyChip r={s.readiness} />}
      </div>

      {s && <Nav active={active} hasDream={hasDream} dreamName={s.name}
        onNavigate={(v) => navigate(v as View)} />}

      <div className="sr" role="status" aria-live="polite">{s ? announce(s) : ''}</div>

      {!s && isError ? (
        // Don't strand the page on "loading…" when Lucid is unreachable — say so; the poll keeps
        // retrying underneath and the page self-heals when the daemon comes back.
        <div className="card" role="alert">Can't reach Lucid — retrying…</div>
      ) : isLoading || !s ? (
        <div className="card" aria-busy="true">loading…</div>
      ) : (
        <>
          {/* the named three-dot breakdown is noise when healthy — surface it only when a piece is down */}
          {!s.readiness.can_dream && <ReadinessCard r={s.readiness} />}
          {/* deferred Create-from-image requests — global (independent of the current dream); self-hides when empty */}
          <QueuePanel />

          {active === 'dream' && s.chain && (
            <>
              {/* one unified instrument: the cinematic player + the dream-tree branch-map, with the
                  "what happens next" choices folded in as glowing future branches */}
              <Chain state={s} revealing={revealing} onLatestReady={clearReveal} />
              {phase === 'dreaming' && <Dreaming turn={s.turn} />}
              {/* the controls that act on THIS dream: a private one burns (+ save to stash); a saved one deletes */}
              {s.private ? <PrivateCard stash={s.stash} onGoStash={() => navigate('stash')} /> : <LibraryCard name={s.name} />}
            </>
          )}

          {active === 'new' && <Start onStarted={goDream} />}

          {active === 'library' && <DreamGallery onOpened={goDream} onNew={() => navigate('new')} />}

          {active === 'stash' && <StashPanel stash={s.stash} onOpened={goDream} onNew={() => navigate('new')} />}

          {active === 'settings' && (
            <>
              <h2 className="lib-head" data-view-heading tabIndex={-1}><span>Settings</span></h2>
              <EngineToggle engine={s.engine} />
              <PreviewToggle />
              <p className="lib-empty" style={{ marginTop: 12 }}>
                Saving, renaming, and deleting live with each dream — open one to manage it.
              </p>
            </>
          )}
        </>
      )}
    </div>
  )
}
