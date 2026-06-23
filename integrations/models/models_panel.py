#!/usr/bin/env python3
"""AgentOS model audit panel — see every model that runs, what it's for, and that it's local.

A calm, read-only web view over the model registry (registry.json — the single source of truth).
For each affiliation it shows the role, purpose, the model, its runtime, size, provenance, a
LOCAL-only trust badge, safety-critical badge, and a LIVE "resident on the GPU now?" dot (Ollama
/api/ps). Loopback-only, stdlib-only, no writes. Opens at http://127.0.0.1:9124.

Edit the affiliations in registry.json, never here — code resolves its model from the same file.
"""
import json
import os
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.environ.get("AGENTOS_MODEL_REGISTRY", os.path.join(HERE, "registry.json"))
HOST = os.environ.get("AGENTOS_MODELS_HOST", "127.0.0.1")
PORT = int(os.environ.get("AGENTOS_MODELS_PORT", "9124"))
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


def _resident_ollama():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=2) as r:
            return {m.get("name", "") for m in json.load(r).get("models", [])}
    except Exception:
        return set()


def _vram():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4)
        free, total = (int(x) for x in out.stdout.strip().splitlines()[0].split(","))
        return {"free_gb": round(free / 1024, 1), "total_gb": round(total / 1024, 1)}
    except Exception:
        return {"free_gb": None, "total_gb": None}


def audit():
    try:
        reg = json.loads(open(REGISTRY).read())
    except Exception as e:
        return {"models": [], "error": f"registry: {e}", "vram": _vram()}
    resident = _resident_ollama()
    models = []
    for m in reg.get("models", []):
        name = m.get("model", "")
        # an ollama model is "resident" if a loaded name starts with it (tags/digests vary)
        live = any(r == name or r.startswith(name.split(":")[0]) for r in resident) if m.get("runtime") == "ollama" else None
        models.append({**m, "resident": live})
    return {"models": models, "vram": _vram(), "registry_path": REGISTRY}


PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content=dark>
<title>Models · AgentOS</title><style>
:root{--inst-base:#12141c;--inst-deep:#161a28;--inst-horizon:#1a2238;--inst-text:#e6e9f0;
--inst-muted:#8a90a0;--inst-label:#878c9b;--inst-blue:#9b82e0;--inst-warm:#ff9957;--brand-warm:#e0884f;
--glass:rgba(38,42,54,.46);--hairline:rgba(255,255,255,.07);--up:#74d39a;--idle:#757c8e;
--radius:16px;--blur:14px}
@media (prefers-reduced-transparency:reduce){:root{--glass:rgba(24,28,40,.94)}.card{backdrop-filter:none}}
*{box-sizing:border-box}body{margin:0;font:15px/1.55 system-ui,sans-serif;color:var(--inst-text);min-height:100vh;
background:radial-gradient(1100px 600px at 85% -12%,color-mix(in srgb,var(--inst-blue) 8%,transparent),transparent 60%),
 linear-gradient(160deg,var(--inst-base),var(--inst-deep) 45%,var(--inst-horizon))}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px}
h1{font-size:1.4rem;font-weight:600;margin:0 0 2px}.subtle{color:var(--inst-label);font-weight:400}
.sub{color:var(--inst-muted);font-size:.83rem;margin:6px 0 18px;max-width:64ch}
.trust{display:flex;align-items:center;gap:10px;background:color-mix(in srgb,var(--up) 9%,transparent);
 border:1px solid color-mix(in srgb,var(--up) 30%,transparent);border-radius:var(--radius);padding:12px 16px;margin-bottom:8px;font-size:.85rem}
.trust b{color:var(--up)}
.vram{color:var(--inst-muted);font-size:.8rem;margin:6px 0 18px}
.card{background:var(--glass);border:1px solid var(--hairline);border-radius:var(--radius);
 padding:16px 18px;margin:12px 0;backdrop-filter:blur(var(--blur))}
.role{font-weight:600;font-size:1.02rem;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.badge{font-size:.68rem;font-weight:600;letter-spacing:.02em;padding:2px 8px;border-radius:999px;
 border:1px solid var(--hairline);color:var(--inst-muted);text-transform:uppercase}
.badge.safety{color:var(--brand-warm);border-color:color-mix(in srgb,var(--brand-warm) 45%,transparent)}
.badge.rt{color:var(--inst-blue);border-color:color-mix(in srgb,var(--inst-blue) 35%,transparent)}
.model{font-family:ui-monospace,monospace;color:var(--inst-blue);font-size:.9rem;margin:6px 0}
.purpose{color:var(--inst-muted);font-size:.86rem;margin:4px 0 10px}
.meta{display:flex;gap:16px;flex-wrap:wrap;font-size:.78rem;color:var(--inst-label);border-top:1px solid var(--hairline);padding-top:10px}
.meta b{color:var(--inst-muted);font-weight:500}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.live{background:var(--up)}.dim{background:transparent;border:1.5px solid var(--idle)}
.note{color:var(--inst-label);font-size:.76rem;margin-top:8px;font-style:italic}
a{color:var(--inst-blue)}
</style></head><body><div class=wrap>
<h1>Models <span class=subtle>· what runs, and why</span></h1>
<div class=sub>Every model AgentOS uses, the job it does, and where it came from. This is read from one
registry file — change a model there and the whole system follows.</div>
<div class=trust><span>🔒</span><div><b>All local.</b> Every model here runs on this machine. Nothing about
 your dreams, prompts, or images is sent anywhere.</div></div>
<div id=vram class=vram></div>
<div id=app><div class=card>loading…</div></div>
<div class=note id=path></div>
</div><script>
const E=s=>(s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function card(m){
 const live=m.resident===true, rtKnown=m.runtime==='ollama';
 const dot=rtKnown?`<span class="dot ${live?'live':'dim'}"></span>${live?'loaded now':'not loaded'}`:'loads on demand';
 return `<div class=card><div class=role>${E(m.role)}`
  +(m.safety_critical?`<span class="badge safety">⚠ safety-critical</span>`:'')
  +`<span class="badge rt">${E(m.runtime)}</span></div>`
  +`<div class=model>${E(m.model)}</div>`
  +`<div class=purpose>${E(m.purpose)}</div>`
  +(m.notes?`<div class=note>${E(m.notes)}</div>`:'')
  +`<div class=meta><span><b>${E(m.size_gb)} GB</b></span>`
  +`<span>${dot}</span>`
  +`<span>${m.local?'🔒 local':'⚠ remote'}</span>`
  +`<span><b>from</b> ${E(m.source)}</span>`
  +(m.used_by&&m.used_by.length?`<span><b>used by</b> ${E(m.used_by.join(', '))}</span>`:'')
  +`</div>`
  +(m.alternatives&&m.alternatives.length?`<div class=note style="margin-top:9px">swappable to: ${E(m.alternatives.join(' · '))}</div>`:'')
  +`</div>`;}
async function load(){let d;try{d=await(await fetch('/api/models')).json();}catch(e){return;}
 const v=d.vram, vr=document.getElementById('vram');
 vr.textContent=(v&&v.free_gb!=null)?`GPU: ${v.free_gb} GB free of ${v.total_gb} GB — a model only loads when there's room; otherwise the job waits.`:'';
 document.getElementById('app').innerHTML=(d.models||[]).map(card).join('')||'<div class=card>No models registered.</div>';
 document.getElementById('path').textContent='source of truth: '+(d.registry_path||'registry.json');}
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
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/healthz":
            self._send(200, "ok", "text/plain")
        elif path == "/api/models":
            self._send(200, json.dumps(audit()), "application/json")
        else:
            self._send(404, "not found", "text/plain")


def main():
    print(f"AgentOS model audit panel → http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
