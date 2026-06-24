import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { PreviewToggle } from 'web'

// PreviewToggle is a collapsed <details>; open it at mount so the card shows the
// On/Off choice and the honest GPU heat/noise disclosure, not just the summary.
function Open({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    ref.current?.querySelector('details')?.setAttribute('open', '')
  }, [])
  return <div ref={ref}>{children}</div>
}

// At-rest: the collapsed "Path previews — Off" disclosure.
export const Resting = () => <PreviewToggle />

// Open: the On/Off choice with the "runs on your GPU, may spin the fan" note.
export const Expanded = () => (
  <Open>
    <PreviewToggle />
  </Open>
)
