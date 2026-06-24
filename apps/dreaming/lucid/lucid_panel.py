#!/usr/bin/env python3
"""AgentOS Lucid — minimal panel for the dream loop spike (ADR-0014).

THROWAWAY SPIKE surface: a stdlib HTTP panel (no deps, no QML yet) to *feel* the
loop — play the current clip, pick one of the LLM's "what happens next" buttons
(or type your own), watch the story branch forward. The shipped surface is QML +
notification-as-control behind the consent gate (ADR-0009/0014); this is just to
prove the interaction.

Run:  python3 lucid_panel.py <session>     then open http://127.0.0.1:8765
Start a session first:  python3 lucid_engine.py start <session> --image <png>
"""
import html
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import lucid_engine as L

PORT = int(os.environ.get("LUCID_PORT", "8765"))
SESSION = sys.argv[1] if len(sys.argv) > 1 else "demo"
STATE = {"status": "idle", "beats": [], "beats_for": None, "error": None}
LOCK = threading.Lock()


def _beats_for_current():
    """Lazily propose beats for the current node; cache by node id."""
    tree = L.load_tree(SESSION)
    cur = tree["current"]
    if STATE["beats_for"] != cur:
        ctx = L.story_context(tree, cur)
        try:
            STATE["beats"] = L.propose_beats(ctx)
            STATE["beats_for"] = cur
        except Exception as e:  # noqa: BLE001
            STATE["error"] = f"beat-gen failed: {e}"
            STATE["beats"] = []
    return tree, cur


def _advance(prompt, label):
    with LOCK:
        if STATE["status"] == "dreaming":
            return
        STATE["status"], STATE["error"] = "dreaming", None

    def work():
        try:
            L.step(SESSION, prompt, label)
            STATE["beats_for"] = None  # force re-propose for the new node
        except Exception as e:  # noqa: BLE001
            STATE["error"] = str(e)
        finally:
            STATE["status"] = "idle"
    threading.Thread(target=work, daemon=True).start()


PAGE = """<!doctype html><meta charset=utf-8><title>Lucid · {session}</title>
<style>
 body{{background:#0b0d12;color:#e6e9ef;font:15px/1.5 system-ui;margin:0;
   display:flex;flex-direction:column;align-items:center;gap:18px;padding:24px}}
 video,img.frame{{max-height:62vh;border-radius:12px;background:#000;box-shadow:0 6px 30px #0008}}
 h1{{font-weight:600;font-size:15px;color:#8b93a7;letter-spacing:.08em;text-transform:uppercase}}
 .beats{{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;max-width:760px}}
 button{{background:#171c26;color:#e6e9ef;border:1px solid #28303f;border-radius:10px;
   padding:10px 16px;font:inherit;cursor:pointer}}
 button:hover{{border-color:#4c8bf5;background:#1c2330}}
 .sub{{font-size:12px;color:#6b7384;max-width:520px;text-align:center}}
 form{{display:flex;gap:8px}} input{{background:#11151d;border:1px solid #28303f;
   border-radius:10px;color:#e6e9ef;padding:10px 14px;width:360px;font:inherit}}
 .dreaming{{color:#9db4f5}} .err{{color:#f5736b;max-width:560px}}
</style>
<body data-dreaming="{dreaming}">
<h1>Lucid · {session}</h1>
{body}
<p class=sub>spike · SFW · no consent-gate/lease yet (ADR-0014). Each beat ≈ a few minutes.</p>
<script>
 async function poll(){{
   const s = await (await fetch('/state')).json();
   if(s.status==='idle'){{ location.reload(); }}
 }}
 if(document.body.dataset.dreaming==='1') setInterval(poll,2500);
</script>
"""


def render():
    tree, cur = _beats_for_current()
    node = tree["nodes"][str(cur)]
    if STATE["status"] == "dreaming":
        body = ("<p class=dreaming>✦ dreaming the next moment… (a few minutes)</p>"
                "<img class=frame src='/frame'>")
        return PAGE.format(session=html.escape(SESSION), body=body, dreaming="1")
    # idle
    if node.get("clip"):
        media = "<video src='/clip' autoplay loop muted controls></video>"
    else:
        media = "<img class=frame src='/frame'>"
    err = f"<p class=err>{html.escape(STATE['error'])}</p>" if STATE["error"] else ""
    btns = "".join(
        f"<button onclick=\"go({i})\">{html.escape(b['label'])}</button>"
        for i, b in enumerate(STATE["beats"]))
    if not btns:
        btns = "<span class=sub>(no suggestions — type your own below)</span>"
    body = f"""{media}
 <p class=sub>What happens next?</p>
 <div class=beats>{btns}</div>
 <form onsubmit="return custom(event)">
   <input name=p placeholder="✎ or type your own…" autocomplete=off>
   <button>Go</button>
 </form>{err}
 <script>
  function go(i){{ fetch('/advance',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'choice='+i}}).then(()=>location.reload()); }}
  function custom(e){{ e.preventDefault(); const p=e.target.p.value.trim(); if(!p)return false;
    fetch('/advance',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'prompt='+encodeURIComponent(p)}}).then(()=>location.reload()); return false; }}
 </script>"""
    return PAGE.format(session=html.escape(SESSION), body=body, dreaming="0")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path):
        if not path or not os.path.exists(path):
            self._send(404, "text/plain", b"not found"); return
        with open(path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(path)[1].lower()
        ctype = {".mp4": "video/mp4", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(ext, "application/octet-stream")
        self._send(200, ctype, data)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            self._send(200, "text/html; charset=utf-8", render().encode())
        elif p == "/state":
            self._send(200, "application/json",
                       json.dumps({"status": STATE["status"]}).encode())
        elif p == "/clip":
            tree = L.load_tree(SESSION)
            self._serve_file(tree["nodes"][str(tree["current"])].get("clip"))
        elif p == "/frame":
            tree = L.load_tree(SESSION)
            fr = tree["nodes"][str(tree["current"])].get("out_frame")
            self._serve_file(os.path.join(L.INPUT_DIR, fr) if fr else None)
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if urlparse(self.path).path != "/advance":
            self._send(404, "text/plain", b"not found"); return
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode())
        if "prompt" in form:
            _advance(form["prompt"][0], "custom")
        elif "choice" in form and STATE["beats"]:
            b = STATE["beats"][int(form["choice"][0])]
            _advance(b["prompt"], b["label"])
        self._send(202, "application/json", b'{"ok":true}')


def main():
    print(f"[lucid] panel for session '{SESSION}' -> http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
