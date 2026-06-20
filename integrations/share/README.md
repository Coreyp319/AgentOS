# AgentOS Share — phone → your-box ingest hub (ADR-0027)

Share a photo from your iPhone into one of your box's own sinks, **chosen at share time**:

| Door | Trust class | What it does | Status |
|------|-------------|--------------|--------|
| **Dream** | data → renderer | start a Lucid dream from the photo (B2 likeness gate on the box) | ✅ Phase 0/1 |
| **Ask Hermes** | data → orchestrator | photo + caption → a Hermes chat message (on-box key) | ✅ Phase 1 (set key) |
| **Hermes task** | data → orchestrator | add a kanban task | ⏳ Phase 2 (honest "not yet") |
| **Claude** | instructions → actor | save an **inert** proposal for desktop approval | 🔒 Phase 3 (never executes here) |

The hub is a self-contained `spikes/dreaming/lucid/lucid_share.py` (stdlib + PIL) on
`127.0.0.1:8770`, fronted tailnet-only by `tailscale serve`. It is a **new, separate service** —
it does *not* live inside `lucid_web.py` (the council's eventual "dedicated service," pulled
forward to v0 because the Lucid web tree is under concurrent rewrite, and because code-execution
must not share the dream loop's lifecycle — see ADR-0027 §2). The only edit to `lucid_web.py` is a
~15-line `X-Share-Key` acceptance on `/api/start` for the Dream door.

## Security model (ADR-0027)
- **tailnet membership is the real boundary.** `serve`, never `funnel` — tailnet-only.
- The **PWA** (a browser) authenticates with a per-process **CSRF token**; the **iOS Shortcut**
  (not a browser) uses a file-backed **`X-Share-Key`** (`~/.config/agentos/share.key`, 0600).
  Neither is claimed to be a boundary on its own — they're defense-in-depth.
- **Hermes' API key never leaves the box** and is never sent to the phone; the hub reads it from
  its own environment. Hermes `:8642` and ComfyUI `:8188` stay loopback-only.
- **Every path strips EXIF/GPS** before anything else sees the image.
- The **Claude door never spawns `claude -p`** — it writes an inert, labeled-untrusted proposal
  file. Execution is Phase 3, behind its own blocking review gate (privacy/security/reversibility/
  resource-safety/ux on record first).

## Install (on the box)
```sh
# 1. the hub service (new user service, additive)
cp integrations/share/agentos-share.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agentos-share.service

# 2. (optional) wire the Hermes-chat door's on-box key
cp integrations/share/share.env.example ~/.config/agentos/share.env
chmod 600 ~/.config/agentos/share.env
$EDITOR ~/.config/agentos/share.env          # paste HERMES_API_KEY
systemctl --user restart agentos-share.service

# 3. expose it tailnet-only
integrations/agentosd-remote.sh up           # now serves :8770 too

# 4. the Dream door needs lucid_web to pick up the X-Share-Key hook
systemctl --user restart agentos-lucid.service
```

## Use it from the iPhone
Two paths — see **`ios-shortcut.md`**:
- **Shortcut** (Photos → Share → *Share to box* → pick a door) — the real Share-sheet experience.
- **PWA** (`https://4090.tail096c29.ts.net:8770/` → Add to Home Screen) — an installable app with
  its own photo/camera picker and the four doors.

## Phase status
- **Phase 0/1 (this):** PWA + Shortcut + Dream door + Hermes-chat + Claude inert-proposal.
- **Phase 2:** Hermes-task bridge (shell Hermes' own `kanban` CLI; needs argv sign-off).
- **Phase 3:** Claude execution as a human-approved proposal queue (blocking review gate).
