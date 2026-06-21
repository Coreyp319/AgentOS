# spikes/atrium — the launch surface, proven before it ships

A measurement spike for **ADR-0031** (the launch surface, "the Atrium"). The design council
decided the launch pad is *not a new app* but a **thin fourth verb** in the surface-labor
contract: desktop = Plasma/KRunner, phone = a read-only PWA launch view served by the existing
status panel. The council rated it **ITERATE · 5.0/10** and gated the ADR on this spike landing
real numbers for two unproven seams and one live a11y cap. This proves them.

> Throwaway by charter (`spikes/` is excluded from the build). Nothing here is installed. The
> point is to measure, decide, then fold the proven bits into `integrations/status-panel/`.

## What it proves (and the numbers)

Run `python3 measure.py` → writes **[`MEASUREMENTS.md`](MEASUREMENTS.md)**. Latest run:

| Gate | Result |
|---|---|
| **#1 degraded-state contrast** (the live a11y cap) | The original `opacity:.5;saturate(.6)` treatment drops body text to **4.3:1 — FAILS WCAG AA**. The cap was real. The fix here (dim the *signal* — dots/glass — keep the *text*) restores **12.69:1 ✅**. |
| **#5 PWA installability** (static) | manifest + SW + 192/512/maskable icons all present and valid → **static check PASS**. (Live-origin install over `tailscale serve` still owed — needs the box.) |
| **Q1 standing cost** | The launch view is a **still room**: no WebGL, no canvas, no rAF → **≈0 GPU**. It's a route on the existing panel daemon → **0 new standing processes**. Host RSS ≈27 MiB. |
| **first paint** | A single **~20 KB** self-contained document, **1 request**, **0 blocking external resources**, no web fonts → paint is parse-bound. |

## The two seams the council called unproven

1. **Server-emitted loopback signal (gap #4).** The "Copy fix" shell one-liner is decided on the
   server, never the client. `classify_origin()` reads the connecting peer + the `X-Forwarded-*` /
   `Tailscale-User-*` headers that `tailscale serve` injects. Fail-closed: a command is emitted
   **only** to a provably-local request. A spoofed `X-Forwarded-For` from localhost only makes the
   caller *more* restricted — the dangerous direction (a phone receiving a shell command) is
   structurally blocked because the proxy always adds the header. Locked down by tests.

2. **Origin-aware doors (gaps #2/#3).** `door_for()` classifies every service into `open` /
   `desktop-only` / `monitor-only` — **never a dead door**. On a tailnet origin a loopback URL is
   rewritten to the tailnet host; an un-served service (ComfyUI `:8188`, `tailnet:false`) renders
   *"on your desktop"* instead of a link that 404s on the phone.

## The moat affordance (S1), in its honest gated state

On-demand doors (ComfyUI) carry the **lease-aware cost line** — the council's signature move and
the one lever that lifts market-fit off the floor. It is *designed here, not faked*: until
`agentosd lease` (`org.agentos.Coordinator1`) is installed it states the honest truth
("Opening reads the GPU coordinator first — the cost lights up here once the lease daemon is
installed"). The layout/a11y is measured now so wiring the real admission verdict later is a
data swap, not a redesign.

## Files

| File | Role |
|---|---|
| `atrium.html` | the launch view — still room, instrument-glass tokens, origin-aware doors, fixed stale treatment, S1 affordance |
| `atrium_server.py` | reuses the production catalog + `build_status`; adds `/launch.json` (origin-aware + loopback signal), `/manifest.webmanifest`, `/sw.js`, `/icons/*` |
| `manifest.webmanifest`, `sw.js` | the PWA shell (installable; honest offline state — shell only, never stale-as-live state) |
| `make_icons.py`, `icons/` | PWA icons rendered from the existing instrument mark (no new art) |
| `contrast_probe.html`, `measure.py` | the measurement harness (chromium + Pillow); writes `MEASUREMENTS.md` + `out/*.png` |
| `tests/test_atrium.py` | origin-logic tests — the security-critical invariants (20 tests) |

## Run it

```bash
# the measurements (chromium + Pillow):
python3 measure.py

# the server, against the REAL catalog:
python3 atrium_server.py            # → http://127.0.0.1:8780

# the tests:
python3 -m unittest discover -s tests

# eyeball it offline (no server, mock data):
xdg-open 'atrium.html?demo=1'              # local desktop view
xdg-open 'atrium.html?demo=1&remote=1'     # what the phone sees over the tailnet
xdg-open 'atrium.html?demo=1&forcestale=1' # the (fixed) blind/stale state
```

## Hardening applied (from the adversarial review pass)

A 12-agent review (security / a11y / reversibility / resource-safety / correctness) ran over this
spike; 0 false positives, all confirmed findings fixed:
- **a11y:** focus is preserved across the poll (no rebuild unless state changed; focus restored to
  the same door id) · open-door health now has a visible state word, not colour-only (WCAG 1.4.1) ·
  fuller aria-labels (name + state + desc + S1 note) · copy-fix now works for split-brain open
  doors too · stale vs blind kept distinct · guarded aria-live.
- **security:** the shell-leak guard now needs **two** independent local signals (no forwarding
  header *and* a loopback Host) on top of a loopback peer; an empty `X-Forwarded-For` counts as a
  proxy; error responses no longer leak filesystem paths to remote clients; `X-Forwarded-Host` is
  format-validated before it becomes a door href; the server refuses a non-loopback bind.
- **resource-safety:** `/launch.json` is backed by a 1.5s TTL snapshot behind a lock (no fan-out
  hang); one malformed catalog row degrades to a skipped row, never a blank view.
- **measurement:** the contrast probe now also covers the secondary inks (`--muted`/`--faint`) —
  which surfaced a cross-surface `--inst-label` AA miss (3.86:1). **Fixed** by lifting it
  `#7a8090 → #878c9b` (now 4.55:1) across the canonical instrument register, `panel.html`, and both
  keyhole `InstrumentPalette.qml` — the same value the Lucid web app already vetted (ADR-0019/G7).

## Still gated / owed before any `integrations/` change

- **Live tailnet origin:** confirm PWA install + SW scope under the *actual* `tailscale serve`
  HTTPS cert on the box (static checks pass; the live origin is unverified here).
- **S1 real data:** the lease daemon is built-but-not-installed; wire the admission verdict once
  `agentosd lease` runs.
- **Catalog drift** (gap #3) was the one piece safe to land in production now (it's a current
  bug — a dead ComfyUI phone door); done in `integrations/status-panel/services.json` + guarded
  by a new test. Everything else stays in this spike pending the above.

See `docs/design/0031-launch-surface-FINAL-council-brief.md` for the full council verdict.
