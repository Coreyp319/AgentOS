# ADR-0042: Continuous UI improvement via Union CSS — one loop in two stages (manual rails now, auto-proposer gated on Union maturing)

- Status: **Proposed — Stage 1 (manual rails) BUILT + verified live; Stage 2 (auto-proposer)
  DEFERRED behind a stability gate. AMENDED 2026-06-22 (Aurora): Stage 1 productionized — the
  style was authored into the WhiteSur look + rebranded `agentos`→`aurora`, given a cohesive
  Aurora Light/Dark colour-scheme pair, shipped as `integrations/aurora-theme/`, and the Charter
  was taught to read CSS; three-panel reviewed (cohesion / a11y / vision-fit) with must-fixes
  applied. See the Amendment section below.** The MVP manual loop is stood up on the box: the
  Charter drift misfire is fixed, the editable `aurora` Union style is the active target with
  authored identity tokens, and `css-tx.py` gives CSS edits a validated, reversible checkpoint/
  revert. The model-driven CSS *proposer* is intentionally not built. This ADR records the decided
  architecture so it does not drift.
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

## Amendment — 2026-06-22: Aurora (productionized, light + dark, reviewed)

The manual rails proved out, so the deferred productionization + Charter-CSS items were pulled
forward and shipped, and the look was given a real light/dark identity. What changed:

- **Rebrand `agentos` → `aurora`** (user steer: "propose rebrands"). The widget style + colour
  schemes are now "Aurora", deliberately unifying the widget identity with the `nimbus-aurora`
  reactive-wallpaper feed (one ambient name). `css-tx.py` default + the env knob updated.
- **The WhiteSur look, authored.** Measured the user's NimbusRefinedDark/WhiteSur pack and
  re-expressed it as a two-radius scale (`--corner-radius: 6px` controls / `--popup-corner-radius:
  12px` floating surfaces), a single-token elevation ramp (`--elevation-{1,2,3}`; Union allows only
  ONE shadow layer — no comma stacks, no `inset`, verified against the engine), and a solid lavender
  focus ring. Token-first, gated by `css-tx.py`'s `union-ruleinspector` load check.
- **Light + dark as ONE identity.** Union exposes no "is-dark" selector to CSS, so light/dark lives
  in the colour scheme: **Aurora Dark** (the rebranded CoreyLavender violet elevation ladder) + a new
  cohesive **Aurora Light** twin (same violet accent, deep-violet focus, the ladder mirrored into a
  faint-lavender near-white ramp). One Union style serves both; `aurora-mode.sh light|dark|toggle`
  swaps the scheme.
- **A deterministic colour gate.** `tools/check-contrast.py` verifies every body/selection/link/
  semantic pairing + the focus ring (non-text 3:1) at WCAG AA, and is **wired into `apply.sh` as a
  hard gate** (refuse-to-install on FAIL), matching the ruleinspector load gate — "code disposes"
  applied to colour, not eyeballed. Both schemes pass AA (most pairings AAA).
- **Productionized** as `integrations/aurora-theme/` (apply/restore/aurora-mode + README, registered
  in `components.conf`, desktop tier, **default off** — Union is still a preview). Reversible:
  `apply.sh` backs up the prior `widgetStyle` + `ColorScheme` once (never recording Union/Aurora as
  the restore target); `restore.sh` restores them + drops the env knob.
- **Charter is now CSS-aware** (the deferred prerequisite): `ui-audit-style.py` snapshots
  `UNION_STYLE_NAME` + the `:root` tokens, and the "dimensional depth" principle reads the CSS
  (two-radius + shadow), not just compositor blur. Re-baselined to Charter **v3** (Aurora identity;
  drift → 0, so the deliberate switch no longer trips the ADR-0030 wallpaper-desat floor).
- **Reviewed** by three panels — cohesion (visual-systems) 7/10, a11y 5/10, vision-fit 8/10 (no
  capping violation). Must-fixes applied: the **focus ring is now SOLID** (the translucent 0.45 ring
  failed WCAG 2.4.13 / 1.4.11 — it composited <3:1; decoupled from the soft hover-fill, which keeps
  the alpha); the depth language was unified into the elevation ramp; the selection alternate band
  was corrected (it had been *lighter* than the normal band — backwards for legibility); one Aurora
  Dark semantic foreground was nudged to clear AA.

### Residual review follow-ups (none wedge the desktop; verify on screen at relogin)
- **CVD redundancy (a11y).** Links vs body/visited, and positive/neutral/negative, are separated
  largely by hue — the robust fix is a non-colour channel (underline / icon shape) at the
  *app/Kirigami* layer, not pure theme; the schemes widened luminance where they could.
- **Interaction-state polish (a11y).** Dark button hover is border-only (~2:1); checked vs pressed
  fills are close; the switch off-handle is low-contrast. Theme-fixable but unverifiable without a
  live render (GUI launches are unavailable in the authoring harness) — deferred to a relogin pass.
- **Elevation-ladder mirror (cohesion).** Dark keeps the WhiteSur-correct recessed "well" for `View`
  while light makes `View` the brightest paper, so the ladder is not a perfect mirror; floating
  surfaces still lift via the elevation ramp + hairline in both modes. Left as an intentional,
  conventions-based call — raising dark `View` above `Window` is a one-token change if preferred.
- **Dark hairline (S2).** At `contrast=4` the scheme-derived borders are soft; a popup over its own
  window leans on the (now unified) shadow. A `contrast` bump would sharpen separation but trades
  against the calm intent — left to the user.
- **Stage 2** (the constrained-diff CSS auto-proposer) remains DEFERRED behind the Union
  dialect-stability gate.

### Design-council pass — 2026-06-22 (cap found, then lifted)

A full 22-agent design council (brief: `docs/design/0042-aurora-desktop-look-council-brief.md`) rated
the *post-fix* state **5.0/10 — a11y-capped** (uncapped 6.6) and caught that the earlier "focus ring is
now SOLID" claim was **incomplete**: `text.css:43` and `tables.css:36` still bound the *translucent*
`--focus-color` fill as the keyboard focus outline (2.71 / 2.66:1), so the comment asserting "the alpha
never touches the ring" was false in source — a real honesty defect. Fixed this pass:
- **Rebound both outlines to the solid `--focus-outline`** (now every focus ring — button / text field /
  table row / slider / check — routes through a solid token ≥3:1), corrected the comment, and added an
  **`apply.sh` focus-binding lint** (`grep` for `outline:…--focus-color` → refuse to install, `exit 5`)
  so the green-gate-over-bad-binding trap cannot recur.
- **Extended `check-contrast.py`** with engaged-state (hover) rows — both schemes clear 3:1 on hover too.
- **Dark hover via the shipped-but-unused `DecorationHover`** (≈6:1 on the button fill vs the ~2:1
  selection-violet border it replaced), for both the standard and flat/toolbar buttons.
- **`.changed` off the amber hazard ramp → informational lavender** (a benign reversible edit must not
  read as a warning on a reversibility-first desktop; CVD glyph/edge-bar redundancy is a deferred
  app-layer follow-up).
- **Delight + honesty:** `restore.sh` prints a restoration receipt ("…your desktop is exactly as it was");
  `apply.sh`'s revert copy no longer overclaims.

**Resolved by the council:**
- **Elevation-ladder mirror → RESOLVED-KEEP (locked invariant).** Five design agents independently held
  that dark-mode elevation conventionally *lightens with lift* while light leans on shadow (M3, Adwaita,
  Primer, HIG); a true numeric mirror would make Aurora *less* recognizably WhiteSur. The asymmetry
  (`dark View` = recessed well; `light View` = brightest paper) is kept and **named an invariant** so
  personalization respects it.
- **Division of labour (do-not-converge).** Widget Aurora is the *resting body* — it never signals; the
  `nimbus-aurora` wallpaper is the only surface that signals. The widget amber/neutral token stays off the
  wallpaper's scarce warm `needs-you` channel. The two share a name, not a meaning.

Post-fix the cap lifts (Experience 5→~8); verdict collapses to **ITERATE toward 9**. Still open: the
engaged-state polish that needs eyes (checked-vs-pressed in motion, switch off-handle, 6px-control HiDPI
target size — a **relogin checklist**, not "verified"); an optional reduced-motion lever (`aurora-motion.sh`
+ a `restore.sh` backup of `AnimationDurationFactor`); and two taste forks the council escalated to the
human — a **shader-derived accent** (keep violet now / regenerate from the idle shader hue later) and a
**light↔dark crossfade** (hard cut + reduced-motion lever now / ~300ms KWin fade later). Both are deliberate
follow-ups, not bar-blockers. Do not promote the component from default-off until the relogin checklist clears.

**Update (same session) — shader-derived accent BUILT (the ambitious fork chosen).** Corey chose the
shader-derived accent. `tools/derive-accent.py` samples the Indigo Channel's *resting* fog hue
(`spikes/ue-probe/indigo_channel_setup.py:87`, `FOG_INSCATTER` → RGB 39,41,69, HSL 236°) and rotates the
charter violet a bounded **k=0.35** toward it, **hard-clamped to a violet arc [255–285°]** with a WCAG
luminance floor — so the accent reads *co-lit by the environment* yet can never leave violet or drop below
AA (a green/amber wallpaper clamps to the arc edge). The accent moves a calm, deliberate 6 points →
**`118,92,196`** in both schemes (selected text 4.92:1; focus/hover rings ≥3:1; `check-contrast.py` exit 0),
its decoration/hover/WM family rippled to match. This resolves **art-director's recorded dissent (§6 of the
brief)** toward "safe-by-design," not safe-by-accident. The accent **pins to the Indigo Channel** as the
canonical home wallpaper; re-deriving on every wallpaper switch is deferred (a one-violet-identity call for
art-director). The light↔dark crossfade fork remains deferred.

## Amendment — 2026-06-23: OS-wide cohesion pass (one violet accent across every toolkit)

The 2026-06-22 work built + reviewed Aurora **in isolation** (the Qt/Union identity). A top-to-bottom
cross-layer review then asked the harder question: does Aurora cohere inside the *rest* of the live
desktop (GTK, the Plasma shell, the AgentOS surfaces)? Five independent reviewers (visual-systems,
ambient-embodiment, a11y, brand, plus a surface audit) converged: **OS cohesion ≈ 5/10 — one identity
in shape/depth/type, fractured on accent.** The desktop rendered **violet windows over a blue shell**,
with *three* unrelated blues live at once.

### The finding (quantified, sourced)
| Layer | Was | Hue / chroma | Status |
|---|---|---|---|
| Qt windows (Aurora) | violet `#765CC4` | 255° / 47% | ✓ identity |
| GTK3 apps | violet `#7c5cc4` (`gtk-3.0/colors.css`) | 256° / 46% | ✓ already done |
| Titlebars (aurorae) | lavender WM blend | 255° | ✓ follows scheme |
| **GTK4 / libadwaita** | **blue `#0860F2`** (`gtk-4.0/gtk.css`) | 217° / **94%** | ✗ unchosen WhiteSur leftover |
| **Plasma panel / tray / plasmoids** | **blue `#315BEF`** (WhiteSur-dark `colors`) | 227° / **86%** | ✗ unchosen WhiteSur leftover (always-on surface) |
| **AgentOS instrument surfaces** | **periwinkle `#7aa2ff`** (`instrument-tokens.md`) | 223° | ⚠ *deliberate* register, blue by choice |

The chroma point compounded the hue point: the violet is 47% sat *by design* ("low chroma except the
accent"); the leftover blues are 86–94% — so the off-brand colour was also the **loudest**, parked on
the panel and every GTK4 control. a11y confirmed the re-accent is **contrast-safe**: white-on-`#765CC4`
= 4.92:1 AA (already gated), the swap introduces **zero** new failures, drops saturation ~44 pts (a
measurable calm/eye-strain win), and is CVD-positive (blue 227° / violet 255° already collapse to one
hue under red-green CVD, so the "two accents" distinction was illusory for ~8% of users anyway).

### Decision — re-accent the WhiteSur layers UP to violet; do NOT revert Qt to blue
Violet is the **intentional, shader-derived, lower-chroma, already-majority** identity (Qt + GTK3 +
the Union style + the wallpaper provenance via `derive-accent.py`); the blues are unchosen WhiteSur
defaults that violate the calm-chroma rule *even if unified*. And — user decision (2026-06-23) — the
AgentOS **instrument** register, though a deliberate "deep-navy glass cockpit," **unifies its accent to
violet too** (its aurora ramp already crested violet), so the OS is **one accent**. Shape/depth/type
already cohere OS-wide, so this is a re-accent, not a rebuild.

### What shipped (all reversible, opt-in/off-by-default)
1. **`aurora-theme` extended** to own the GTK app layer: GTK4 `gtk.css` accent `#0860F2`→`#765CC4`
   (backed up), `gsettings accent-color → purple` for pure-libadwaita apps, and the GTK icon-theme
   unified to `Nimbus-dark-refined` (was WhiteSur). `restore.sh` reverses each.
2. **New `aurora-panel` component** — the Plasma shell. Clones the active desktop theme to
   `<name>-aurora`, sets the clone's `colors` = `AuroraDark.colors` (the violet lives in **one** place),
   repoints `plasmarc`. Original theme untouched; revert = repoint back. Isolated from `aurora-theme`
   so the unrenderable shell experiment can't endanger the proven Qt work.
3. **New `swaync-aurora` component** — the notification "nervous system" wore a full macOS mimic
   (Apple blue/green, SF Pro) and was managed by *nothing*. Ships a deep-navy-glass + violet-accent
   swaync style (Inter; red only for `.critical`; the reserved warm never spent), installed reversibly.
4. **Instrument register unified blue→violet** — `--inst-blue #7aa2ff → #9b82e0` (hue 256°, AA on the
   glass at 5.0–5.8:1, calmer at 42% sat) across `instrument-tokens.md` (canonical), the status panel,
   models panel, Lucid (`theme.css` + SSR `lucid_web.py`, incl. hardcoded `rgba(122,162,255)`/`#a9c4ff`
   stragglers), the share hub (`--cool #8aa9ff → #9b82e0`), and the keyhole plasmoid (`#7AA2FF/#2C57C9
   → #9B82E0/#5A45A6`). The token NAME is kept for back-compat (value is now violet). The Lucid
   `--dir-*` categorical narrative-direction hues are **deliberately left** (a coded set, not the accent).
5. Minor: launch.html body sans → Inter (matched its sibling panel + the desktop).

### Reversibility + verification
Every change is backed up and reversed by a `restore.sh`; the three components are **off by default**
(Union is still a 6.7 preview). The authoring harness can't render, so on-screen confirmation is a
**relogin checklist** (panel/tray highlight violet not blue; a GTK4 app's selection violet; Qt + GTK
selections match; a swaync toast in navy glass; keyhole in a violet panel). `bash -n` passes on all six
scripts; `check-contrast.py` still clears AA; the instrument surfaces grep clean of accent-blue.

### Residuals (deferred, non-wedging)
- A deeper GTK4 recolour (accent *hover/active* shades, link blues) beyond the primary accent define.
- Display-serif harmonization across web surfaces (launch.html Palatino vs Lucid Fraunces) — left
  per-surface; launch.html deliberately avoids web fonts, so forcing Fraunces was rejected.
- The GTK `gtk-theme-name=WhiteSur-Light`-with-`prefer-dark` pointer (resolves dark today) — left as-is
  (changing it risks GTK theme resolution for unclear benefit).
- aurora-panel's WhiteSur-SVG path: a blue hardcoded *outside* the `ColorScheme-*` classes (if any)
  would survive the `colors` swap — the one thing to eyeball at relogin.

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
- ~~Teach the Style Charter to read CSS tokens (`UNION_STYLE_NAME` + `:root` variables)~~ **DONE
  2026-06-22 (see Amendment)** — Charter v3 snapshots the Union tokens; CSS-depth rubric wired. A
  richer CSS *drift threshold/rubric* tuned for Stage 2 remains future work.
- ~~Productionize `agentos` + the env knob as an `integrations/` component~~ **DONE 2026-06-22 as
  `integrations/aurora-theme/` (see Amendment).** Routing `css-tx.py` through the ADR-0005 agentosd
  Rust tx is still optional/deferred.
- `union-ruleinspector` is a load/parse gate, not a semantic one (it can't tell a real rule match from an
  inherited default, and is post-write). A richer pre-apply validator is a future nicety, not a blocker.
- Whether to keep Union global or run it as a per-app authoring sandbox (revert `widgetStyle → kvantum`,
  develop CSS via per-app launches) remains the user's standing call; both are supported and reversible.
