import { useState } from 'react'
import { useBeats, useDream } from './api'
import type { LucidState } from './api'

// "what happens next" — LLM-proposed beats + a free-text turn, plus the honest outcome of the last
// turn (skipped / error / refused) rendered inline.
export default function Choice({ state }: { state: LucidState }) {
  const dream = useDream()
  const [own, setOwn] = useState('')
  const [flash, setFlash] = useState('')
  const [committed, setCommitted] = useState(false)
  const canDream = state.readiness.can_dream
  const tipId = state.chain?.nodes[state.chain.nodes.length - 1]?.id ?? -1
  const { data: beats = [], isLoading, isFetching } = useBeats(
    !!state.chain && canDream && state.turn.phase !== 'dreaming', state.session, tipId)
  const t = state.turn
  // A pick is a commitment: the instant it's in flight, lock the menu so the user can't fire a second
  // beat (the server would reject it, but silent lock-out is calmer than an error flash) and so the
  // held suggestions don't read as still-choosable while the dream is already starting. `committed`
  // holds the lock from a successful fire until the poll swaps in <Dreaming/> (this unmounts) — without
  // it the lock releases the instant the mutation settles, ~2.5s before the poll, leaving a double-fire
  // window. A fire that returns an error does NOT commit, so the user can retry.
  const busy = dream.isPending || committed
  const loadingBeats = isLoading || (isFetching && beats.length === 0)

  function showFlash(m: string) { setFlash(m); window.setTimeout(() => setFlash(''), 6000) }

  async function fire(prompt: string, label: string) {
    try {
      const j = await dream.mutateAsync({ prompt, label })
      if (j?.error) showFlash(j.error)
      else setCommitted(true)   // started — stay locked until <Dreaming/> takes over
    } catch {
      showFlash('Could not reach Lucid — try again.')   // a network drop must not vanish silently
    }
  }

  return (
    <div className="card">
      {flash && <div className="flash" role="alert">{flash}</div>}
      <div className="card-title">What happens next?</div>
      {t.phase === 'skipped' && <div className="banner">That beat was skipped — the graphics card was needed elsewhere, so the dream fails open and your desktop is untouched. Choose again when you're ready.</div>}
      {t.phase === 'error' && <div className="banner bad">That clip didn't come through — your desktop is untouched. Try again.</div>}
      {t.phase === 'refused' && <div className="banner">That direction isn't something Lucid can make. Try a different turn.</div>}
      {canDream ? (
        <>
          {loadingBeats ? (
            <div className="note">considering the next moves…</div>
          ) : beats.length ? (
            beats.map((b, i) => (
              <button key={i} className="beat" disabled={busy} onClick={() => fire(b.prompt, b.label)}>
                <b>{b.label}</b><small>{b.prompt}</small>
              </button>
            ))
          ) : (
            <div className="note">No suggestions — type your own below.</div>
          )}
          <input
            type="text" value={own} placeholder="…or type what happens next" disabled={busy}
            onChange={(e) => setOwn(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && own.trim() && !busy) { fire(own.trim(), 'custom'); setOwn('') } }}
          />
        </>
      ) : (
        <div className="note">Choosing what happens next switches on once everything above is ready.</div>
      )}
    </div>
  )
}
