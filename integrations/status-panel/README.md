# AgentOS status panel — boot-health at a glance

A calm, read-only web panel that shows the live state of the whole AgentOS + Nimbus
boot stack: each systemd unit's status (user + system) plus a quick port/HTTP
reachability check for the services that expose one. Per the surface-labor contract
(ADR-0017), the all-clear is silence: it opens at login **only when something needs
attention** — a clean boot stays quiet (the keyhole tray carries the calm). Open it from
the tray, or `http://127.0.0.1:9123`, any time.

On an attention row you can **"Why?"** (a read-only `journalctl` tail, inline — no terminal)
and **"Copy fix"** (the exact `systemctl reset-failed && restart`); a header **"bring stack
up"** copies `apply-all.sh`. A service that *falls over after boot* fires one calm swaync
toast (disable with `AGENTOS_STATUS_NOTIFY=0`). When a service recovers, its row exhales
green — delight made of true state. The server stays strictly read-only; the human disposes.

`http://127.0.0.1:9123`

## Install / remove
```
./apply.sh      # install + enable + start the --user service, and set it as the login landing page
./restore.sh    # remove the service + the login opener
```
`apply.sh` retires the older dashboard-only login opener (`hermes-dashboard-open.desktop`);
the panel links out to the Hermes dashboard and ComfyUI instead, so it's the new front door.

## How it works
- `status_panel.py` — a tiny stdlib-only HTTP server (no venv, no deps). Serves
  `panel.html` and a `/status.json` it computes on demand from `systemctl show` /
  `list-units` and short-timeout HTTP probes. Loopback-only, no write endpoints.
- `services.json` — the **editable catalog**. Add a service by appending an entry:
  ```json
  { "id": "my-svc", "name": "My service", "group": "Desktop QoL",
    "scope": "user", "unit": "my-svc.service", "url": "http://…", "health": "http://…", "desc": "…" }
  ```
  - `scope`: `user` (default) or `system`.
  - `unit`: exact unit id. Use `match` (a glob) instead for awkward names like the
    xdg-autostart wallpaper unit (`app-*hexen*wallpaper*@autostart.service`).
  - `url`: shows an "Open ↗" link. `health`: a URL probed for reachability.
  - `group`: must be one of the `groups` array (controls section order).

## Status vocabulary
- `up` (green, filled) — a daemon running; shows uptime ("up · 37m").
- `ready` (green, filled) — a `.path`/`.timer` watcher, armed and waiting for its trigger.
- `ran ✓` (green, hollow) — a fire-and-forget task/launcher that completed cleanly (e.g. the
  wallpaper launcher) — done, not "down".
- `starting`/`stopping` (amber, breathing) · `failed` (warm red) · `down` (grey, filled) ·
  `unknown` (dashed ring — couldn't determine) · `absent` (hollow) — not installed.

Only genuinely-actionable states roll up into the header's **"N need attention"** pill: a
`failed` unit, a daemon that's `down`, or something `up`/`starting` whose port won't answer
(a split-brain). Those rows also get a coloured edge and a one-click **Copy fix** button
that copies the exact `systemctl reset-failed … && restart …` (the server never mutates
anything — you run the line).

**Honest when blind:** the payload carries a server `generated_at`; if it goes stale, or the
status server itself is unreachable, the pill drops to a grey "degraded" state and the list
dims — the panel never looks confidently green while it can't actually see.

Status is never conveyed by colour alone — every row carries a text label and an
`aria-label`, rows are a semantic list, and it honors `prefers-reduced-motion`.

## Tests
The kind-aware status logic is pinned by a stdlib `unittest` suite (`run`/`reach` are
injected, so nothing shells out). Covers daemon up/down, task `ran ✓` vs nonzero-exit
failure, watcher `ready`, the escaped-name list-units fallback, `absent`, the attention
rollup, the bad-row-doesn't-blank-the-panel guarantee, and the `/status.json` data contract:
```
python3 -m unittest discover -s integrations/status-panel/tests
```

## Logs
`journalctl --user -u agentos-status-panel.service -f`
