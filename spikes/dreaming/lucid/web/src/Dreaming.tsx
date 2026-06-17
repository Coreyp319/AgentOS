import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import type { Turn } from './api'

const fmt = (t: number) => `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`

// the signature moment: the dream developing out of the dark while the GPU generates the next clip.
export default function Dreaming({ turn }: { turn: Turn }) {
  // tick elapsed locally (seeded from the server) so the aurora isn't re-mounted on every poll
  const start = useRef(Date.now() - (turn.elapsed ?? 0) * 1000)
  const [secs, setSecs] = useState(turn.elapsed ?? 0)
  useEffect(() => {
    const id = setInterval(() => setSecs(Math.max(0, Math.floor((Date.now() - start.current) / 1000))), 1000)
    return () => clearInterval(id)
  }, [])
  const label = turn.label && turn.label !== 'custom' ? turn.label : null
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}
      style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="stage" aria-busy="true">
        <div className="aurora"><i /><i /><i /></div>
        <div className="grain" />
        <div className="cap">
          <p className="beat-q" style={label ? undefined : { opacity: 0.6 }}>
            {label ? `“${label}”` : 'the next moment is forming…'}
          </p>
        </div>
      </div>
      <div className="card">
        <div className="dreamrow">
          <span>
            <b>✦ Dreaming this beat…</b>
            <div className="note" style={{ marginTop: 2 }}>Making the next clip — this usually takes a few minutes.</div>
          </span>
          <span className="elapsed-xl">{fmt(secs)}</span>
        </div>
        <div className="note" style={{ marginTop: 8 }}>
          It runs through the graphics lease, so it never crowds out your other apps — you can watch it in the keyhole tray.
        </div>
      </div>
    </motion.div>
  )
}
