#!/usr/bin/env python3
"""Lucid preview server — a self-contained, runnable surface that wires the backends from this
session into ONE page you can actually click, without touching the React app (web/) or lucid_web.py.

It proves four things end to end, against the real modules:
  • Refine        POST /api/refine  -> lucid_refine (the local narrator polishes a rough idea, gated)
  • Premise seed  POST /api/start   -> lucid_linear.start(premise=…); GET /api/beats shows the LIVE
                                       premise-steered suggestions (proof the seed reaches every beat)
  • Creations     GET  /api/jobs    -> lucid_jobs (the SAME board the right-click launcher writes —
                  GET  /api/clip       so a right-click "Create Video from Image" shows up here)
  • Consent       the grave-pause likeness modal (design/consent-likeness.html), inline

Loopback only, CSRF + same-origin guarded (mirrors lucid_web.py). The opening is a server-generated
abstract frame (trusted; no B2 needed) so the preview never asks for a real-person seed.

Run:  python3 preview_server.py     (port LUCID_PREVIEW_PORT, default 8770)
"""
import hmac
import json
import os
import secrets
import subprocess
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L   # noqa: E402
import lucid_refine as R   # noqa: E402
import lucid_jobs as J     # noqa: E402

HOST = os.environ.get("LUCID_PREVIEW_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUCID_PREVIEW_PORT", "8770"))
SESSION = os.environ.get("LUCID_PREVIEW_SESSION", "preview")
ORIGIN_OK = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
CSRF = secrets.token_hex(16)


def _http_ok(url, timeout=1.2):
    try:
        urllib.request.urlopen(url, timeout=timeout).read(1)
        return True
    except Exception:
        return False


def _coord_up():
    try:
        r = subprocess.run(
            ["busctl", "--user", "call", "org.agentos.Coordinator1", "/org/agentos/Coordinator1",
             "org.agentos.Coordinator1", "Status"], capture_output=True, text=True, timeout=4)
        return r.returncode == 0
    except Exception:
        return False


def readiness():
    return {"ollama": _http_ok(f"{L.E.OLLAMA}/api/version"), "coordinator": _coord_up()}


def _synthetic_opening():
    import tempfile
    from PIL import Image, ImageDraw
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img = Image.new("RGB", (720, 1280), (18, 22, 38))
    d = ImageDraw.Draw(img)
    for y in range(0, 1280, 5):
        d.line([(0, y), (720, y)],
               fill=(min(255, 18 + y // 12), min(255, 28 + y // 20), min(255, 60 + y // 9)))
    img.save(p)
    return p


def chain_or_none():
    try:
        return L.load_chain(SESSION)
    except Exception:
        return None


def state():
    chain = chain_or_none()
    return {"readiness": readiness(),
            "premise": (chain or {}).get("premise"),
            "started": chain is not None,
            "jobs": J.recent()}


PAGE = r"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content=dark>
<meta name=csrf content="__CSRF__"><title>Lucid · preview</title>
<link rel=preconnect href="https://fonts.googleapis.com"><link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,360;1,9..144,360&display=swap" rel=stylesheet>
<style>
:root{--base:#12141c;--deep:#161a28;--horizon:#1a2238;--field:#10131d;--raised:#1b2030;
--text:#e9ecf3;--muted:#8a90a0;--label:#7a8090;--hair:rgba(255,255,255,.07);
--blue:#7aa2ff;--warm:#e0884f;--up:#74d39a;--red:#ec7676;--idle:#757c8e;
--warm-soft:color-mix(in srgb,var(--warm) 22%,transparent);
--blue-wash:color-mix(in srgb,var(--blue) 11%,transparent);
--data:ui-sans-serif,system-ui,"SF Pro Text",sans-serif;--display:"Fraunces",Georgia,serif;
--r-sm:9px;--r-md:16px;--r-lg:20px;--ease:cubic-bezier(.2,.85,.25,1)}
*{box-sizing:border-box}
body{margin:0;color:var(--text);font:15px/1.55 var(--data);min-height:100vh;-webkit-font-smoothing:antialiased;
background:radial-gradient(1000px 600px at 80% -10%,color-mix(in srgb,var(--blue) 8%,transparent),transparent 60%),
linear-gradient(160deg,var(--base),var(--deep) 45%,var(--horizon))}
body::after{content:"";position:fixed;inset:0;pointer-events:none;opacity:.04;mix-blend-mode:overlay;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
.wrap{max-width:620px;margin:0 auto;padding:44px 22px 90px}
.brand{display:flex;align-items:baseline;gap:11px;margin-bottom:3px}
.mark{font-family:var(--display);font-weight:360;font-size:2.1rem;letter-spacing:-.01em;line-height:1}
.tag{font-family:var(--display);font-style:italic;font-size:.95rem;color:var(--muted)}
.intro{color:var(--muted);font-size:.84rem;margin:6px 0 22px;max-width:50ch}
.ready{display:flex;gap:16px;font-size:.78rem;color:var(--muted);margin-bottom:14px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}
.dot.on{background:var(--up)}.dot.off{background:transparent;border:1.5px solid var(--idle)}
.card{background:rgba(38,42,54,.46);border:1px solid var(--hair);border-radius:var(--r-md);padding:22px;margin-bottom:16px;backdrop-filter:blur(14px)}
.card-title{font-weight:600;font-size:1.02rem}
.lead{color:var(--muted);font-size:.85rem;margin-top:5px}
.promptwrap{position:relative;margin-top:16px}
.field{width:100%;min-height:96px;resize:vertical;background:var(--field);color:var(--text);
border:1px solid var(--hair);border-radius:var(--r-md);padding:14px 16px 46px;font:inherit;line-height:1.5;transition:border-color .15s,box-shadow .3s}
.field::placeholder{color:var(--label)}.field:focus{outline:none;border-color:color-mix(in srgb,var(--blue) 55%,var(--hair))}
.field.glow{animation:glow 1.5s var(--ease)}
@keyframes glow{0%{box-shadow:inset 0 0 0 60px color-mix(in srgb,var(--warm) 12%,transparent)}100%{box-shadow:inset 0 0 0 60px transparent}}
.refine{position:absolute;right:9px;bottom:9px;display:inline-flex;align-items:center;gap:7px;
background:color-mix(in srgb,var(--warm) 14%,var(--deep));color:#f3d9c4;border:1px solid color-mix(in srgb,var(--warm) 40%,transparent);
border-radius:999px;padding:7px 13px;font:inherit;font-size:.8rem;font-weight:500;cursor:pointer;transition:border-color .15s,background .15s,transform .1s}
.refine:hover{border-color:var(--warm);background:color-mix(in srgb,var(--warm) 20%,var(--deep))}
.refine:active{transform:translateY(1px)}.refine:disabled{opacity:.55;cursor:default}
.refine .wand{width:14px;height:14px;display:block}.refine.busy .wand{animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.under{display:flex;align-items:center;gap:12px;min-height:20px;margin-top:9px}
.under .ok{color:var(--up);font-size:.78rem}.under .err{color:#f0a36b;font-size:.78rem}
.link{background:none;border:none;color:var(--blue);font:inherit;font-size:.78rem;cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px}
.hint{color:var(--label);font-size:.75rem;margin-left:auto}
.check{display:flex;gap:9px;align-items:flex-start;margin:16px 0 4px;cursor:pointer}
.check input{margin-top:3px;accent-color:var(--warm)}.lock{color:var(--text);font-weight:600}.note{color:var(--label);font-size:.75rem}
.begin{display:block;width:100%;text-align:center;background:var(--blue-wash);border:1px solid color-mix(in srgb,var(--blue) 30%,var(--hair));
color:var(--text);border-radius:var(--r-sm);padding:13px;margin-top:16px;font:inherit;font-weight:600;cursor:pointer;transition:border-color .15s,background .15s}
.begin:hover{border-color:var(--blue);background:color-mix(in srgb,var(--blue) 16%,transparent)}
.begin:disabled{opacity:.5;cursor:default}
.premise{font-family:var(--display);font-style:italic;font-size:1.15rem;color:#eef1f8;margin:2px 0 0;line-height:1.35}
.beat{display:block;width:100%;text-align:left;background:var(--blue-wash);border:1px solid var(--hair);color:var(--text);
border-radius:var(--r-sm);padding:11px 13px;margin:8px 0;font:inherit}
.beat b{display:block;color:var(--blue);margin-bottom:2px}.beat small{color:var(--muted)}
.jobs{display:flex;flex-direction:column;gap:9px;margin-top:14px}
.job{display:flex;align-items:center;gap:12px;padding:11px 13px;border:1px solid var(--hair);border-radius:var(--r-sm);background:rgba(16,19,29,.5)}
.job .st{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.st.generating,.st.checking,.st.queued{background:var(--warm);animation:pulse 1.4s ease-in-out infinite}
.st.ready{background:var(--up)}.st.skipped{background:var(--idle)}.st.blocked,.st.failed{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.job .jt{flex:1;min-width:0}.job .jlabel{font-size:.86rem}.job .jstatus{font-size:.74rem;color:var(--muted)}
.job video{width:96px;border-radius:7px;border:1px solid var(--hair);background:#090b11;display:block}
.empty{color:var(--label);font-size:.8rem;padding:6px 0}
.minor{display:flex;gap:9px;margin-top:14px;flex-wrap:wrap}
.ghost{background:transparent;border:1px solid var(--hair);color:var(--muted);border-radius:var(--r-sm);padding:9px 14px;font:inherit;font-size:.82rem;cursor:pointer}
.ghost:hover{border-color:var(--muted);color:var(--text)}
/* consent modal (the grave pause) */
.scrim{position:fixed;inset:0;z-index:10;display:grid;place-items:center;padding:20px;background:rgba(7,9,15,.55);
backdrop-filter:blur(6px) brightness(.6);opacity:0;visibility:hidden;transition:opacity .2s,visibility .2s}
.scrim.open{opacity:1;visibility:visible}
.modal{position:relative;width:min(420px,100%);background:linear-gradient(180deg,var(--raised),var(--deep));border:1px solid var(--hair);
border-radius:var(--r-lg);padding:24px;box-shadow:0 40px 120px -40px #000;transform:translateY(10px) scale(.975);opacity:0;transition:transform .26s var(--ease),opacity .22s}
.scrim.open .modal{transform:none;opacity:1}
.modal::before{content:"";position:absolute;inset:0 0 auto;height:2px;border-radius:var(--r-lg) var(--r-lg) 0 0;
background:linear-gradient(90deg,transparent,var(--warm-soft),var(--warm) 50%,var(--warm-soft),transparent)}
.eyebrow{font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;color:var(--warm);font-weight:600;margin-bottom:6px}
.mtitle{font-size:1.18rem;font-weight:600;margin:0;letter-spacing:-.01em}
.mbody{color:var(--muted);font-size:.9rem;margin:12px 0 0}.mbody b{color:var(--text)}
.attest{display:flex;gap:11px;align-items:flex-start;margin:18px 0 4px;padding:13px;cursor:pointer;
background:color-mix(in srgb,var(--warm) 7%,transparent);border:1px solid var(--warm-soft);border-radius:12px}
.attest input{margin-top:2px;accent-color:var(--warm)}.attest .lbl{font-size:.875rem;color:var(--text);line-height:1.4}
.mfoot{display:flex;gap:10px;margin-top:16px}
.btn{flex:1;border-radius:var(--r-sm);padding:12px;font:inherit;font-weight:500;cursor:pointer;border:1px solid var(--hair);background:var(--raised);color:var(--text)}
.btn.cancel{flex:1.15;background:#222838;border-color:rgba(255,255,255,.12)}.btn.cancel:hover{border-color:var(--muted)}
.btn.go{background:transparent;border-color:var(--warm-soft);color:var(--warm)}.btn.go:disabled{opacity:.4;cursor:not-allowed}
:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
@media (prefers-reduced-motion:reduce){.field.glow,.refine.busy .wand,.st{animation:none}.modal{transform:none}}
@media (prefers-reduced-transparency:reduce){.card{backdrop-filter:none;background:rgba(24,28,40,.96)}.scrim{backdrop-filter:none;background:rgba(7,9,15,.94)}}
</style></head><body><div class=wrap>
<div class=brand><span class=mark>Lucid</span><span class=tag>· preview</span></div>
<div class=intro>A runnable slice of this session's work — Refine, the narrative-seed premise, the
creations queue, and the consent moment. Wired to the real backends.</div>
<div class=ready id=ready></div>

<div class=card id=startcard>
 <div class=card-title>What's this dream about?</div>
 <div class=lead>A few words is enough — Refine shapes it; the prompt then steers every beat.</div>
 <div class=promptwrap>
  <textarea id=prompt class=field placeholder="e.g. a calm aurora over dark rolling hills, someone watching"></textarea>
  <button id=refineBtn class=refine onclick=refine()>
   <svg class=wand viewBox="0 0 24 24" fill=none stroke="#f3d9c4" stroke-width=1.8 stroke-linecap=round><path d="M5 19l9-9"/><path d="M16 4l1 2 2 1-2 1-1 2-1-2-2-1z" fill="#f3d9c4" stroke=none/><path d="M14 6l4 4"/></svg>
   <span id=refineLbl>Refine</span></button>
 </div>
 <div class=under id=under><span class=hint>Refine calls the local narrator (Ollama).</span></div>
 <label class=check><input type=checkbox id=priv>
  <span><span class=lock>🔒 Private session</span> <span class=note>— not saved, never shown elsewhere, wiped on logout. (Private creations never appear in the queue below.)</span></span></label>
 <button class=begin id=beginBtn onclick=begin()>✦ Begin a dream</button>
</div>

<div class=card id=dreamcard style="display:none">
 <div class=card-title>This dream is about…</div>
 <p class=premise id=premiseText></p>
 <div class=lead style="margin-top:12px">What happens next — <b>steered by your premise</b> (live from the narrator):</div>
 <div id=beats><div class=empty>considering the next moves…</div></div>
 <button class=ghost style="margin-top:8px" onclick=reset_()>↺ Start over</button>
</div>

<div class=card>
 <div class=card-title>Creations queue</div>
 <div class=lead>Right-click any image → <b>Create Video from Image</b> and it appears here (the same job board the launcher writes). Private ones never show.</div>
 <div class=jobs id=jobs><div class=empty>No creations yet.</div></div>
</div>

<div class=card>
 <div class=card-title>Consent moment</div>
 <div class=lead>What B2 raises when a real person is in a seed — the grave pause (design preview).</div>
 <div class=minor><button class=ghost onclick=openConsent()>Preview the consent dialog</button></div>
</div>
</div>

<div class=scrim id=scrim role=dialog aria-modal=true aria-labelledby=mtitle>
 <div class=modal>
  <div class=eyebrow>A real person was detected</div>
  <h1 class=mtitle id=mtitle>Is this yours to use?</h1>
  <p class=mbody>Lucid would <b>animate this person into a moving video</b>. Only continue if this is you — or you have their permission, or the right to use this image.</p>
  <label class=attest><input type=checkbox id=att onchange=syncGo()>
   <span class=lbl>I am this person, or I have the right to use this image.</span></label>
  <div class=mfoot>
   <button class="btn cancel" id=cancelBtn onclick="closeConsent('Cancelled — nothing created.')">Cancel</button>
   <button class="btn go" id=goBtn disabled onclick="closeConsent('Consented — would create the video.')">Continue</button>
  </div>
 </div>
</div>

<script>
const CSRF=document.querySelector('meta[name=csrf]').content;
const E=s=>(s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $=id=>document.getElementById(id);
async function jget(u){return (await fetch(u)).json();}
async function jpost(u,b){return (await fetch(u,{method:'POST',headers:{'Content-Type':'application/json','X-Lucid-Token':CSRF},body:JSON.stringify(b)})).json();}

// ---- refine (real Ollama) ----
let original=null;
async function refine(){
 const t=$('prompt').value.trim();
 if(!t){setUnder('<span class=err>Type an idea first.</span>');$('prompt').focus();return;}
 original=$('prompt').value;
 $('refineBtn').classList.add('busy');$('refineBtn').disabled=true;$('refineLbl').textContent='Refining…';
 setUnder('<span class=hint>Shaping your idea…</span>');
 let j;try{j=await jpost('/api/refine',{text:t});}catch(e){j={ok:false,reason:'Could not reach the narrator.'};}
 $('refineBtn').classList.remove('busy');$('refineBtn').disabled=false;$('refineLbl').textContent='Refine';
 if(!j.ok){setUnder('<span class=err>'+E(j.reason||'Could not refine.')+'</span>');return;}
 const f=$('prompt');f.value=j.refined;f.classList.remove('glow');void f.offsetWidth;f.classList.add('glow');
 setUnder('<span class=ok>✓ Refined</span><button class=link onclick=undo()>Undo</button><span class=hint>Edit freely before you begin.</span>');
}
function undo(){if(original!=null){$('prompt').value=original;setUnder('<span class=hint>Back to your words.</span>');}}
function setUnder(h){$('under').innerHTML=h;}

// ---- begin (real start with premise) + premise-steered beats ----
async function begin(){
 const premise=$('prompt').value.trim();
 $('beginBtn').disabled=true;
 let j;try{j=await jpost('/api/start',{premise,private:$('priv').checked});}catch(e){j={error:'Could not start.'};}
 $('beginBtn').disabled=false;
 if(j.error){setUnder('<span class=err>'+E(j.error)+'</span>');return;}
 load();
}
async function reset_(){await jpost('/api/reset',{});load();}

function renderBeats(beats){
 const el=$('beats');if(!el)return;
 if(!beats||!beats.length){el.innerHTML='<div class=empty>No suggestions came back — the narrator may be busy.</div>';return;}
 el.innerHTML=beats.map(b=>'<div class=beat><b>'+E(b.label)+'</b><small>'+E(b.prompt)+'</small></div>').join('');
}

// ---- creations queue (real jobs — written by the right-click launcher) ----
function renderJobs(jobs){
 const el=$('jobs');
 if(!jobs||!jobs.length){el.innerHTML='<div class=empty>No creations yet. Right-click an image → Create Video from Image.</div>';return;}
 el.innerHTML=jobs.map(j=>{
  const ready=j.status==='ready';
  const v=ready?'<video src="/api/clip?id='+encodeURIComponent(j.id)+'" muted loop autoplay playsinline></video>':'';
  const detail=j.detail?(' · '+E(j.detail)):'';
  return '<div class=job><span class="st '+E(j.status)+'"></span><div class=jt>'
   +'<div class=jlabel>'+E(j.title||'Create from image')+'</div>'
   +'<div class=jstatus>'+E(j.status)+detail+'</div></div>'+v+'</div>';
 }).join('');
}

async function load(){
 let s;try{s=await jget('/api/state');}catch(e){return;}
 $('ready').innerHTML='<span><span class="dot '+(s.readiness.ollama?'on':'off')+'"></span>narrator</span>'
  +'<span><span class="dot '+(s.readiness.coordinator?'on':'off')+'"></span>graphics lease</span>';
 if(s.started){
  $('startcard').style.display='none';$('dreamcard').style.display='';
  $('premiseText').textContent=s.premise||'an open dream';
  jget('/api/beats').then(j=>renderBeats(j.beats)).catch(()=>{});
 }else{$('startcard').style.display='';$('dreamcard').style.display='none';}
 renderJobs(s.jobs);
}

// ---- consent modal ----
let lastFocus=null;
function openConsent(){lastFocus=document.activeElement;$('att').checked=false;syncGo();
 $('scrim').classList.add('open');requestAnimationFrame(()=>$('cancelBtn').focus());}
function closeConsent(){$('scrim').classList.remove('open');if(lastFocus)lastFocus.focus();}
function syncGo(){$('goBtn').disabled=!$('att').checked;}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('scrim').classList.contains('open'))closeConsent();});
$('scrim').addEventListener('mousedown',e=>{if(e.target===$('scrim'))closeConsent();});

load();setInterval(load,3000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.replace("__CSRF__", CSRF), "text/html; charset=utf-8")
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path == "/api/state":
            return self._json(200, state())
        if path == "/api/jobs":
            return self._json(200, {"jobs": J.recent()})
        if path == "/api/beats":
            try:
                return self._json(200, {"beats": L.propose(L.context_for(SESSION))})
            except Exception:
                return self._json(200, {"beats": []})
        if path == "/api/clip":
            from urllib.parse import parse_qs, urlsplit
            qid = (parse_qs(urlsplit(self.path).query).get("id") or [""])[0]
            clip = J.clip_path(qid)
            if not clip:
                return self._send(404, "not found", "text/plain")
            try:
                with open(clip, "rb") as f:
                    return self._send(200, f.read(), "video/mp4")
            except OSError:
                return self._send(404, "not found", "text/plain")
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/refine", "/api/start", "/api/reset"):
            return self._send(404, "not found", "text/plain")
        if not hmac.compare_digest(self.headers.get("X-Lucid-Token", ""), CSRF):
            return self._json(403, {"error": "missing/invalid CSRF token"})
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGIN_OK:
            return self._json(403, {"error": "cross-origin refused"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or "{}") if n else {}
        except Exception:
            return self._json(400, {"error": "bad request"})

        if path == "/api/refine":
            return self._json(200, R.refine(req.get("text", "")))

        if path == "/api/reset":
            try:
                L.ST.clear(SESSION)
            except Exception:
                pass
            return self._json(200, {"ok": True})

        if path == "/api/start":
            premise = (req.get("premise") or "").strip()
            private = bool(req.get("private"))
            try:
                L.ST.clear(SESSION)
                seed = _synthetic_opening()
                try:
                    L.start(SESSION, seed, private=private, _trusted_seed=True, premise=premise)
                finally:
                    try:
                        os.remove(seed)
                    except OSError:
                        pass
                return self._json(200, {"ok": True, "premise": premise or None})
            except Exception as e:
                return self._json(200, {"error": f"start failed: {e}"})


def main():
    print(f"Lucid preview → http://{HOST}:{PORT}  (session '{SESSION}')", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
