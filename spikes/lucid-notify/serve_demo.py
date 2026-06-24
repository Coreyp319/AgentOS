#!/usr/bin/env python3
"""ADR-0047 spike — on-phone test harness for the notify leg.

This is the ONLY part of the spike that must run on Corey's real phone, because
iOS PWA push reliability is the one thing platform docs can't settle. It serves a
tiny installable page + the service worker, lets you grant notification
permission, and proves the two paths you can prove WITHOUT a crypto dep:

  (1) FOREGROUND / installed-app notification: tap "Fire test" → the SW shows a
      content-free notification. This proves SW registration + permission +
      showNotification on the actual device.
  (2) The honest gap: a notification to a *fully-closed* iOS PWA needs real web
      push (APNs), which needs VAPID + crypto (see notify.py WebPushTransport).
      This server reports that it cannot do (2) here, so you FEEL the boundary.

To prove the recommended primary path (Telegram, reaches a closed app), use
notify.py with real creds + --send instead; it needs no phone-side install.

Run on the box:   python3 serve_demo.py            # binds 127.0.0.1:8791
Reach from phone: add :8791 to agentosd-remote.sh `tailscale serve`, open the
                  tailnet HTTPS URL, "Add to Home Screen", launch, grant perms.
NOTE: web push / showNotification on iOS requires the page be opened from the
INSTALLED (home-screen) PWA over HTTPS — Safari-tab will not grant it. Tailscale
serve provides the HTTPS origin.
"""
from __future__ import annotations

import http.server
import json
import socketserver

PORT = 8791

PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<link rel=manifest href=/manifest.webmanifest>
<meta name=theme-color content=#12141c>
<title>Lucid notify spike</title>
<style>
 body{font:16px/1.5 system-ui;background:#12141c;color:#e7e9f0;margin:0;
   padding:max(24px,env(safe-area-inset-top)) 24px 24px;-webkit-text-size-adjust:100%}
 h1{font-size:18px;font-weight:600} button{font:inherit;padding:14px 18px;border-radius:12px;
   border:0;background:#3a5bd9;color:#fff;margin:8px 0;min-height:44px;width:100%}
 .muted{color:#878c9b;font-size:14px} code{color:#9fb4ff} .row{margin:18px 0}
 #log{white-space:pre-wrap;background:#1a1d28;border-radius:10px;padding:12px;font-size:13px;color:#9fb4ff}
</style></head><body>
<h1>🌙 Lucid notify spike</h1>
<p class=muted>Proves the foreground/installed-PWA notification path on this device.
A clip-landed push is <b>content-free</b> by contract — app name + a generic line, no dream title.</p>
<div class=row><button id=perm>1 · Grant notification permission</button></div>
<div class=row><button id=fire>2 · Fire a test "clip landed"</button></div>
<div class=row><button id=close>3 · Now fully close the app and tap Fire from a second device…</button></div>
<p class=muted>Step 3 is the honest boundary: a <i>closed</i> iOS PWA needs real web
push (APNs → VAPID → crypto). This harness can't do that. Telegram (notify.py) can.</p>
<div id=log class=muted>log…</div>
<script>
const log=(m)=>{document.getElementById('log').textContent=m+"\\n"+document.getElementById('log').textContent};
async function reg(){ if(!('serviceWorker'in navigator)){log('no SW support');return null;}
  const r=await navigator.serviceWorker.register('/sw.js'); log('SW registered'); return r; }
document.getElementById('perm').onclick=async()=>{
  const p=await Notification.requestPermission(); log('permission: '+p); await reg(); };
document.getElementById('fire').onclick=async()=>{
  const r=await navigator.serviceWorker.ready;
  const payload=await (await fetch('/payload')).json();
  await r.showNotification('Lucid',{body:payload.body,tag:payload.tag,icon:'/icon-192.png',
    badge:'/icon-192.png',data:{url:payload.url}});
  log('fired (content-free): '+JSON.stringify(payload)); };
reg();
</script></body></html>"""

MANIFEST = json.dumps({
    "name": "Lucid notify spike", "short_name": "LucidSpike", "id": "/",
    "start_url": "/?pwa=1", "scope": "/", "display": "standalone",
    "orientation": "portrait-primary", "background_color": "#12141c",
    "theme_color": "#12141c",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"}],
})

# Reuse the real Lucid payload builder so the harness proves the SAME content-free path.
import notify  # noqa: E402

PAYLOAD = notify._safe_payload(
    notify.dream_grew("spike", "n-test").payload("https://localhost:8791")
)


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Strict-ish CSP; service worker needs same-origin only.
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if path == "/sw.js":
            import pathlib
            return self._send(pathlib.Path(__file__).with_name("sw.js").read_bytes(),
                              "application/javascript")
        if path == "/manifest.webmanifest":
            return self._send(MANIFEST.encode(), "application/manifest+json")
        if path == "/payload":
            return self._send(json.dumps(PAYLOAD).encode(), "application/json")
        if path == "/icon-192.png":
            import pathlib
            here = pathlib.Path(__file__).resolve()
            # Lucid's web/public moves spikes/→apps/ under ADR-0046; try both, and
            # the icon is cosmetic so a miss just 404s without breaking the harness.
            for cand in (
                here.parents[2] / "apps" / "dreaming" / "lucid" / "web" / "public" / "icon-192.png",
                here.parents[2] / "spikes" / "dreaming" / "lucid" / "web" / "public" / "icon-192.png",
            ):
                if cand.exists():
                    return self._send(cand.read_bytes(), "image/png")
        self.send_error(404)

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    print(f"serving notify spike on http://127.0.0.1:{PORT}  (content-free payload below)")
    print(" ", json.dumps(PAYLOAD))
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
