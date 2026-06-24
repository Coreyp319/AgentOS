import { useEffect, useState } from 'react'
import { motion } from 'motion/react'
import type { Turn } from './api'

const fmt = (t: number) => `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`

// The companion card to the develop hero, which now lives in <Chain>'s player box (so the finished clip
// can resolve out of the aurora in place). This carries the honest "still working" line + the elapsed
// timer while the GPU generates the next beat.
export default function Dreaming({ turn }: { turn: Turn }) {
  // tick elapsed locally (seeded once from the server) so it advances smoothly between the 2.5s state polls
  const [secs, setSecs] = useState(turn.elapsed ?? 0)
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}>
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
