import { useCallback, useEffect, useState } from 'react'
import { useLucidState } from './api'
import type { LucidState, TurnPhase } from './api'
import { ReadinessCard, ReadyChip, PrivateCard, LibraryCard, EngineToggle } from './components'
import QueuePanel from './QueuePanel'
import Start from './Start'
import Chain from './Chain'
import Dreaming from './Dreaming'

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
  // alone, the Ollama choices flash in over a still-buffering segment — the loop reads as "it skipped
  // showing me what I just made." So we HOLD the loading indication past `done` until the freshly
  // generated clip is actually on screen (Chain reports it via onLatestReady). Only then does <Choice>
  // mount, which is also what triggers the next /api/beats roll — so the new options can't be requested,
  // let alone shown, until the new segment is displayed.
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

  return (
    <div className="wrap">
      <div className="brand">
        <span className="mark">Lucid</span>
        <span className="tag">— a dream, one beat at a time</span>
        {/* ambient readiness: a quiet word by the wordmark; the full breakdown only when paused */}
        {s && <ReadyChip r={s.readiness} />}
      </div>

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
          {!s.chain ? (
            // Empty state leads with the act: Start is the hero; the engine recedes below it as a disclosure.
            <>
              <Start />
              <EngineToggle engine={s.engine} />
            </>
          ) : (
            <>
              {/* one unified instrument: the cinematic player + the dream-tree branch-map, with the
                  "what happens next" choices folded in as glowing future branches (was a separate card) */}
              <Chain state={s} revealing={revealing} onLatestReady={clearReveal} />
              {/* the develop/resolve hero + the choices now live in <Chain>; this is just the timer card
                  while the next beat generates */}
              {phase === 'dreaming' && <Dreaming turn={s.turn} />}
              {/* controls strip — settings + the burn/delete control, where it finally has a referent
                  (a real dream to wipe). A private session burns; a persistent one deletes. */}
              <EngineToggle engine={s.engine} />
              {s.private ? <PrivateCard /> : <LibraryCard />}
            </>
          )}
        </>
      )}
    </div>
  )
}
