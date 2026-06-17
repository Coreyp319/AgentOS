import { useRef, useState } from 'react'
import { useStart, fileToB64 } from './api'

const B2_NOTE = 'Any image you upload is checked for real-person likeness first, and its location/camera metadata is stripped.'

// start a dream from an uploaded image (B2 likeness-gated), a text description (t2i), or a synthetic
// frame. Preserves the existing backend consent flow (SeedBlocked / requires_consent).
export default function Start() {
  const start = useStart()
  const [priv, setPriv] = useState(false)
  const [text, setText] = useState('')
  const [msg, setMsg] = useState(B2_NOTE)
  // B2 likeness consent: when the seed looks like a real person, the backend returns requires_consent.
  // We surface it as an in-page card (not a native confirm) — a real-person-likeness decision deserves
  // an in-surface, legible moment consistent with the instrument register and the aria-live story.
  const [consentReason, setConsentReason] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function begin(consent = false) {
    setConsentReason(null)
    const f = fileRef.current?.files?.[0]
    const body: { private: boolean; image_b64?: string; text?: string; consent?: boolean } = { private: priv }
    if (f) { setMsg('🔎 checking your image for real-person likeness…'); body.image_b64 = await fileToB64(f); body.consent = consent }
    else if (text.trim()) { setMsg('✦ painting your opening…'); body.text = text.trim(); body.consent = consent }
    let j
    try {
      j = await start.mutateAsync(body)
    } catch {
      setMsg('Could not reach Lucid — try again.'); return  // don't leave "checking…/painting…" frozen
    }
    if (j?.blocked) {
      // overridable real-person likeness -> the in-page consent card; a hard block (e.g. possible minor)
      // is never overridable and stays a plain refusal.
      if (j.requires_consent) { setConsentReason(j.reason); setMsg(B2_NOTE); return }
      setMsg('🚫 ' + j.reason); return
    }
    if (j?.error) { setMsg(j.error); return }
    setMsg(B2_NOTE) // success — the state poll flips to the chain
  }

  return (
    <div className="card">
      <div className="card-title">Start a dream</div>
      <div className="note" style={{ marginTop: 6 }}>Begin an interactive dream — then choose what happens next, one beat at a time.</div>
      <label className="block">Opening image <span className="note">(optional — an abstract frame is used if you give neither)</span><br />
        {/* swapping the seed re-opens the B2 gate: a consent granted for the PREVIOUS image must never
            ride along with a different upload (the consent moment names a specific likeness). */}
        <input ref={fileRef} type="file" accept="image/*" onChange={() => setConsentReason(null)} />
      </label>
      <label className="block">…or describe the opening <span className="note">(your words → an image)</span><br />
        <input type="text" value={text} onChange={(e) => { setText(e.target.value); setConsentReason(null) }} placeholder="e.g. a calm aurora over dark rolling hills" />
      </label>
      <label className="check">
        <input type="checkbox" checked={priv} onChange={(e) => setPriv(e.target.checked)} />
        <span><span className="lock">🔒 Private session</span> <span className="note">— kept in memory, not saved, never shown elsewhere, wiped when you log out.</span></span>
      </label>
      <div className="note" style={{ marginBottom: 4 }}>Otherwise the dream is saved on this computer (your dream library) until you delete it.</div>
      <button className="beat" disabled={start.isPending || !!consentReason} onClick={() => begin(false)}>✦ Begin a dream</button>
      {consentReason ? (
        <div className="consent" role="group" aria-label="Real-person likeness consent">
          <div className="banner">{consentReason}</div>
          <div className="note" style={{ marginTop: 2 }}>
            Only continue if this is you, or you hold the right to use this image. A real-person dream is never set as wallpaper or shown elsewhere.
          </div>
          <div className="row">
            <button className="beat warm" disabled={start.isPending} onClick={() => begin(true)}>
              <b>I have the right to use this image — continue</b>
            </button>
            <button className="ghost" onClick={() => { setConsentReason(null); setMsg('Cancelled.') }}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="note" style={{ marginTop: 10 }} role="status" aria-live="polite">{msg}</div>
      )}
    </div>
  )
}
