#!/usr/bin/env python3
"""Lucid web surface (ADR-0014/0015) — the dedicated interactive page the AgentOS status
panel (:9123) opens. The status board stays read-only status + links; lucid *renders* here.

It is a thin, loopback-only, stdlib-only surface over the SAFE MVP path (`lucid_linear`):
every prompt passes the deterministic gate (`lucid_safety.gate_prompt`), every video beat goes
through the coordinator lease (Spawn/confirm-evict/Release), and the whole thing FAILS OPEN and
says so honestly when the coordinator/ComfyUI/Ollama isn't there — it never looks ready when it
isn't, and never forces a GPU load. Wears the shared instrument "glass" register
(integrations/design/instrument-tokens.md) so it feels like the keyhole + status panel.

Endpoints:
  GET  /            the page
  GET  /healthz     200 "ok"  (the status panel's reachability probe)
  GET  /api/state   readiness (coordinator/comfyui/ollama) + current chain + validated beats
  POST /api/dream   one gated, leased turn (same-origin guarded) — {prompt|choose, label}

Run: python3 lucid_web.py   (port LUCID_WEB_PORT, default 8765; loopback only)
"""
import base64
import hmac
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_linear as L   # noqa: E402  (safe MVP path: gate -> confirm-evict -> lease -> generate)
import lucid_safety as S   # noqa: E402

HOST = os.environ.get("LUCID_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("LUCID_WEB_PORT", "8765"))
SESSION = os.environ.get("LUCID_WEB_SESSION", "web")
ORIGIN_OK = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
# Per-process CSRF token: embedded in the page, required as a header on every state-changing POST.
# A cross-origin page can't read it (same-origin policy), so it closes the missing-Origin CSRF gap.
CSRF = secrets.token_hex(16)
# Bound the expensive start path (each upload = an image decode + a ~13s vision model load) so a
# burst of /api/start can't exhaust memory / thrash the GPU the coordinator arbitrates (security).
_START_SEM = threading.BoundedSemaphore(2)
MAX_BODY = 30 * 1024 * 1024     # hard request-body ceiling (before reading)
MAX_IMG = 20 * 1024 * 1024      # decoded-image-bytes ceiling
MAX_PIXELS = 24_000_000         # ~6000x4000 — reject decompression bombs


# ---------------- readiness probes (honest; never claim ready when blind) ----------------
def _http_ok(url, timeout=1.5):
    try:
        urllib.request.urlopen(url, timeout=timeout).read(1)
        return True
    except Exception:
        return False


def _coordinator_up():
    try:
        r = subprocess.run(
            ["busctl", "--user", "call", "org.agentos.Coordinator1", "/org/agentos/Coordinator1",
             "org.agentos.Coordinator1", "Status"], capture_output=True, text=True, timeout=4)
        return r.returncode == 0
    except Exception:
        return False


def readiness():
    comfy = _http_ok(f"http://{L.COMFY_HOST}/system_stats")
    ollama = _http_ok(f"{L.E.OLLAMA}/api/version")
    coord = _coordinator_up()
    # The loop can actually DREAM only when all three are present (lease + backend + narrator).
    return {
        "coordinator": coord, "comfyui": comfy, "ollama": ollama,
        "can_dream": coord and comfy and ollama,
        "why": ([] if coord else ["coordinator down — start `agentosd lease`"])
               + ([] if comfy else ["ComfyUI unreachable"])
               + ([] if ollama else ["Ollama unreachable"]),
    }


def chain_or_none():
    try:
        return L.load_chain(SESSION)
    except Exception:
        return None


def _synthetic_opening():
    """A server-generated abstract opening frame — no user upload, so no real-person B2 concern.
    A placeholder seed until upload + the face/likeness guard (or text-to-opening) land."""
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


def _decode_seed(raw):
    """Validate uploaded bytes are a real image and re-encode to a clean PNG — strips EXIF
    (GPS / camera / identity metadata, a privacy win) and normalizes the format for ComfyUI.
    Guards against decompression bombs: a tiny PNG can claim gigapixels (security review)."""
    from PIL import Image
    import tempfile
    import warnings
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)  # warn-band -> hard reject
        Image.open(io.BytesIO(raw)).verify()             # raises if not a valid image
        img = Image.open(io.BytesIO(raw))                # re-open (verify() leaves it unusable)
        w, h = img.size
        if w > 8192 or h > 8192 or w * h > MAX_PIXELS:
            raise ValueError(f"image dimensions too large ({w}x{h})")
        img = img.convert("RGB")                         # allocation happens here — now bounded
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    img.save(p, "PNG")                                   # no EXIF carried into the PNG
    return p


def state():
    """Fast — readiness + chain + private. The slow beat proposal (Ollama) is a SEPARATE endpoint
    (/api/beats) so the page renders instantly and never blocks on a model load."""
    chain = chain_or_none()
    return {"session": SESSION, "readiness": readiness(), "chain": chain,
            "private": L.ST.is_private(SESSION) or bool(chain and chain.get("private"))}


def beats():
    chain = chain_or_none()
    if chain is None:
        return []
    try:
        return L.propose(L.context_for(SESSION))   # live, schema-validated + red-lined
    except Exception:
        return []


# ---------------- page (instrument glass; status panel / keyhole register) ----------------
PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content=dark><meta name=csrf content="__CSRF__">
<title>Lucid · AgentOS</title><style>
:root{--inst-base:#12141c;--inst-deep:#161a28;--inst-horizon:#1a2238;--inst-text:#e6e9f0;
--inst-muted:#8a90a0;--inst-label:#7a8090;--inst-blue:#7aa2ff;--brand-warm:#e0884f;
--glass:rgba(38,42,54,.46);--hairline:rgba(255,255,255,.07);--up:#74d39a;--idle:#757c8e;--red:#ec7676}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;color:var(--inst-text);
background:linear-gradient(180deg,var(--inst-horizon),var(--inst-base) 60%);min-height:100vh}
.wrap{max-width:760px;margin:0 auto;padding:32px 20px}
h1{font-size:1.4rem;font-weight:600;margin:0 0 2px}.sub{color:var(--inst-label);font-size:.85rem;margin-bottom:24px}
.card{background:var(--glass);border:1px solid var(--hairline);border-radius:16px;padding:18px 20px;margin:14px 0;backdrop-filter:blur(14px)}
.ready{display:flex;gap:18px;flex-wrap:wrap;font-size:.85rem}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;vertical-align:middle}
.on{background:var(--up)}.off{background:transparent;border:1.5px solid var(--idle)}
.beat{display:block;width:100%;text-align:left;background:rgba(122,162,255,.07);border:1px solid var(--hairline);
color:var(--inst-text);border-radius:12px;padding:12px 14px;margin:8px 0;cursor:pointer;font:inherit}
.beat:hover{border-color:var(--inst-blue)}.beat b{display:block;color:var(--inst-blue);margin-bottom:2px}
.beat small{color:var(--inst-muted)}
input[type=text]{width:100%;background:var(--inst-deep);border:1px solid var(--hairline);color:var(--inst-text);
border-radius:12px;padding:11px 14px;font:inherit}
.note{color:var(--inst-label);font-size:.82rem}.warn{color:var(--brand-warm)}
a{color:var(--inst-blue)} .clip{font-size:.82rem;color:var(--inst-muted);margin:4px 0}
</style></head><body><div class=wrap>
<h1>Lucid <span class=note style="font-weight:400">· interactive dream loop</span></h1>
<div class=sub>Watch a clip, choose what happens next — the story picks up from the last frame.
<span class=warn>spike (ADR-0015): generation runs through the VRAM lease; never co-resident.</span></div>
<div id=app><div class=card>loading…</div></div>
</div><script>
async function load(){
 const s=await (await fetch('/api/state')).json();const r=s.readiness;const a=document.getElementById('app');
 const dot=b=>`<span class="dot ${b?'on':'off'}"></span>`;
 let h=`<div class=card><div class=ready>
   <span>${dot(r.coordinator)}coordinator (lease)</span>
   <span>${dot(r.comfyui)}ComfyUI</span>
   <span>${dot(r.ollama)}Ollama (narrator)</span></div>`;
 if(!r.can_dream)h+=`<div class=note style="margin-top:10px">Can't dream right now — ${r.why.join('; ')}. The loop fails open to the ambient shader.</div>`;
 h+=`</div>`;
 if(s.private)h+=`<div class=card style="border-color:var(--brand-warm)">
   <b style="color:var(--brand-warm)">🔒 Private session</b>
   <div class=note style="margin-top:4px">Ephemeral — sealed in RAM (tmpfs), not saved, not shown on the status hub, no wallpaper. Burned on logout.</div>
   <button class=beat style="margin-top:10px;border-color:var(--brand-warm)" onclick='burnit()'>🔥 Burn this dream now</button></div>`;
 if(!s.chain){h+=`<div class=card><b>Start a dream</b>
   <div class=note style="margin-top:6px">Begin an interactive dream — then choose what happens next, one beat at a time.</div>
   <label style="display:block;margin:12px 0">Opening image <span class=note>(optional — an abstract frame is used if you don't pick one)</span><br>
     <input type=file id=img accept="image/*" style="margin-top:6px;color:var(--inst-muted);max-width:100%"></label>
   <label style="display:flex;gap:9px;align-items:flex-start;margin:12px 0;cursor:pointer">
     <input type=checkbox id=priv style="margin-top:3px">
     <span><b style="color:var(--brand-warm)">🔒 Private session</b>
     <span class=note>— ephemeral, sealed in RAM, not saved, not on the status hub, auto-burned on logout.</span></span></label>
   <button class=beat onclick='startDream()' id=startbtn>✦ Begin a dream</button>
   <div id=startmsg class=note style="margin-top:10px">An uploaded image is checked for real-person likeness before use (ADR-0017); its EXIF metadata is stripped.</div></div>`;}
 else{
   const n=s.chain.nodes;
   h+=`<div class=card><b>Your dream so far</b> · ${n.length} frame(s)`;
   n.forEach(x=>{h+=`<div class=clip>#${x.id} ${x.label}${x.clip?' · '+x.clip.split('/').pop():''}</div>`});
   h+=`</div><div class=card><b>What happens next?</b>
     <div id=beats><div class=note>considering the next moves…</div></div>`;
   h+=`<input id=own type=text placeholder="…or type what happens next" onkeydown="if(event.key==='Enter')dreamOwn()">`;
   if(!r.can_dream)h+=`<div class=note warn style="margin-top:8px">Choosing is disabled until the loop is ready (above).</div>`;
   h+=`</div>`;
 }
 a.innerHTML=h;
 if(s.chain&&r.can_dream)loadBeats();
}
let BEATS=[];
async function loadBeats(){
 const el=document.getElementById('beats');if(!el)return;
 const j=await (await fetch('/api/beats')).json();BEATS=j.beats||[];
 if(!BEATS.length){el.innerHTML='<div class=note>No suggestions — type your own.</div>';return;}
 el.innerHTML=BEATS.map((b,i)=>`<button class=beat onclick='dream(${i})'><b>${b.label}</b><small>${b.prompt}</small></button>`).join('');
}
const CSRF=document.querySelector('meta[name=csrf]').content;
async function post(body){
 const res=await fetch('/api/dream',{method:'POST',headers:{'Content-Type':'application/json','X-Lucid-Token':CSRF},body:JSON.stringify(body)});
 const j=await res.json();if(j.error)alert(j.error);load();
}
function dream(i){const b=BEATS[i];if(b)post({prompt:b.prompt,label:b.label})}  // send the shown prompt, not a stale index
function dreamOwn(){const v=document.getElementById('own').value.trim();if(v)post({prompt:v,label:'custom'})}
function fileB64(f){return new Promise(r=>{const rd=new FileReader();rd.onload=()=>r(rd.result.split(',')[1]);rd.readAsDataURL(f);});}
async function startDream(consent){
 const priv=document.getElementById('priv').checked, f=document.getElementById('img').files[0];
 const msg=document.getElementById('startmsg'), btn=document.getElementById('startbtn');
 const body={private:priv};
 if(f){body.image_b64=await fileB64(f);body.consent=!!consent;msg.textContent='🔎 checking your image for real-person likeness…';btn.disabled=true;}
 const j=await (await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json','X-Lucid-Token':CSRF},body:JSON.stringify(body)})).json();
 btn.disabled=false;
 if(j.blocked){
   if(j.requires_consent){if(confirm(j.reason+'\\n\\nContinue?'))return startDream(true);msg.textContent='Cancelled.';return;}
   msg.textContent='🚫 '+j.reason;return;  // hard block (e.g. possible minor) — not overridable
 }
 if(j.error){msg.textContent=j.error;return;}
 load();
}
async function burnit(){if(!confirm('Burn this private dream now? This wipes every trace and cannot be undone.'))return;
 const j=await (await fetch('/api/burn',{method:'POST',headers:{'X-Lucid-Token':CSRF}})).json();
 alert('Burned '+(j.burned||0)+' sink(s).'+((j.failed&&j.failed.length)?' FAILED: '+j.failed.join('; '):''));load();}
load();setInterval(load,5000);
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

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.replace("__CSRF__", CSRF), "text/html; charset=utf-8")
        elif path == "/healthz":
            self._send(200, "ok", "text/plain")
        elif path == "/api/state":
            self._send(200, json.dumps(state()), "application/json")
        elif path == "/api/beats":   # slow (Ollama) — fetched separately so the page never blocks
            self._send(200, json.dumps({"beats": beats()}), "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/dream", "/api/burn", "/api/start"):
            return self._send(404, "not found", "text/plain")
        # CSRF: a state-changing POST must carry the per-process token embedded in the page (a
        # cross-origin page can't read it). Fail closed. Defense-in-depth: reject a bad Origin too.
        if not hmac.compare_digest(self.headers.get("X-Lucid-Token", ""), CSRF):
            return self._send(403, json.dumps({"error": "missing/invalid CSRF token"}), "application/json")
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGIN_OK:
            return self._send(403, json.dumps({"error": "cross-origin refused"}), "application/json")
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n > MAX_BODY:   # reject an oversized body BEFORE reading it into memory (security)
            return self._send(413, json.dumps({"error": "payload too large"}), "application/json")
        try:
            req = json.loads(self.rfile.read(n) or "{}") if n else {}
        except Exception:
            return self._send(400, json.dumps({"error": "bad request"}), "application/json")
        if path == "/api/burn":   # wipe the private session's every sink (ADR-0016)
            removed, failed = L.burn(SESSION)
            return self._send(200, json.dumps({"ok": not failed, "burned": len(removed),
                                               "failed": failed}), "application/json")
        if path == "/api/start":  # begin a dream; uploaded image (B2-gated in start) or synthetic frame
            private = bool(req.get("private"))
            consent = bool(req.get("consent"))
            img_b64 = req.get("image_b64")
            if not _START_SEM.acquire(blocking=False):   # bound concurrent decode + vision-model loads
                return self._send(429, json.dumps({"error": "busy — try again in a moment"}), "application/json")
            try:
                L.ST.clear(SESSION)   # clean any prior session of this name before a fresh start
                if img_b64:
                    if not readiness()["ollama"]:   # B2 needs the vision model; fail fast, don't hang
                        return self._send(200, json.dumps({"error": "can't check the image — the narrator (Ollama) is unavailable"}), "application/json")
                    try:
                        raw = base64.b64decode(img_b64, validate=True)
                        if len(raw) > MAX_IMG:
                            raise ValueError("image too large (max 20 MB)")
                        seed = _decode_seed(raw)
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"invalid image: {e}"}), "application/json")
                    try:
                        L.start(SESSION, seed, private=private, consent=consent)  # B2 runs INSIDE start()
                    except L.SeedBlocked as e:
                        return self._send(200, json.dumps({"blocked": True, **e.verdict.as_dict()}), "application/json")
                    except Exception as e:
                        return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                    finally:
                        try:
                            os.remove(seed)
                        except OSError:
                            pass
                    return self._send(200, json.dumps({"ok": True, "private": private}), "application/json")
                # no image -> a server-generated abstract opening (trusted; no real person)
                seed = _synthetic_opening()
                try:
                    L.start(SESSION, seed, private=private, _trusted_seed=True)
                except Exception as e:
                    return self._send(200, json.dumps({"error": f"start failed: {e}"}), "application/json")
                finally:
                    try:
                        os.remove(seed)
                    except OSError:
                        pass
                return self._send(200, json.dumps({"ok": True, "private": private}), "application/json")
            finally:
                _START_SEM.release()
        rd = readiness()
        if not rd["can_dream"]:
            return self._send(200, json.dumps({"error": "not ready — " + "; ".join(rd["why"])}),
                              "application/json")
        # resolve the prompt (typed or chosen), then drive ONE gated, leased turn via lucid_linear.
        prompt, label = req.get("prompt"), req.get("label", "custom")
        if prompt is None:
            beats = L.propose(L.context_for(SESSION))
            idx = int(req.get("choose", 0))
            if not beats or idx >= len(beats):
                return self._send(200, json.dumps({"error": "that beat expired — pick again"}),
                                  "application/json")
            prompt, label = beats[idx]["prompt"], beats[idx]["label"]
        if S.gate_prompt(prompt) is None:
            return self._send(200, json.dumps({"error": "that beat was blocked by the red-line gate"}),
                              "application/json")
        try:
            node = L.step(SESSION, prompt, label)
            return self._send(200, json.dumps({"ok": True, "node": node}), "application/json")
        except SystemExit as e:
            return self._send(200, json.dumps({"error": str(e)}), "application/json")
        except Exception as e:
            return self._send(200, json.dumps({"error": f"dream failed (fail-open): {e}"}), "application/json")


def main():
    try:  # sweep private clip/frame orphans whose tmpfs session is gone (crash/logout) — ADR-0016
        reaped = L.ST.reap_orphans()
        if reaped:
            print(f"reaped {len(reaped)} orphaned private session(s): {reaped}", flush=True)
    except Exception as e:
        print(f"orphan reap skipped: {e}", flush=True)
    print(f"Lucid web → http://{HOST}:{PORT}  (session '{SESSION}')", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
