# The Indigo Channel — reactive build-spec (ADR-0023/0029)

How the live UE 5.8 (Lumen) wallpaper **subtly reacts** to `agent.json`, `wind.json`,
and a (net-new) `music.json`, and how the SAME mapping persists through the UE↔shader
fallback crossfade. Design-technologist lens: runtime mechanism, exact transfer
functions, cost, and the safety inheritance.

Companion code: `indigo_reactive_setup.py` (authors the MPC + material taps, gated,
GPU-untouched). Reuses the already-researched channel in `remote_control_setup.md` and
the throttle vocabulary in `cvar_ladder.md`.

**Status:** DRAFT / spike. Nothing here ships on its own; Corey builds + measures on the
4090. Facts are tagged `[VERIFIED]` (read from the codebase or Epic source), `[DOC]`
(Epic docs), `[VERIFY-LIVE]` (needs a first-cook confirmation).

---

## 0. The one-paragraph answer

agentosd already produces the signals and already speaks loopback HTTP. The packaged
`-game` wallpaper exposes a **Material Parameter Collection** (`MPC_AgentOS_Reactive`) —
a set of global scalars any material reads — plus the **cvar console surface** it already
exposes for the throttle ladder. agentosd's *reactivity pusher* (a new tokio task in the
**lock-free wind/feed lane**, never the lease lane) low-passes the feeds and PUTs the
eased values to `KismetMaterialLibrary::SetScalarParameterValue` (for material levers:
glow, warmth, snag-dim, wind-yaw) and to a tiny set of cvars (for engine levers: fog
density, motion speed) over `:30010`. Idle = every reactive scalar 0 = the unmodified
Indigo Channel, byte-for-byte. The crossfade survives because **the eased signal lives in
agentosd, not in either renderer** — the shader floor reads the same `agent.json`/
`wind.json` the way it does today, so a UE→shader fall lands on the same look with no flash.

---

## 1. Runtime mechanism — three candidates, one pick

The constraint: the wallpaper runs the **packaged cooked `-game` build** (no editor, we
author no Blueprints), it must read live signals each tick and drive parameters, and the
reactive path must **never take an arbitration lock or delay a preempt SIGKILL** (the
load-bearing condition inherited from `wind.rs:18-37`).

### Candidate A — runtime Python tick inside UE  ❌ REJECTED (infeasible)
A `unreal`-API tick reading the json each frame. **Dead on arrival:** the official
`PythonScriptPlugin` is **editor-only at runtime** — Python execution is available only in
editor-utility Blueprint classes, not in any runtime Actor, and does not tick in a packaged
`-game` build [DOC, Epic "Scripting the Unreal Editor Using Python" + UE forums "Python in
Unreal at runtime"]. The 3rd-party `UnrealEnginePython` (20tab) embeds a runtime VM but is
unmaintained for 5.x and is a heavy, non-shipping dependency. Don't reinvent; don't adopt.

### Candidate B — MPC + a reader Actor/Blueprint that polls the file  ❌ REJECTED (needs BP, adds tick cost, owns a file handle in-engine)
A `BP_AgentReader` Actor with a tick that reads `agent.json` (via the JSON Blueprint
Utilities plugin) and calls `SetScalarParameterValue` on the MPC. Works, but: (1) it
**requires authoring a Blueprint**, which the cooked-build pipeline explicitly avoids
(`indigo_channel_setup.py:447-458` — "we author no Blueprints"); (2) it puts a per-frame
file read + JSON parse **on the UE game thread**, the thread we are trying to keep cheap
under GPU pressure; (3) it makes UE the *consumer*, so a stale/missing file must be handled
twice (in UE and in the shader floor). More moving parts, in the renderer we most want to
keep dumb.

### Candidate C — agentosd → Remote Control HTTP → MPC + cvars  ✅ PICKED
agentosd (which **already** reads `agent.json`/`wind.json` and **already** has the loopback
RC channel designed for the throttle ladder) low-passes the signals and **pushes** them
into the engine. Two sinks, both already proven reachable over `PUT /remote/object/call`:

1. **MPC scalars** (the smooth visual levers): call the engine CDO
   `KismetMaterialLibrary::SetScalarParameterValue` [VERIFIED — `KismetMaterialLibrary` is
   a `BlueprintCallable` runtime library; the CDO-call pattern is identical to the
   `ExecuteConsoleCommand` recipe already verified in `remote_control_setup.md:128-160`].
2. **cvars** (the few engine-state levers — fog density, MaxFPS): the existing
   `ExecuteConsoleCommand` PUT, already fully specified in `remote_control_setup.md`.

**Why C wins, against the constraints:**
- **No Blueprint, no runtime Python.** The MPC asset + material taps are authored
  headlessly (`indigo_reactive_setup.py`); the *driving* is external HTTP. The cooked build
  contains zero new game code.
- **Lock-free by construction.** The pusher is a tokio task mounted exactly like the wind
  sink (`wind.rs:266-290 attach`): it owns only its own eased-state value (`Arc<Mutex<…>>`),
  has **no handle to `lease::Inner`**, and never `.await`s across the file read or the HTTP
  PUT. It is in the **same lane that already carries the `wind.rs` no-lock guarantee** — the
  `wind_path_takes_no_inner_lock` tripwire (`wind.rs:379-392`) extends to it for free. A
  preempt SIGKILL travels the lease lane, which the pusher cannot name or reach. **This is
  the single most important property and it is satisfied structurally, not by discipline.**
- **agentosd is already the right place.** It already reads both feeds for the keyhole and
  the shader floor; making it also the UE pusher means **one consumer of the grammar**, not
  three. UE stays a dumb renderer of pushed scalars (and so does the shader).
- **It degrades cleanly.** If RC is down / UE is mid-relaunch, the PUT fails best-effort
  (fail-open, ADR-0003) and UE simply holds its last MPC values — exactly the wind sink's
  "consumer holds last-good" contract (`wind.rs:36-37`).

**The two known risks of C (flag, don't bury):**
- **`WorldContextObject` on a CDO call** `[VERIFY-LIVE]`. `SetScalarParameterValue(World,
  Collection, ParameterName, ParameterValue)` takes a world. The proven `ExecuteConsoleCommand`
  recipe passes `WorldContextObject: null` and lets RC resolve the active world; whether the
  same `null` resolves for `SetScalarParameterValue` is the one wire-format question to
  confirm on first cook. **Fallback if `null` doesn't resolve:** register the MPC (and/or a
  thin level actor) as a named **Remote Control preset/exposed property** and PUT
  `/remote/object/property` against the exposed object — no CDO, no world context. (Authoring
  a preset is headless via the same Python API; this is the documented property path
  [DOC, RC HTTP Reference: property endpoint works in `-game` if the property is
  `BlueprintVisible` and not `BlueprintReadOnly`].)
- **RC server unauthenticated on `:30010`** — already on record as ADR-0029 Open §B, routed
  to `security-reviewer`. Bound to `127.0.0.1` (`remote_control_setup.md:91-95`); the pusher
  must stay loopback-only and this gate must close before the channel ships.

### Cadence
The feeds are **edge-driven** — `feed.rs:333` only rewrites `agent.json` when the derived
state changes; `wind.rs:250-254` stops touching `wind.json` once neutral. So the pusher is
NOT a busy 60 Hz HTTP loop. It runs an **internal eased-state tick at ~30 Hz** (the
low-pass, in-process, cheap) and **emits an RC PUT only when an eased scalar has moved more
than an epsilon since the last sent value** (e.g. `|Δ| > 0.004`, ~1/255). At rest, the
eased values settle and the pusher goes **silent** — zero HTTP, zero churn, exactly the
wind sink's idle edge-write discipline. A burst (task starts → `busy` 0→0.85) produces a
~1–2 s ramp of small PUTs while the spring settles, then silence.

---

## 2. The MPC scalar set (the renderer-side mailbox)

`MPC_AgentOS_Reactive`, authored by `indigo_reactive_setup.py`. All defaults are the idle
value; agent reactivity = 0 reproduces the unmodified scene.

| MPC scalar    | Range        | Default | Driven by (eased) | Lever (see §3) |
|---------------|--------------|---------|-------------------|----------------|
| `Busy`        | 0..1         | **0.0** | `agent.busy`      | focal-glow lift + breath pace |
| `Warm`        | 0..1         | **0.0** | `agent.warm`      | needs-you dawn behind far blades (RESERVED) |
| `Snag`        | 0..1         | **0.0** | `agent.snag`      | desaturate + dim (calm, never red) |
| `WindGustX`   | -1..1        | **0.0** | wind `dir.x*gust` | light-yaw lateral nudge |
| `WindGustY`   | -1..1        | **0.0** | wind `dir.y*gust` | reserved (vertical fog breath) |
| `MotionSpeed` | 0..1         | **1.0** | governor          | parallax/breath rate (throttle/reduce-motion seam) |

Engine-state levers that are NOT material scalars (pushed as cvars, §3): **fog density**
and **t.MaxFPS / motion**. (A material can't change `ExponentialHeightFog.fog_density`; that
is an engine property, reached by cvar/console, not a Collection Parameter.)

`MotionSpeed` default is `1.0` because it is the **reduce-motion / throttle** seam, not an
agent signal — its "off" is authored pace, and it is excluded from the idle-byte-identical
claim (which is strictly about *agent reactivity* being off).

---

## 3. Signal → lever map (transfer functions, bounds, decay)

The design grammar, ported from what the aurora shader floor already does
(`aurora.frag:663-720` Hills, `921-964` Flow) so the two renderers *speak the same
language*. **Warmth is reserved for needs-you** — no other lever spends warm chroma
(`indigo_channel_setup.py:81-84` palette lock). **Snag is calm, never red.** Every lever
has a hard subtlety ceiling: this is ambient, not a dashboard.

Notation: `s` = the eased signal in [0,1] (or signed for wind). All gains are the *maximum*
contribution at `s=1`; idle (`s=0`) is the static value.

### BUSY → focal-glow lift + advection/breath pace
*"The agent is working: the channel quickens and the cyan core breathes a touch brighter."*
- **Focal glow intensity** (the cyan rake — `INDIGO_LIGHT_INT` base 2000 lux): push a cvar
  *or* an MPC-driven emissive scale. Transfer: `light_scale = 1.0 + 0.18 * Busy`
  → at `Busy=1`, +18% brightness, hard cap. Bounds `[1.0, 1.18]`. **Not** via raw light
  intensity over RC (that risks blowing the manual-exposure look); preferred as an MPC
  scalar multiplying the light's emissive contribution / a bloom-intensity tap so it stays
  inside the post pipeline. `[VERIFY-LIVE]` which tap reads best at `-3.0` exposure bias.
- **Motion pace** (the parallax + light-breath rate): scale `MotionSpeed_effective =
  MotionSpeed * (1.0 + 0.25 * Busy)` → busy quickens the dark-ride drift up to +25%.
  **Crucial honesty/calm bound:** the *base* parallax periods are 41/53/67 s
  (`indigo_channel_setup.py:154-159`); +25% takes the slowest to ~54 s — still far below an
  attention-capture rate. Pace is the cheapest, most legible "working" cue (it's exactly
  what Hills does: `aurora.frag:669` `aflow = flow * aPace`).
- **Decay:** ω ≈ 1.5 rad/s (the wind sink's spring, `wind.rs:58`). A task ending eases the
  lift/pace back to calm over ~1.5–2 s. Never a step.

### WARM → needs-you dawn behind the far blades  (RESERVED — spend warm NOWHERE else)
*"Something is waiting on YOU."* The ONE deliberate warmth (matches `feed.rs:104` the only
warm bloom, and `aurora.frag:706-712`).
- **Mechanism:** an additive warm rim, gathered LOW and at the FAR end of the channel
  (behind `BladeBack`/`BladeFar`), so it reads as a dawn behind the geometry — the depth
  equivalent of Hills' "warm glow behind the far ridges." Implemented as an MPC-driven
  emissive add on a far backdrop / the fog inscatter's far band, NOT on the cyan core.
- **Transfer:** `warm_add = 0.14 * Warm` in a warm hue (≈ #E8B27A, the dawn tint), gated to
  the far depth band only. Bounds `[0, 0.14]`. The art-director owns the exact hue + the
  depth gate; this is the magnitude + placement contract.
- **Why far + low + dim:** keeps the foreground blades legible and keeps warmth from
  fighting the locked cyan-violet palette. Per the per-renderer grammar table doctrine, the
  SAME warm signal sits *behind the far blades* on the Indigo Channel exactly as it sits
  *behind the far ridges* on Hills — same grammar, geometry-specific placement.
- **Decay:** SLOW. ω ≈ 1.0 rad/s (slower than busy) — a dawn, not a flash. ~2–3 s in/out.

### SNAG → desaturate + dim + thicken haze  (calm, never red — design LAW)
*"Stopped, waiting."* Matches `aurora.frag:714-718` and the snag-is-calm law.
- **Desaturate + dim** (material/post): `col = mix(col, luma(col), 0.30 * Snag)` and a dim
  `bright_scale = 1.0 - 0.15 * Snag`. Bounds: desat `[0, 0.30]`, dim `[0.85, 1.0]`. (The
  slab tap in `indigo_reactive_setup.py:wire_slab_snag` is the minimal emissive-dim version;
  the full luma-desaturate belongs in a post material — `[VERIFY-LIVE]`.)
- **Thicken haze** (engine, cvar — fog density is NOT a material scalar): push
  `ExponentialHeightFog` density via console. Transfer: `fog_density_effective =
  0.22 * (1.0 + 0.30 * Snag)` → up to +30% thicker. Reached by a cvar/console PUT, not the
  MPC (it is an engine property). Bounds `[0.22, 0.286]`.
  `[VERIFY-LIVE]`: the exact console route to set live fog density (likely a tagged actor
  property via RC `/remote/object/property` on the exposed fog component, since
  `fog_density` has no global cvar — this is the cleanest non-Blueprint path and is why fog
  is on the *property* sink, not the *cvar* sink).
- **Decay:** ω ≈ 1.2 rad/s — settles "into" the snag, eases out gently. Never red, never a
  pulse, never an alarm. A snag is the room going quiet, not an error light.

### WIND (dir·gust) → light-yaw lateral nudge  (renderer-agnostic, already consumed)
*"You dragged a window; the channel feels the air move."* The wind feed is renderer-agnostic
(`wind.rs` neutral-vector contract: `windDir = dir·gust`, rest ⇒ (0,0)).
- **Mechanism:** nudge the cyan light's **yaw** (the same lever the parallax LevelSequence
  already breathes — `LIGHT_YAW` base 160°, breath ±1.5°, `indigo_channel_setup.py:158`).
  Transfer: `yaw_offset = 2.5° * WindGustX` (signed), added on top of the authored breath.
  Bounds `±2.5°`. So a drag rakes the god-rays a hair in the drag direction, then springs
  back. `WindGustY` reserved for a future vertical fog-breath; default 0, inert.
- **Decay:** the wind sink already eases gust to exactly 0 at rest with ω≈1.5
  (`wind.rs:129-155`). The pusher just forwards the already-eased `dir*gust`; the yaw nudge
  inherits that decay for free and returns to the authored breath with no extra spring.

### Summary of the subtlety ceilings (the calm contract, one place)
| Lever | Max at s=1 | Idle |
|---|---|---|
| focal-glow brightness | +18% | ×1.0 |
| motion pace | +25% (slowest period 41→54 s) | ×1.0 |
| warm dawn add | +0.14 (far/low only, reserved) | 0 |
| snag desaturate | 30% | 0 |
| snag dim | −15% | ×1.0 |
| snag fog thicken | +30% (0.22→0.286) | ×1.0 |
| wind yaw nudge | ±2.5° | authored breath only |

Every ceiling is chosen so that **all signals at maximum simultaneously** still reads as
"the room is a little more awake," never as motion that pulls the eye. (Mirrors the shader's
"contributions stay capped so working + loud music can't compound into a blowout,"
`hills-reactive/README.md:46`.)

---

## 4. Easing / decay — premium + calm, never gimmicky

The hard rule (ADR-0009, ADR-0029 D2): **idle is byte-identical to the unmodified scene**,
and reactivity must read as ambient, not as an attention magnet.

- **Low-pass everything; never step.** The feeds are edge-driven (`feed.rs:333`): a task
  start flips `busy` 0→0.85 in one event. A raw MPC set would *snap* and read as a glitch.
  The pusher runs the **same critically-damped first-order spring as the wind sink**
  (`wind.rs:135` `x += (target − x)·(1 − e^{−ω·dt})`) per scalar. Per-lever ω: busy 1.5,
  warm 1.0 (slowest — a dawn), snag 1.2, wind inherits the sink's 1.5. **Ambient ω 1–2**,
  exactly the design brief.
- **Idle proof, not idle hope.** The day this is wired, capture a **fixed-`iTime`/fixed-seed
  `-game` frame at all-MPC-zero** and `compare`/hash it against the stock Indigo Channel
  capture (`game_shot.sh` already exists). If a tap leaks `+0.0001` into the idle path, the
  "strictly additive" claim dies silently — trust the pixel diff, not the eye. (This is the
  exact idle-drift pitfall; the gate goes in with the branch, not weeks later.)
- **MotionSpeed=0 is the freeze seam.** Already built: `INDIGO_MOTION_SPEED=0` collapses the
  parallax loop to a held pose (`indigo_channel_setup.py:140-145`). The pusher drives
  `MotionSpeed`→0 for **reduce-motion** (read the platform accessibility setting — it MUST
  reach the renderer) AND as the **GPU-throttle** rung (a frozen wallpaper is cheaper). Same
  lever, two callers; reduce-motion always wins.
- **No re-author on a throttle/relaunch.** All reactivity is pushed state, so after a
  kill→relaunch-to-FLOOR (ADR-0004), the relaunched UE boots at MPC defaults (idle) and the
  pusher re-converges it from the live feed within ~1.5 s. Nothing reactive lives in engine
  memory — it lives in `agent.json`/`wind.json` and the pusher's spring, both of which
  survive the relaunch. (Design for restart, not for shedding — the shed-mirage pitfall.)

### Audio → lever map (NET-NEW signal), with HARD subtlety bounds
Music is the riskiest for calm — beat-reactivity is the canonical attention magnet. So its
ceilings are the **tightest** of any signal, and it is **off by default** (opt-in; an
ambient wallpaper should not pulse to your music unless you ask).

- **Signals from `music.json` (producer sketch §audio):** `level` (0..1 smoothed loudness),
  `bass` (0..1 low-band energy), `beat` (0..1 decaying onset envelope), `playing` (bool).
- **Map (all gated behind an explicit `MUSIC_REACT=on` and a master `music_gain ≤ 0.5`):**
  - `bass` → a slow fog-breath: `fog_density_effective *= (1.0 + 0.06 * bass)` — a barely-
    perceptible swell, **+6% max** (a quarter of snag's haze ceiling). ω ≈ 2.0 (music can be
    a touch livelier than agent state, but still eased).
  - `level` → focal-glow shimmer: `light_scale *= (1.0 + 0.05 * level)` — **+5% max**.
  - `beat` → **NO geometry pulse, NO flash.** At most a +0.03 transient on the bloom that
    decays in <300 ms — below the "this is reacting to my music" threshold. Default OFF even
    when music react is on; it is the first thing to cut if it reads gimmicky.
  - `playing=false` → all music levers ease to 0 over ~2 s; the wallpaper returns to pure
    agent-state reactivity.
- **Hard ceiling rationale:** music levers sum to at most +6%/+5% — *strictly smaller* than
  any agent lever — so music can never dominate the agent grammar (a `needs_you` dawn must
  always be visible over the loudest track). This mirrors the shader floor's existing
  `uMusicReact` bass-swell (`aurora.frag:1159-1175`) and its "capped so loud music can't
  blow out" claim — but here the cap is **explicitly about subtlety**, and (per the spike
  gap) the GPU/VRAM marginal cost of the music path must be measured, not assumed.

---

## 5. Cost — per-tick read + push, and 24/7 safety on the shared 4090

The honest accounting (the reactive layer's marginal cost — the thing nobody has measured
yet for the shader, and must be measured here too).

### CPU (agentosd side) — effectively free
- **File reads:** the pusher does NOT re-read the feeds every tick. It reuses agentosd's
  existing edge-driven reads (`feed.rs` already polls every 2 s and rewrites only on change;
  `wind.rs` is the 60 Hz spring that already runs). The pusher subscribes to the in-process
  eased state — no new file I/O beyond what's already there.
- **Spring tick:** ~6 scalars × a 2-line lerp at 30 Hz = nanoseconds. Negligible.
- **HTTP PUTs:** **only on change > epsilon**, then silent at rest. A busy→idle transition
  is ~30–60 small PUTs over ~2 s, then nothing. A loopback HTTP PUT is ~tens of µs of CPU.
  At rest: **zero PUTs, zero cost.** This is the wind sink's idle-edge-write discipline
  applied to the network.

### GPU (UE side) — the levers were CHOSEN to add no passes
- **MPC scalar reads are free.** A Collection Parameter is a global shader constant; reading
  it in an existing material costs nothing measurable (no new texture, no new pass).
- **The levers reuse existing knobs**, exactly like the shader's "reuse advection pace,
  focus breath, haze-mix — don't add a pass" discipline:
  - glow lift = a multiply on existing emissive/bloom (already in the post chain),
  - pace = the parallax rate (already animating),
  - warm dawn = an additive emissive on geometry that already renders,
  - snag desat/dim = a post lerp (PostProcessQuality already on),
  - fog thicken = a parameter on the fog volume that already renders,
  - wind yaw = a 2.5° rotation on a light that already moves.
  **No new full-screen pass, no new render target, no new actor that ticks.** A new
  full-screen pass for an effect would be the expensive mistake; this spec avoids it by
  construction.
- **The one real GPU cost is `Busy`-driven pace** (more frames of motion). At FULL it is in
  the noise; under throttle, `MotionSpeed`→0 (freeze) dominates any busy-pace add, so the
  throttle ladder always wins the GPU-time argument. Net: the reactive layer's GPU marginal
  cost is **near zero by design**, but `[VERIFY-LIVE]`: capture Δfps and Δpower for
  `idle` vs `busy=1` vs `snag=1` vs `warm=1` on the live `-game` build (the measurement the
  shader floor still owes too). Do not claim "free" without those four numbers.

### 24/7 safety
- The pusher is fail-open and lock-free; a wedged HTTP call cannot delay a SIGKILL.
- At rest it is silent (no PUTs, no churn) — a wallpaper that has been idle overnight is
  doing literally nothing on either side.
- Under VRAM pressure the throttle/kill path (ADR-0004/0029 D3) is unaffected — reactivity
  is pushed state that re-converges after a relaunch; it never holds VRAM or a lock.

---

## 6. Same mapping feeds the shader floor — reactivity persists across the crossfade

The whole point of ADR-0029 D2: UE and the shader are **two renderers of one signal**. The
reactivity must not vanish when the wallpaper falls to the floor.

- **One producer grammar.** Both renderers consume `agent.json` (+ `wind.json`, +
  `music.json`). The shader already does (`hills-reactive`, the proven bridge). The pusher
  is just a *second consumer* that translates the same eased signal into UE's MPC/cvars.
  **There is no second producer and no second grammar** (ADR-0029 carry-forward: "we add a
  renderer, not a new producer grammar").
- **Identical transfer functions, two backends.** The §3 map is written to *match the
  shader's existing levers*: busy→pace+brightness (`aurora.frag:666-686`), warm→far/low dawn
  (`706-712`), snag→desaturate+dim+haze (`714-718`), wind→`dir·gust` (`wind.rs` contract).
  So the *meaning* of every signal is the same in both — a `needs_you` is a far-low warm dawn
  whether UE or the shader is presenting. The gains differ in magnitude (UE +0.14 warm add
  vs the shader's branch gain) because the geometries differ — this is the per-renderer
  grammar-table doctrine, not a divergence.
- **The crossfade lands on the same look.** When UE is killed/relaunched-to-shader-floor
  (ADR-0004/0029 D1), the shader is already reading the live `agent.json`/`wind.json` and is
  already at the correct reactive state — there is no reactive "re-sync" needed, because the
  shader never stopped consuming the feed. The fall is a *renderer* swap, not a *signal*
  swap: a `busy=0.85` desktop shows busy-pace in UE and busy-pace in the shader, so the
  ~800 ms crossfade is between two views of the *same* busy state, not from "reactive" to
  "dead." (Hold-last-good on both sides means no blank-flash even if a feed read fails
  mid-swap.)
- **The eased state lives in agentosd, shared.** Because the spring runs in the pusher/
  producer (not in either renderer), the *eased* value is consistent across the swap — the
  shader's own low-pass and the pusher's low-pass both track the same edge-driven feed with
  the same ω family (1–2), so neither renderer snaps on the handoff.

---

## 7. Hand-offs (by exact agent name)

- **`generative-artist` / `art-director`:** own the exact warm hue (#E8B27A is a placeholder),
  the far-depth gate for the needs-you dawn, and whether the snag desaturate reads right at
  −3.0 exposure. The §3 *magnitudes + placement contract* is mine; the chroma is theirs.
- **`motion-designer`:** owns the per-lever ω choices (busy 1.5 / warm 1.0 / snag 1.2 /
  music 2.0) and the +25% pace ceiling — the timing/decay feel is their lane; I've set
  defaults that match the wind spring.
- **`rust-performance-reviewer`:** the pusher's lock-free mounting in the wind/feed lane (NOT
  lease), the epsilon-gated PUT cadence, and the "never `.await` across the HTTP call" shape —
  the same review `wind.rs` got.
- **`resource-safety-reviewer`:** confirm the pusher cannot delay a SIGKILL (the
  `wind_path_takes_no_inner_lock`-style tripwire must extend to it), and the relaunch
  re-converge story.
- **`security-reviewer`:** RC `:30010` lock-down (ADR-0029 Open §B) before the channel ships.
- **`wayland-computeruse-reviewer`:** unchanged — this spec assumes the Open §A wallpaper-
  layer probe passes; reactivity is moot until UE is actually the wallpaper. If §A fails and
  fallback "C" returns, the pusher targets the aurora `ShaderEffect` uniforms instead of UE
  MPC scalars — the §3 map is identical, only the sink changes.

---

## Sources
- Epic — Scripting the Unreal Editor Using Python (editor-only at runtime):
  <https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python>
- Epic — Using Material Parameter Collections in Unreal Engine (5.7/5.8):
  <https://dev.epicgames.com/documentation/en-us/unreal-engine/using-material-parameter-collections-in-unreal-engine>
- Epic — Remote Control API HTTP Reference (`/remote/object/call`, `/remote/object/property`,
  CDO `Default__` path, `-game` property rules):
  <https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-api-http-reference-for-unreal-engine>
- UE forums — Python in Unreal at runtime (confirms no runtime tick in packaged builds):
  <https://forums.unrealengine.com/t/python-in-unreal-at-runtime/637685>
- In-repo verified: `spikes/ue-probe/remote_control_setup.md` (RC channel, loopback, CDO
  call recipe), `spikes/ue-probe/cvar_ladder.md` (throttle/cvar surface),
  `crates/agentosd/src/wind.rs` (lock-isolation guarantee + spring), `crates/agentosd/src/feed.rs`
  (edge-driven producer), `spikes/hills-reactive/aurora.frag` (shader floor lever grammar).
