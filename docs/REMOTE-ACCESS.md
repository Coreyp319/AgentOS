# Remote access — AgentOS on the go

How to reach AgentOS from your phone, securely. Recorded 2026-06-19.

There are **two complementary remote modes**, and you want both:

| Mode | Reach it via | Best for |
|---|---|---|
| **Conversational** | Telegram → Hermes gateway | "how's my GPU / summarize this / kick off a job" |
| **Direct UI** | Tailscale → the localhost web UIs | the visual surfaces — **Lucid dreaming**, status/models panels, the board |

---

## The security model (read first)

Every AgentOS web UI is **unauthenticated** — it trusts "localhost = you" (Lucid is
NSFW-capable; the Hermes board can drive the agent). So **the remote-access layer IS the
auth.** Therefore:

- **NEVER** port-forward on the router, bind these services to `0.0.0.0`, or use a public
  tunnel (public ngrok, etc.). That exposes dangerous unauthenticated UIs to the internet.
- **DO** use a private overlay network where device membership is the auth, and keep every
  service bound to `127.0.0.1`. A reverse proxy on the overlay fronts them with TLS.

All AgentOS surfaces are localhost-bound today (the safe default):

| Port | Service |
|---|---|
| 8765 | Lucid — interactive dream-loop web UI |
| 9119 | Hermes dashboard / kanban board |
| 9123 | AgentOS status panel (boot-health) |
| 9124 | AgentOS model audit panel |
| 8642 | Hermes REST control API (Bearer-auth; see gpu-coordinator/DEPLOY.md) |
| 8188 | ComfyUI (do **not** expose — heavy, unauth) |

Tunnel-safety check (2026-06-19): Lucid (`web/src/api.ts`) and the panels use
**relative / same-origin** fetches — no hardcoded `localhost` — so they work behind a
port-preserving proxy. Use **port-per-UI** proxying (below) so root-absolute paths like
`/api/...` resolve correctly.

---

## Mode 1 — Conversational (already live)

The Telegram bot is connected to the Hermes gateway; each message runs a full agent turn on
the box. As of 2026-06-19 it also carries **AgentOS GPU introspection** (the `agentos` MCP —
`gpu_status` / `gpu_residency` / `gpu_why`; see `integrations/hermes/gpu-coordinator/DEPLOY.md`
→ "wire AgentOS introspection"). Try: *"how much GPU VRAM is free right now?"* — verified
end-to-end (the agent calls `gpu_status` and answers from live telemetry).

Nothing more to set up for this mode. Email ("check my inbox & summarize") is a separate,
unconfigured skill — needs `himalaya` installed + an account, not covered here.

---

## Mode 2 — Direct UI via Tailscale (recommended)

Tailscale solves both problems at once: **NAT traversal** (reach the box from cellular) and
**secure exposure** (`tailscale serve` proxies a localhost port over HTTPS to *only your
authenticated tailnet devices* — services stay `127.0.0.1`-bound).

### On the box (sudo)

```bash
sudo pacman -S tailscale
sudo systemctl enable --now tailscaled
sudo tailscale up          # opens a login URL — authenticates this box to your tailnet
```

### On your phone

Install the Tailscale app, log into the **same account**.

### Expose the UIs (port-per-UI keeps each app at its own root)

```bash
tailscale serve --bg --https=8765 http://127.0.0.1:8765   # Lucid
tailscale serve --bg --https=9123 http://127.0.0.1:9123   # status panel
tailscale serve --bg --https=9124 http://127.0.0.1:9124   # models panel
tailscale serve --bg --https=9119 http://127.0.0.1:9119   # Hermes board
tailscale serve status                                     # confirm mappings
```

Or just run the helper (does all of the above + prints the phone URLs):

```bash
integrations/agentosd-remote.sh up       # serve the UIs   (down | status)
```

`serve` CLI flags vary slightly by version — check `tailscale serve --help` if one differs.
From the phone: `https://<your-box>.<your-tailnet>.ts.net:8765/` (Lucid), `:9123`, etc. TLS
via the tailnet cert; reachable only by your devices; nothing public.

### Interactive-app origin caveat (learned 2026-06-19)

The read-only panels work over `serve` as-is. Apps with **localhost-origin security** need the
tailnet name explicitly allowed, or their POST actions fail under a different hostname:

- **Lucid (8765)** rejects the tailnet `Origin` (a defense-in-depth check on top of its
  per-process CSRF token), so the page loads but actions 403 — the UI then shows a "csrf"/reload
  loop. Fix: allow the tailnet origin via env — a systemd drop-in for `agentos-lucid.service` at
  `~/.config/systemd/user/agentos-lucid.service.d/override.conf`:

  ```ini
  [Service]
  Environment=LUCID_EXTRA_ORIGINS=https://<your-box>.<tailnet>.ts.net:8765
  ```

  then `systemctl --user daemon-reload && systemctl --user restart agentos-lucid.service`.
  (Backed by `LUCID_EXTRA_ORIGINS` in `spikes/dreaming/lucid/lucid_web.py`.)

- **Hermes board (9119)** has a DNS-rebinding guard that rejects any non-loopback `Host` (400);
  `--insecure` does NOT relax it. Its intended remote mode is a non-loopback bind + OAuth auth
  gate — until that's set up, drive the board via Telegram. Don't `serve` it expecting it to work. **Do not** serve 8188
(ComfyUI) or, unless you mean to, 8642 (the REST API already has its own Bearer auth).

### Teardown

```bash
tailscale serve --https=8765 off   # repeat per port
sudo tailscale down                # leave the tailnet
```

---

## Mode 2 (alt) — SSH local-forward (zero install, same-network)

`ssh` is already on the box. From a phone SSH client (Termius/Blink):

```bash
ssh -L 8765:localhost:8765 -L 9123:localhost:9123 corey@<box>
```

then open `localhost:8765` on the phone. **Caveat:** SSH alone does not solve reachability —
it works on your home LAN or with a public/forwarded SSH endpoint, but for true cellular "on
the go" you still need NAT traversal, which is what Tailscale provides. Quick win for
same-network; Tailscale is the real "anywhere" answer.

---

## Hardening — the tailnet is the auth (do these)

The exposed UIs are unauthenticated; their security rests entirely on the tailnet. Verified
clean 2026-06-19: `serve` (not `funnel`), api_server localhost-only, secrets `0600`, Telegram
fail-closed + allowlisted to one user. To keep it that way:

1. **Enable Tailscale account 2FA** (and on the SSO it's tied to). A compromised account or an
   added/malicious tailnet device = direct reach to Lucid (NSFW + GPU) and the panels. Highest-
   leverage item.
2. **Stay on `serve`, never `funnel`.** `serve` = tailnet-only; `funnel` = public internet. Check
   with `tailscale serve status` — every line must say "tailnet only".
3. **Restrict the serve ports with an ACL** (matters most once you add a user or share a node). In
   the admin console → Access Controls, apply a policy like this, then assign `tag:agentos` to the
   box:

   ```json
   {
     "tagOwners": { "tag:agentos": ["autogroup:admin"] },
     "acls": [
       { "action": "accept", "src": ["autogroup:member"],
         "dst": ["tag:agentos:8765,9123,9124,9119,22"] },
       { "action": "accept", "src": ["autogroup:member"], "dst": ["autogroup:self:*"] }
     ]
   }
   ```

   Only your own devices reach the AgentOS ports; a shared/added user can't, even on the tailnet.
4. **Never set `GATEWAY_ALLOW_ALL_USERS=true`** — it bypasses the Telegram allowlist (the bot is
   currently fail-closed and locked to a one-entry allowlist).
5. **Keep api_server (:8642) localhost-only** and the board (9119) host-guarded / OAuth-gated —
   don't `serve` either without its auth.

## Why not just drive Lucid from Telegram?

Lucid is visual, interactive-branching, heavy (minutes/beat through the VRAM lease), and B2
safety-gated — a text chat is an awkward, slow interface for it. The dreaming experience
belongs in the web UI over Tailscale (Mode 2). Telegram (Mode 1) is for commanding and
introspecting, not for watching dreams branch.
