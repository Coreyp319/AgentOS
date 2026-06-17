import { useEffect, useRef, useState } from 'react'
import type { DreamNode } from './api'
import { clipUrl, frameUrl } from './api'

// "clip management": the native player (autoplay/loop/poster, off-the-shelf platform player) + a
// poster filmstrip of the chain. This is the first time the surface actually plays the generated
// video — the old page listed clips as text only.
export default function Chain({ nodes }: { nodes: DreamNode[] }) {
  const playable = nodes.filter((n) => n.clip)
  const latest = playable.length ? playable[playable.length - 1].id : nodes[nodes.length - 1].id
  const [selId, setSelId] = useState<number>(latest)
  // Follow the newest clip as the story advances — but only if the user is already watching the tip.
  // If they've clicked back to review an earlier frame, don't yank them to the new clip mid-watch.
  const prevLatest = useRef(latest)
  useEffect(() => {
    if (selId === prevLatest.current) setSelId(latest)
    prevLatest.current = latest
  }, [latest, selId])
  const sel = nodes.find((n) => n.id === selId) ?? nodes[nodes.length - 1]

  return (
    <div className="card">
      <div className="card-title">Your dream so far · {nodes.length} frame{nodes.length === 1 ? '' : 's'}</div>
      <div style={{ marginTop: 12 }}>
        {sel.clip ? (
          <video
            key={sel.id}
            className="vid"
            src={clipUrl(sel.id)}
            poster={frameUrl(sel.id)}
            autoPlay loop muted playsInline controls
          />
        ) : (
          <img className="still" src={frameUrl(sel.id)} alt={sel.label || 'opening frame'} />
        )}
      </div>
      {nodes.length > 1 && (
        <div className="strip">
          {nodes.map((n) => (
            <button
              key={n.id}
              className={'thumb' + (n.id === selId ? ' sel' : '')}
              onClick={() => setSelId(n.id)}
              title={n.label}
            >
              <img src={frameUrl(n.id)} alt={n.label || 'frame'} loading="lazy" />
              <div className="cap2">{n.label || 'opening'}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
