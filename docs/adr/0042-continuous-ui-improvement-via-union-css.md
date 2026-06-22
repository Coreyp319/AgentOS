# ADR-0042: Continuous UI improvement via Union CSS — one loop in two stages (manual rails now, auto-proposer gated on Union maturing)

- Status: **Proposed — Stage 1 (manual rails) BUILT + verified live; Stage 2 (auto-proposer)
  DEFERRED behind a stability gate.** The MVP manual loop is stood up on the box: the Charter
  drift misfire is fixed, the editable `agentos` Union style is the active target with authored
  identity tokens, and `css-tx.py` gives CSS edits a validated, reversible checkpoint/revert. The
  model-driven CSS *proposer* is intentionally not built. This ADR records the decided architecture
  so it does not drift.
- Date: 2026-06-22
- Deciders: Corey (product steer this session: validate the 6.7 upgrade → activate Union globally →
  *"lets make the output of our next steps a skill we can use when modifying UI for the OS"* →
  *"review how well we are setup to continually improve via CSS and the UI hermes agent"* → on the
  review, *"fix and then what do you recommend?"* → chose to build the rails).
- Review input: a 15-agent readiness workflow (`wf_e8dcf522-a76`, 2026-06-22) — 7 dimensions assessed,
  each adversarially verified against the code, synthesized into a scorecard + gap plan. Verified
  readiness of "continually improve the UI via Union CSS + the Hermes UI agent" **before** this ADR:
  **3/10 — two disconnected islands.**
- Relates to: ADR-0001 (don't reinvent — reuse the Hermes audit spine + the agentosd lease, do not
  build a new theming engine or a second proposer), ADR-0005 (apply/rollback transaction — `css-tx.py`
  is the file-level realization of that pattern for CSS; the agentosd Rust tx remains the future
  substrate home), ADR-0034 (deterministic UI audit + Style Charter + attended critique — the loop
  this extends), ADR-0030 (drift → reactive wallpaper Desat — the signal the Union switch misfired),
  ADR-0003 (fail-open — the gate refuses, it never wedges the desktop).

## Context

Plasma 6.7 (installed on the box 2026-06-22) ships **Union**, a CSS-based Qt style engine
(`union-input-css.so` → `libUnion.so` → QtWidgets/QtQuick output), as a tech preview. It makes the
desktop's widget styling a **deterministic, diff-able, version-controllable CSS source** — a natural
fit for the AgentOS vision of "the desktop improves itself," where the model proposes a change and
code disposes.

AgentOS already has a *proven* continuous-improvement loop: the Hermes **Daily UI audit** (ADR-0034).
It is deliberately deterministic and model-free over a narrow `kdeglobals`/`kwinrc` allowlist
(contrast, font weights, a few KWin effects), with a Style Charter (a versioned aesthetic identity),
an **attended** model critique pass that runs under the agentosd VRAM lease and emits *prose* cards,
backup/verify/revert, earned-autonomy staging, before/after screenshot auditability, and a
cross-login persistence honesty net. It carries a hard-won lesson — *"the contrast lie"*: writing the
`kdeglobals` shadow, reading it back same-process as "success," while the active color scheme
re-stamped it at login → a silently falsified audit trail. The loop exists to make that impossible.

The readiness review found the obvious thing: **Union CSS and that loop are two disconnected islands.**
The audit toolchain references `widgetStyle` only to *deny* it ("load-bearing, never auto-edit"); the
word "union" and `ruleinspector` appear nowhere in its authoring path. Nothing snapshots, proposes,
applies, validates, or reverts a CSS edit. The `agentos` style was a byte-identical copy of breeze and
wasn't even the active target. Activating Union also tripped a real bug: the Charter read the
*deliberate* `widgetStyle` switch as identity drift and raised a wallpaper desaturate floor (ADR-0030).

But two facts make Union the *right* next target rather than a detour:
1. **Durability.** A Union style is a plain file under `~/.local/share/union/css/styles/<name>/`. It is
   not re-stamped at login by a scheme or LookAndFeel. So it structurally escapes the ADR-0034 "lie"
   class — what you write is what persists.
2. **A real deterministic gate.** `union-ruleinspector` resolves the full property tree for a
   widget+state and *throws on a style that doesn't load*. That is exactly the "code disposes"
   validator the kdeglobals path never had (its shadow-writes were opaque).

The countervailing fact: Union is an explicit **6.7 tech preview** (`themerc`: "Union (In Development)",
renders ≈ Breeze), and its CSS dialect/selectors/tokens may change between releases.

## Decision

**Make Union CSS a first-class target of the existing continuous-improvement loop — reusing the
ADR-0034 spine and the agentosd lease, not building a new mechanism — and ship it in two stages,
gating the autonomous part on Union maturing.**

### Principles (the non-negotiables this must honor)
- **Don't reinvent (ADR-0001).** Reuse the audit spine (Charter, backup/verify/revert, ledger,
  before/after) and the lease-hosted attended-model harness (`critique.py`). No second proposer, no new
  theming engine.
- **Model proposes, code disposes.** The deterministic disposer for CSS is `union-ruleinspector`: a
  proposed state that does not load is rejected, mechanically, before it is ever trusted or rendered.
- **Reversible by default (ADR-0005).** Every CSS change is a checkpoint with a byte-faithful snapshot
  and a one-command revert that re-validates. Activation itself is reversible (`widgetStyle → kvantum`,
  the kept v1 Charter, removing the env knob).
- **Honest.** No shadow-write, no login-revert lie; the ledger records validation truthfully; the
  Charter's drift signal must not misfire on a deliberate change.
- **Fail-open (ADR-0003).** The gate refuses a bad checkpoint; it never wedges the desktop. The widget
  *activation* knob (`widgetStyle`) stays out of any automated path — only the named-style CSS content
  is ever auto-touched, never the global switch ADR-0034 bars.

### Stage 1 — manual rails (BUILT, this session)
1. **Fix the drift misfire.** Re-baseline the Style Charter (`ui-audit-style.py keep`) so
   `widgetStyle=Union` is the expected identity (Charter v2; v1 preserved/revertable). Drift → 0,
   wallpaper desat floor → 0. The deliberate switch is no longer read as unwanted drift.
2. **Make the editable style the active, authored target.**
   `~/.config/environment.d/union-style.conf` → `UNION_STYLE_NAME=agentos` (Flatpak-safe; *not* the
   `QT_QUICK_CONTROLS_STYLE` env var, which leaks into Flatpak sandboxes), and author real AgentOS
   identity tokens in `agentos/variables.css` (rounder `--corner-radius`, a stronger lavender
   `--focus-outline-alpha`). Colors already bind to the live KColorScheme, so the palette/accent follow
   `CoreyLavender` automatically — identity here is *shape and restraint*, per the Charter.
3. **`css-tx.py` — the reversible, gated apply/checkpoint/revert** (`.claude/skills/union-css-theming/`,
   committed `456673b`). `verify`/`status`/`diff`/`checkpoint`/`revert`/`history` over the style working
   tree; append-only ledger + per-checkpoint snapshots (style dir + the required `defaults/`) in
   `~/.local/share/union/css/.tx/`. `checkpoint` runs the ruleinspector gate first and **refuses** an
   unloadable state, so `revert` always lands on a validated checkpoint.

This closes a **manual** loop: *edit CSS → status/diff → verify (gate) → checkpoint or REFUSED →
revert*. Human-authored, tooling-disposed, honest, reversible. It is real value on the live desktop and
costs nothing if Union's dialect shifts.

### Stage 2 — the auto-proposer (DEFERRED behind a stability gate)
The named-but-unbuilt ADR-0034 §Decision-6 "attended explore pass under the lease" *is* a CSS-authoring
agent. When built, it reuses `critique.py`'s proven lease-hosted harness but changes its output contract
from prose to a **constrained, token-level CSS diff** over a tight allowlist (additive token edits —
radii, spacings, alphas, accent derivations — never structural rewrites), gated behind the Stage-1
`verify` + `revert` rails, and steered by a CSS-aware Style Charter (Stage 2 also teaches the Charter to
read `UNION_STYLE_NAME` + the `:root` tokens, which it is currently blind to).

**The gate to start Stage 2:** Union's CSS dialect must prove stable across at least one or two Plasma
releases (it is an in-development preview today). Building a model that auto-edits CSS against a moving
dialect is the one risk that violates "reversible/honest by default" in spirit — a silent dialect change
could break widget rendering desktop-wide. Until then, the manual rails are the product.

### Productionization (when proven)
The `agentos` style + the env knob currently live in `~/.local`/`~/.config` (not repo-tracked). Promote
them to an `integrations/` component with `apply.sh`/`restore.sh` (the existing registry pattern) once
the loop has earned it. The `css-tx.py` file tx may later route through the ADR-0005 agentosd Rust tx to
give CSS a substrate-level (not just Python) transaction — also deferred until the loop proves value.

## Consequences
- Readiness moves from "two islands" to a **closed manual loop** that honors every non-negotiable. The
  hard parts (the propose→dispose→revert→verify spine, the lease harness, the Charter, before/after)
  were already built; this connects them to CSS rather than reinventing them.
- The desktop is briefly a *downgrade* (Union ≈ Breeze until `agentos` CSS re-expresses the look) — an
  accepted, reversible cost; Stage 1 step 2 is the fast path back to identity.
- We deliberately ship *less*: no autonomous CSS editing yet. That is the correct call for a preview
  engine; the manual loop captures the value with none of the moving-target risk.
- A second reversibility surface (CSS file tx) exists alongside the kdeglobals one until ADR-0005 unifies
  them.

## Open / deferred
- Stage 2 (the constrained-diff CSS proposer) — gated on Union dialect stability across a Plasma release.
- Teach the Style Charter to read CSS tokens (`UNION_STYLE_NAME` + `:root` variables) and add a CSS
  drift/rubric — prerequisite for Stage 2.
- Productionize `agentos` + the env knob as an `integrations/` component; optionally route `css-tx.py`
  through the ADR-0005 agentosd Rust tx.
- `union-ruleinspector` is a load/parse gate, not a semantic one (it can't tell a real rule match from an
  inherited default, and is post-write). A richer pre-apply validator is a future nicety, not a blocker.
- Whether to keep Union global or run it as a per-app authoring sandbox (revert `widgetStyle → kvantum`,
  develop CSS via per-app launches) remains the user's standing call; both are supported and reversible.
