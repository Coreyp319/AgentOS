# C1-spike findings — kwin-mcp on this Plasma 6 box

**Verdict: VIABLE. The long pole is tractable — not a multi-month unknown.** `kwin-mcp`
installs and runs on this exact box in minutes, and capture + EIS input + AT-SPI all work in
an isolated virtual session with zero effect on the live desktop. Computer-use (C2) can likely
be pulled **earlier** than the roadmap's "long pole" framing assumed; the must-solve is a
coordinate transform, not a capability gap.

## Setup (reproducible, ~5 min)
- Clone `github.com/isac322/kwin-mcp` (Python 3.12+; runs on system Python 3.14).
- The only painful deps (`PyGObject`, `dbus-python`, `Pillow`) are **already system-installed** —
  so: `uv venv --system-site-packages --python /usr/bin/python3 .venv` then
  `uv pip install mcp` + `uv pip install --no-deps -e .`. (Avoids building PyGObject/dbus.)
- Drive it directly via `kwin_mcp.core.AutomationEngine` — no MCP client needed. Drivers:
  `spikes/kwin-mcp/spike_drive{,2,3}.py` (the clone is gitignored; drivers reproduce the runs).

## What works ✓
- **Virtual sandbox**: `session_start` spawns `dbus-run-session kwin_wayland --virtual`, returns a
  wayland socket, launches an app, and brings up the **KWin EIS input backend**. Isolated; the
  live desktop is untouched.
- **AT-SPI tree**: `accessibility_tree` returned 233 elements for kcalc — full hierarchy
  (`[application] kcalc → [frame] KCalc 640x480 → panels → [text] display @ (12,50,616x28)`),
  with roles, states, bboxes, and `actions` (`Press`/`SetFocus`). Named elements resolve:
  kwrite's `[button] "New File" @ (51,173,119x28) [Press, SetFocus]` found by name.
- **Input (EIS)**: `mouse_click` and `keyboard_type` execute (no ydotool/inputsynth needed).
- **Screenshot**: full-fidelity PNG of the virtual session (kwrite rendered with the WhiteSur/
  Nimbus theme — traffic-lights, frosted dark). Capture is flawless.

## The two real costs (both anticipated by the design) ⚠️
1. **AT-SPI quality varies by app.** kcalc's number buttons expose as **unnamed `[check box]`
   with `(0,0,0x0)` geometry** — unusable for semantic targeting. → semantic-first works on
   well-instrumented apps; **vision/SOM fallback is mandatory** for the rest (matches ADR design).
2. **Coordinate-space mismatch (the key C2 task).** `find`/`accessibility_tree` report
   **window-local** coordinates, but `mouse_click` expects **screen-global**. Proof: kwrite's
   "New File" was found at window-local `(51,173)`; its real screen position is `~(430,347)`;
   clicking the reported bbox center `(110,187)` **missed** (the editor never opened). → C2 must
   transform window-local → screen by **joining the element bbox with the window's screen
   geometry from KWin** (this is the same wrinkle flagged for the attention overlay, ADR/vision).

## Minor
- Harmless `WARN | failed to send message: Broken pipe` on `session_stop` (teardown race).

## Implication for sequencing
- Computer-use is **de-risked and pull-able earlier**. C2 ≈ wrap `AutomationEngine` as the Linux
  `ComputerUseBackend` + **add the window-geometry coordinate transform** + wire vision/SOM
  fallback. The coordinate transform is the one must-solve; everything else is plumbing.
- The same coordinate-join is needed by the attention overlay (C3) — solve it once, reuse.
