# AgentOS surface-labor contract

AgentOS speaks through several surfaces. They share a visual grammar (see
[`instrument-tokens.md`](instrument-tokens.md)) but they must NOT share *jobs* — if two
surfaces narrate the same truth slightly differently, the user learns to trust neither and
falls back to staring at one. This is the division-of-labor contract: who says what, when.

The governing principle is the project's: **the all-clear is silence; interruptions are
earned.** A surface that is quiet is not broken — it's the floor being solid.

| Surface | Owns | Says | Never |
|---|---|---|---|
| **Reactive wallpaper** | fleet *mood* | working · needs-you · snag · idle (and a *quieter-than-idle* stale posture) | boot-health, per-service failure, anything you must act on |
| **Keyhole tray** (ADR-0012) | the glanceable *fact* + always-on fallback | fleet state, VRAM lease, and the boot board *on demand* (when you open it) | interrupting; standing alarms |
| **Status panel** (ADR-0017) | the *full* boot board — diagnose & recover | every service's live state, "why", the copyable fix | mutating the system; being the all-clear (the all-clear is its own absence) |
| **swaync notifications** | the *interrupt* | "needs you", and a service that **fell over after boot** (a regression) | boot-time amber (expected churn); re-firing for a still-failed unit |

## Rules that fall out of this

1. **Boot-time churn is not a failure.** Units pass through `starting`/`down` before going
   green; that's the panel's business, never an interruption. Only a *new failure edge after
   boot settled* earns a swaync toast.
2. **The all-clear is silence.** A clean boot should make the panel *recede* (open only when
   `summary.attention > 0`) and leave the tray to carry the calm. No surface announces "all
   fine" — fine is the absence of noise.
3. **One reachability truth.** The keyhole owns the steady-state "is it alive *right now*"
   question; the panel owns "did boot succeed". They read the same `/status.json` contract so
   they can't disagree. The panel does not grow a live fleet view — that's the tray's job.
4. **Honest when blind, everywhere.** An unreachable source reads `unknown`/stale, never a
   calm fake `idle` — and stale must look *quieter than idle*, not identical to it. (The
   wallpaper feed degrading a dead Hermes to plain `idle` is the known holdout — see ADR
   backlog / `feed.rs`.)
5. **Show, don't dispose.** Surfaces propose actions (Copy fix, bring-stack-up); the human
   disposes. No surface mutates system state without an explicit, reversible, ADR'd path.

## Why this is a contract, not a style note
Without it, every new service (Lucid, ComfyUI, the next thing) tempts each surface to grow
toward the others — the panel toward a live dashboard, the tray toward alarms, the wallpaper
toward status. The contract is what keeps four calm surfaces from collapsing into four noisy
ones.
