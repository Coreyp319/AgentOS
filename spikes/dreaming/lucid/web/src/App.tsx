import { useLucidState } from './api'
import type { LucidState } from './api'
import { ReadinessCard, PrivateCard, LibraryCard } from './components'
import QueuePanel from './QueuePanel'
import Start from './Start'
import Chain from './Chain'
import Dreaming from './Dreaming'
import Choice from './Choice'

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
  return (
    <div className="wrap">
      <div className="brand">
        <span className="mark">Lucid</span>
        <span className="tag">— a dream, one beat at a time</span>
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
          <ReadinessCard r={s.readiness} />
          {/* deferred Create-from-image requests — global (independent of the current dream); self-hides when empty */}
          <QueuePanel />
          {s.private && <PrivateCard />}
          {!s.chain ? (
            <Start />
          ) : (
            <>
              <Chain nodes={s.chain.nodes} />
              {!s.private && <LibraryCard />}
              {s.turn.phase === 'dreaming' ? <Dreaming turn={s.turn} /> : <Choice state={s} />}
            </>
          )}
        </>
      )}
    </div>
  )
}
