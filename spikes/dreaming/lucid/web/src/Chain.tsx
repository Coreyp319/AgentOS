import { useEffect, useRef, useState } from 'react'
import type { DreamNode } from './api'
import { clipUrl, frameUrl } from './api'

const FPS = 16   // Wan i2v cadence (lucid_engine); a node's `length` is a frame count -> seconds = length / FPS
const fmtDur = (secs: number) => `${Math.floor(secs / 60)}:${String(Math.round(secs % 60)).padStart(2, '0')}`

// The develop hero: the aurora forms while the GPU generates, then — when the finished frame arrives
// (`posterSrc`) — that frame blooms IN over the aurora, in the SAME box, under the held serif caption,
// and `onResolved` fires after the dissolve so the real <video> takes over in place (ADR-0014's "the
// clip developing"). A child component so its `posterIn` state re-arms by unmounting between beats; the
// dissolve is opacity-only over two loaded rasters (zero GPU) and collapses to a cut under reduced-motion.
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
      <div className="cap">
        <p className="beat-q" style={caption ? undefined : { opacity: 0.6 }}>
          {caption ? `“${caption}”` : 'the next moment is forming…'}
        </p>
      </div>
    </div>
  )
}

// "clip management": the native player (autoplay/loop/poster, off-the-shelf platform player) + a
// poster filmstrip of the chain. This is the first time the surface actually plays the generated
// video — the old page listed clips as text only.
// `onLatestReady` lets the parent hold its loading indication until the freshest clip is actually on
// screen (App's reveal hold). It fires when the latest clip has loaded its first frame — or right away
// when we're not the one displaying that frame (clip-less tip, or the user has scrolled back to review
// an earlier frame), so the hold can never stall on a clip we'll never paint.
// `dreaming`/`revealing`/`caption` thread the turn state into the player box so the develop hero — the
// aurora forming, then the finished frame resolving out of it — happens HERE, in place, rather than in a
// separate card the clip pops in beside (ADR-0014: "the clip developing, not a spinner"). `revealing` is
// App's reveal hold; the poster's dissolve releasing it (onPosterLoad) is what hands off to the player.
export default function Chain({ nodes, onLatestReady, dreaming = false, revealing = false, caption = null }:
  { nodes: DreamNode[]; onLatestReady?: () => void; dreaming?: boolean; revealing?: boolean; caption?: string | null }) {
  const playable = nodes.filter((n) => n.clip)
  const latest = playable.length ? playable[playable.length - 1].id : nodes[nodes.length - 1].id
  const [selId, setSelId] = useState<number>(latest)
  const [playAll, setPlayAll] = useState(false)   // play every segment in order, advancing on `ended`
  const [repeat, setRepeat] = useState(true)      // loop — the whole sequence in play-all, else the one clip
  const stripRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  // Follow the newest clip as the story advances — but only if the user is already watching the tip.
  // If they've clicked back to review an earlier frame, don't yank them to the new clip mid-watch.
  const prevLatest = useRef(latest)
  useEffect(() => {
    if (selId === prevLatest.current) setSelId(latest)
    prevLatest.current = latest
  }, [latest, selId])
  const sel = nodes.find((n) => n.id === selId) ?? nodes[nodes.length - 1]
  const viewingLatestClip = sel.id === latest && !!sel.clip
  useEffect(() => {
    if (!viewingLatestClip) onLatestReady?.()   // nothing to wait for — release the hold immediately
  }, [viewingLatestClip, sel.id, onLatestReady])

  // "Play all" = watch the dream end-to-end from the first clip; toggling it off stops on the spot.
  function togglePlayAll() {
    if (playAll) return setPlayAll(false)
    if (!playable.length) return
    setSelId(playable[0].id)
    setPlayAll(true)
  }
  // each segment ends -> advance to the next; at the tail, wrap if Repeat is on, else stop on the last frame.
  function onEnded() {
    if (!playAll) return
    const i = playable.findIndex((n) => n.id === sel.id)
    const next = playable[i + 1]
    if (next) setSelId(next.id)
    else if (repeat) setSelId(playable[0].id)   // >=2 clips while play-all is offered, so the id changes -> remounts
    else setPlayAll(false)
  }
  const pos = playAll ? playable.findIndex((n) => n.id === sel.id) + 1 : 0
  const idx = nodes.findIndex((n) => n.id === sel.id)           // position in the full chain (for step nav)
  const totalSecs = playable.reduce((a, n) => a + (n.length ? n.length / FPS : 0), 0)
  const go = (j: number) => { if (nodes[j]) setSelId(nodes[j].id) }

  // keep the playing segment's thumbnail in view on long chains (own scroll only — never moves the page).
  // Honour prefers-reduced-motion (a JS scroll the CSS media query can't reach) and don't pan if it's
  // already visible — a calm viewer isn't yanked on every advance.
  useEffect(() => {
    if (!playAll) return
    const strip = stripRef.current
    const el = strip?.querySelector('.thumb.sel') as HTMLElement | null
    if (!strip || !el) return
    const visible = el.offsetLeft >= strip.scrollLeft && el.offsetLeft + el.clientWidth <= strip.scrollLeft + strip.clientWidth
    if (visible) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    strip.scrollTo({ left: el.offsetLeft - strip.clientWidth / 2 + el.clientWidth / 2, behavior: reduce ? 'auto' : 'smooth' })
  }, [selId, playAll])

  // keyboard control for the whole player — Space play/pause, ←/→ step a beat, Home/End jump, P play-all,
  // R repeat. Bails while typing so a custom prompt in <Choice> is never eaten. Re-subscribed when the
  // chain/selection/mode changes so the handler never reads a stale closure.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (dreaming || revealing) return                  // nothing to drive while the next moment forms/resolves
      const el = document.activeElement
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return
      const onVideo = el === videoRef.current            // let the browser own media keys on the focused player
      const onButton = el instanceof HTMLButtonElement || el instanceof HTMLAnchorElement
      const pl = nodes.filter((n) => n.clip)             // self-contained so the once-subscribed handler is never stale
      switch (e.key) {
        case ' ': case 'k': {
          if (onVideo || onButton) return                // native: play/pause the focused video, or click the button
          const v = videoRef.current
          if (!v) return
          e.preventDefault()
          if (v.paused) v.play().catch(() => {}); else v.pause()
          break
        }
        case 'ArrowLeft': if (onVideo) return; if (idx > 0) { e.preventDefault(); setSelId(nodes[idx - 1].id) } break
        case 'ArrowRight': if (onVideo) return; if (idx < nodes.length - 1) { e.preventDefault(); setSelId(nodes[idx + 1].id) } break
        case 'Home': if (onVideo) return; e.preventDefault(); setSelId(nodes[0].id); break
        case 'End': if (onVideo) return; e.preventDefault(); setSelId(nodes[nodes.length - 1].id); break
        case 'p': case 'P':
          if (playAll) setPlayAll(false)
          else if (pl.length > 1) { setSelId(pl[0].id); setPlayAll(true) }
          break
        case 'r': case 'R': setRepeat((v) => !v); break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [nodes, idx, playAll, dreaming, revealing])

  return (
    <div className="card">
      <div className="card-title">
        Your dream so far · {nodes.length} frame{nodes.length === 1 ? '' : 's'}
        {totalSecs > 0 && <span className="player-pos"> · {fmtDur(totalSecs)}</span>}
      </div>
      <div style={{ marginTop: 12 }}>
        {dreaming || revealing ? (
          // the develop hero, in the player box: aurora forming -> the finished frame resolving out of it
          <DevelopHero caption={caption} posterSrc={revealing ? frameUrl(latest) : null}
            onResolved={() => onLatestReady?.()} />
        ) : sel.clip ? (
          <video
            key={sel.id}
            ref={videoRef}
            className="vid"
            src={clipUrl(sel.id)}
            poster={frameUrl(sel.id)}
            aria-label={`Dream clip: ${sel.label || 'opening'}`}
            autoPlay muted playsInline controls
            loop={!playAll && repeat}      // in play-all we hand off to the next clip on `ended` instead of looping
            onEnded={onEnded}
            // first frame painted (or it failed) — the freshest segment is now displayed; release the hold
            onLoadedData={onLatestReady}
            onError={onLatestReady}
          />
        ) : (
          <img className="still" src={frameUrl(sel.id)} alt={sel.label || 'opening frame'} />
        )}
      </div>
      {!dreaming && !revealing && playable.length > 0 && (
        <div className="player-bar" role="group" aria-label="Playback"
          aria-keyshortcuts="Space ArrowLeft ArrowRight Home End P R">
          {/* one-shot announcement: fires when play-all starts/stops, NOT on every clip advance (the
              visible counter below is aria-hidden so a screen reader isn't read the count each segment) */}
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
          <button
            className={'pbtn' + (repeat ? ' on' : '')} aria-pressed={repeat}
            onClick={() => setRepeat((v) => !v)}
            title={playAll ? 'Loop the whole dream' : 'Loop this clip'}
          >
            <span className="ic" aria-hidden="true">🔁</span>
            Repeat {repeat ? 'on' : 'off'}
          </button>
          {playAll && playable.length > 1 && (
            <span className="player-pos" aria-hidden="true">Playing {Math.max(1, pos)} of {playable.length}</span>
          )}
        </div>
      )}
      {!dreaming && !revealing && nodes.length > 1 && (
        <div className="strip" ref={stripRef} role="group" aria-label="Frames — jump to a beat">
          {nodes.map((n) => (
            <button
              key={n.id}
              className={'thumb' + (n.id === selId ? ' sel' : '')}
              aria-current={n.id === selId ? 'true' : undefined}
              onClick={() => setSelId(n.id)}
            >
              <img src={frameUrl(n.id)} alt={n.label || 'frame'} loading="lazy" />
              <div className="cap2">{n.label || 'opening'}{n.length ? ` · ${fmtDur(n.length / FPS)}` : ''}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
