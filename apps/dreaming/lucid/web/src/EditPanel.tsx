import { useRef, useState, type ChangeEvent } from 'react'
import { editPreview, useEditCommit, type EditPreviewResult } from './api'

// ADR-0040 — prompt-guided keyframe edit ("edit-then-animate"). Point at the selected frame, describe the
// action ("raise the lantern, turn toward the glow"), optionally drop a reference image; Qwen-Image-Edit
// produces a NEW keyframe shown for APPROVAL (the fuse→dream readback grammar) before the minutes-long i2v.
// Then "Animate" either grows a NEW beat from the edited pose or REPLACES this shot in place (revertible).
// Self-contained so Chain.tsx stays small; total fail-open mirrors the backend (a decline never blocks the
// normal beat path).

type Placement = 'branch' | 'replace'
type Phase = 'compose' | 'previewing' | 'ready' | 'committing'

function fileToB64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] ?? '')   // strip the data: prefix
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
}

export default function EditPanel({ node, canReplace, disabled, onClose }:
  { node: number; canReplace: boolean; disabled: boolean; onClose: () => void }) {
  const [instruction, setInstruction] = useState('')
  const [placement, setPlacement] = useState<Placement>('branch')
  const [imgB64, setImgB64] = useState<string | null>(null)
  const [imgName, setImgName] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>('compose')
  const [res, setRes] = useState<EditPreviewResult | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const commit = useEditCommit()
  const working = phase === 'previewing' || phase === 'committing' || disabled

  async function runPreview(consent = false) {
    const text = instruction.trim()
    if (!text) { setMsg('Describe the edit first.'); return }
    setMsg(null); setPhase('previewing')
    let r: EditPreviewResult
    try {
      r = await editPreview({ node, prompt: text, placement, image_b64: imgB64 ?? undefined, consent })
    } catch {
      setPhase('compose'); setMsg('Something went wrong — try again.'); return
    }
    if (r.blocked) {   // the reference image tripped the B2 real-person likeness guard (ADR-0017)
      setPhase('compose')
      if (r.requires_consent &&
        window.confirm('That reference looks like a real person. Continue only if you are this person '
          + 'or have the right to use this image.\n\nContinue?')) {
        return runPreview(true)
      }
      setMsg(r.requires_consent ? 'Reference not used.' : 'That reference image can’t be used.')
      return
    }
    if (!r.ok || !r.token) { setPhase('compose'); setMsg(r.reason || 'Couldn’t edit the frame just now.'); return }
    setRes(r); setPhase('ready')
  }

  function animate() {
    if (!res?.token) return
    setPhase('committing')
    // F1: only close when a turn actually STARTED (the page's TURN poll then shows progress). If the commit was
    // declined (not-ready / token expired / a dream slipped in), KEEP the approved keyframe + surface the reason
    // instead of silently closing and losing the edit.
    commit.mutate({ token: res.token }, {
      onSuccess: (r: { started?: boolean; error?: string }) => {
        if (r && r.started) { onClose(); return }
        setPhase('ready')
        setMsg((r && r.error) || 'Couldn’t start the render — preview again.')
      },
      onError: () => { setPhase('ready'); setMsg('Couldn’t reach the server — try again.') },
    })
  }

  async function onPick(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    try { setImgB64(await fileToB64(f)); setImgName(f.name); setMsg(null) }
    catch { setMsg('Couldn’t read that image.') }
  }
  function clearImg() { setImgB64(null); setImgName(null); if (fileRef.current) fileRef.current.value = '' }

  return (
    <div className="edit-panel" role="group" aria-label="Edit this frame with a prompt">
      {(phase === 'compose' || phase === 'previewing') ? (
        <>
          <div className="edit-head">Direct the action — edit this frame, then animate from it</div>
          <textarea className="edit-instr" value={instruction} disabled={working} rows={2}
            placeholder="e.g. raise the lantern and turn toward the glow…"
            aria-label="Describe the edit to this frame"
            onChange={(e) => setInstruction(e.target.value)}
            onKeyDown={(e) => {
              if ((e.key === 'Enter' && (e.metaKey || e.ctrlKey))) { e.preventDefault(); runPreview() }
              else if (e.key === 'Escape') { e.preventDefault(); onClose() }
            }} />
          <div className="edit-row">
            <input ref={fileRef} type="file" accept="image/*" className="edit-file" disabled={working}
              aria-label="Optional reference image" onChange={onPick} />
            {imgName && <button type="button" className="edit-imgclear" onClick={clearImg}
              aria-label="Remove the reference image">ref: {imgName} ✕</button>}
          </div>
          <div className="tagchips" role="group" aria-label="What should the edit become?">
            <button type="button" className={'tagchip' + (placement === 'branch' ? ' on' : '')}
              aria-pressed={placement === 'branch'} disabled={working}
              onClick={() => setPlacement('branch')}>Grow a new beat</button>
            {canReplace && (
              <button type="button" className={'tagchip' + (placement === 'replace' ? ' on' : '')}
                aria-pressed={placement === 'replace'} disabled={working}
                title="Re-render this shot in place (revertible)"
                onClick={() => setPlacement('replace')}>Replace this shot</button>
            )}
          </div>
          {msg && <p className="edit-msg" role="status">{msg}</p>}
          <div className="tag-draft-row">
            <button type="button" className="tag-save" disabled={working || !instruction.trim()}
              onClick={() => runPreview()}>
              {phase === 'previewing' ? 'Editing the frame…' : 'Preview edit'}
            </button>
            <button type="button" className="tag-cancel" onClick={onClose}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          <div className="edit-head">Here’s the edited starting frame — animate from it?</div>
          {res?.preview && <img className="edit-keyframe" src={res.preview} alt="The edited frame the next clip will animate from" />}
          {msg && <p className="edit-msg" role="status">{msg}</p>}
          <div className="tag-draft-row">
            <button type="button" className="tag-save" disabled={working} onClick={animate}>
              {phase === 'committing' ? 'Animating…'
                : placement === 'replace' ? 'Animate · replace this shot' : 'Animate · new beat'}
            </button>
            <button type="button" className="tag-cancel" disabled={phase === 'committing'}
              onClick={() => { setPhase('compose'); setRes(null) }}>Try a different edit</button>
          </div>
        </>
      )}
    </div>
  )
}
