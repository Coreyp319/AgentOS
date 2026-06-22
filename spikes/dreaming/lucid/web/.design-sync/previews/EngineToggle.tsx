import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { EngineToggle } from 'web'

// EngineToggle is a collapsed <details>; open it at mount so the card shows the
// real engine picker (the option buttons + VRAM glosses), which is the view that
// carries the design — not just the one-line summary bar.
function Open({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.querySelector('details')?.setAttribute('open', '')
  }, [])
  return <div ref={ref}>{children}</div>
}

const WAN = { active: 'wan', options: ['wan', '10eros'] }
const TEN = { active: '10eros', options: ['wan', '10eros'] }

// At-rest: the collapsed disclosure showing the active engine + its VRAM gloss.
export const Collapsed = () => <EngineToggle engine={WAN} />

// Open, Wan active — the picker with both engines and their look/VRAM trade-off.
export const PickerWan = () => (
  <Open>
    <EngineToggle engine={WAN} />
  </Open>
)

// Open, 10Eros active — the heavier, sharper engine selected.
export const Picker10Eros = () => (
  <Open>
    <EngineToggle engine={TEN} />
  </Open>
)
