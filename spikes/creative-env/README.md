# spikes/creative-env — Phase-0 creative pipeline (prompt → themed 3D env → tracked move)

**Throwaway spike** (`spikes/` is excluded from the cargo workspace). Proves the Design-0023
Phase-0 loop end-to-end in Blender/EEVEE: a **validated brief JSON** becomes a coherent, themed 3D
environment with a **dual-purpose path** (visible trail + camera rail), rendered to a short
painterly clip and handed to the lucid dream tree.

Canonical first scene: **a waving amber field of grass, stylized like an oil painting**
(`briefs/amber_field.json`, the exact brief from Design-0023's "Canonical first sample").

This drives Blender **directly** (`blender --background --python`), NOT the forge MCP server, so the
loop is independently verifiable. The production lease wrap is documented, not wired (see *Lease* below).

## The non-negotiable: model proposes, **code disposes**
The pipeline's input is a *validated brief* (`validate_brief.py`). There are **no model calls in
this spike** — the brief is the model's only output, and everything downstream is deterministic
Python:
- **placement** — `layout.py` (pure math) + `build_scene.py` (bpy) compute every transform;
- **palette clamp** — every material albedo runs through `clamp_color` → nearest locked-palette
  entry, so colour can never leave the brief's `palette`;
- **camera spline** — `lay_path.py` lays ONE Bezier from `path.from`→`path.to` that is both the
  visible mown-trail and the camera rail; `camera.subject`/`move` are *intent*, code disposes the
  actual aim and travel;
- **the validator** — `lay_path.validate_path` raycasts N stations for camera-in-mesh / occluded
  look-at and deterministically widens-and-retries; a clipping path is the failure it kills. The
  **verdict ACTS, it is not just logged** (see *The gate acts* below): an invalid path triggers a
  deterministic degrade to a safe camera, and a still-failing path is flagged in the manifest **and**
  marked on every frame. The clip/occlusion *verdict* + its **per-kind thresholds** are extracted into
  `clip_verdict.py` (PURE geometry, no `bpy`, unit-tested);
- **style** — `painterly.py` (Kuwahara-ish smooth + posterize + grain) is a system-owned grade,
  never a prompt word.

## The gate acts (not a report) — Design-0023 P0.3
A gate that never declines is a report. `render_move.py` used to render unconditionally and only
*record* `valid`. Now the verdict drives the disposal (ADR-0003 fail-open):

1. **valid** → render the brief's camera move as-is.
2. **invalid** (clips / occlusions after `max_regen` widen-retries) → **deterministically degrade**
   to a *safe* camera: `lay_path.degrade_camera_to_safe` lifts the eye to `SAFE_EYE_HEIGHT` (12 m,
   above every catalogued occluder height), forces a **level** arc, and shortens travel to
   `SAFE_DRIFT_END` (0.20 of the rail) — a raised, level, short drift that *cannot* dip under or be
   occluded by scene geometry. Then re-validate. The manifest records `degraded: true` + the
   `pre_degrade` clip list.
3. **still invalid** after the safe fallback → `validator_failed: true` in `render.json` **and** a
   **magenta warning band** stamped on every frame (`painterly._stamp_failed_marker`). A reviewer can
   never mistake a clipping move for a clean one — the proposal is flagged in the data *and* on screen.

Per-kind thresholds (P2.11): the occlusion check is a **pure function of the brief's element kinds**
(`clip_verdict.thresholds_for_kinds`). Grass legitimately fills the near, low foreground, so a grass
hit that is near + low is foreground clutter, not an occluder; a `column`/`vault` does **not** get
that pass — a near, low pillar hit is exactly the clip the gate must catch. A scene takes its
*strictest* occluding element's tolerance, so adding a column to a grass brief tightens the gate
rather than letting the pillar hide behind the grass rule. Unknown kinds fail **safe/strict**.

The random seed is fixed (`layout.SEED`). The **scene geometry is byte-reproducible** (verified, see
*Determinism*).

## File tree
```
spikes/creative-env/
├── README.md                 # this file
├── .gitignore                # out/ (render output) + __pycache__/ are regenerable, not committed
├── brief_schema.json         # the model→code contract: schema VERSION + enums/allowlists (camera.move, …)
├── validate_brief.py         # PURE PYTHON validator (+ schema-version gate) + nearest-in-palette clamp_color (no bpy)
├── clip_verdict.py           # PURE PYTHON clip/occlusion VERDICT + per-kind thresholds + geometry-hash gate (no bpy)
├── layout.py                 # PURE PYTHON deterministic geometry: field extents, the trail Bezier, raycast helpers
├── build_scene.py            # bpy [1][2]: ground + 50k grass scatter (geometry nodes) + golden-hour sun + wind-wave
├── lay_path.py               # bpy [3][4]: the dual-purpose spline + camera rig + raycast validator (verdict ACTS)
├── painterly.py              # [5] painterly post-grade — numpy post-pass (primary) + validator-failed marker
├── render_move.py            # in-Blender entrypoint: validate→build→lay→(degrade if invalid)→render→grade→manifest
├── run.sh                    # drives flatpak/system Blender headless, then encodes the mp4 via system ffmpeg
├── briefs/
│   └── amber_field.json      # the canonical brief (Design-0023) — now carries "schema": "0.1.0"
└── tests/
    ├── test_validate_brief.py  # 15 contract tests (off-vocab rejection + palette clamp + schema versioning)
    └── test_clip_verdict.py    # 16 verdict tests (clip/occlusion, per-kind thresholds, geometry-hash gate)
```

## How to run
```bash
cd spikes/creative-env

# pure-python contract gate (no Blender, no GPU):
python3 validate_brief.py briefs/amber_field.json
python3 tests/test_validate_brief.py            # 15/15 (contract + schema versioning)
python3 tests/test_clip_verdict.py              # 16/16 (verdict + per-kind thresholds + geometry hash)

# full pipeline (14s drift @ default fps → frames + graded frames + mp4 + .blend):
./run.sh                                         # canonical amber field
./run.sh --quick --res 480                       # fast 6-frame smoke
./run.sh --fps 8 --res 480                       # what was used to verify (≈14s wall-clock)
./run.sh briefs/other.json                        # any other validated brief
BLENDER=blender ./run.sh                          # use a system blender instead of flatpak
```
Artifacts land in `out/`: `frames/` (raw EEVEE PNGs), `graded/` (painterly), `move.mp4`,
`scene.blend` (reproducible), `render.json` (the validation manifest).

Seed the dreaming tree from the first frame (the lucid seam, `lucid_engine.py:269`):
```bash
python3 ../dreaming/lucid/lucid_engine.py start amber --image "out/graded/g_0001.png"
```

## Lease (production hook — documented, NOT wired here)
In production this render runs **owned by the agentosd VRAM coordinator under the `AdoptScope`
lease** (ADR-0022 / Design-0022), exactly as `integrations/blender/phase0-render.sh` wraps
`render.py`: the daemon `Spawn`s the flatpak, `AdoptScope` moves the flatpak's transient scope under
agentosd's cgroup so a preempt SIGKILLs the whole render and reclaims VRAM, and the lucid seam is
driven by the substrate, not by hand. The spike **deliberately omits the lease** so it stays
independently runnable. See the note at the bottom of `run.sh`.

## What's deterministic
- **Brief validation + palette clamp + schema version** — pure functions, unit-tested (15/15).
- **The clip/occlusion verdict + per-kind thresholds** — pure geometry in `clip_verdict.py`,
  unit-tested (16/16) **without Blender**: clean field, planted-occluder rejected, recovered-by-widen,
  and the strictest-occluder threshold rule.
- **Scene geometry** — fixed seed → identical 50k-instance scatter and trail control points across
  independent builds. *Verified*: hashing the evaluated 125k-vertex grass mesh gave the **same
  SHA-256** on two separate Blender invocations (`ab334840…`), as did the trail spline.
- **Decision-geometry hash gate** (checked-in golden) — `clip_verdict.scene_geometry_sha256` hashes
  the trail control points + the validator's 24 stations + the per-kind thresholds (rounded coords,
  canonical JSON), **not the pixels** (EEVEE AA makes PNGs non-byte-reproducible — see below). The
  canonical amber field is pinned at `2dc89fac…` in `tests/test_clip_verdict.py`; `render.json`'s
  `report.geometry_sha256` is computed through the same pure path and **matches the golden** end-to-end
  (verified this pass). A change to that hash means a *decision* changed — re-pin if intended, else it
  is a determinism regression.
- **Painterly grade** — fixed numpy RNG seed (`0xCA11A5`) for the grain.
- **Camera move** — derived from the brief + fixed layout, no randomness; the safe-degrade fallback is
  itself deterministic (fixed eye height + travel, LINEAR).

## Brief-schema versioning (Design-0023 P3.13)
The brief now declares a SemVer `schema` (`"0.1.0"`), and `validate_brief.py` **requires** it and
rejects an incompatible MAJOR (`SUPPORTED_SCHEMA_VERSION`). This is the **two-disposer contract gate**:
before Unreal becomes a second consumer of the brief, every disposer must agree on the contract MAJOR
or refuse to dispose it, rather than silently mis-reading a contract it does not understand. MINOR/PATCH
ahead of the disposer is accepted (additive-forward-compatible within a MAJOR); the schema keeps
`additionalProperties: false` rigor, so an unknown key is still rejected.

The only run-to-run variance is **EEVEE's anti-aliasing dither** (TAA sampling has no exposed seed;
`use_taa_reprojection` is disabled to minimize it). That is renderer-pixel noise, **not** a
pipeline-decision nondeterminism — the *decisions* (where every blade sits, the spline, the palette,
the aim) are byte-reproducible.

## What actually ran here vs not (honest)

**Box:** flatpak `org.blender.Blender` **5.1.2**, Python 3.13.9, **EEVEE-only** (no Cycles), numpy
2.3.4 inside Blender, no PIL, system ffmpeg n8.1.1.

**VERIFIED (ran, with evidence):**
- Full pipeline end-to-end, **~14 s wall-clock** for the 14 s @ 8 fps drift (112 frames):
  validate → 50k grass scatter → palette-clamp → spline → **raycast validator (24 stations clear,
  0 clips, 0 occlusions, 0 regens)** → render → painterly grade → **valid 480×270 h264 mp4, 14.0 s,
  112 frames** (`ffprobe`-confirmed) → `scene.blend` saved.
- The amber field reads correctly: a horizon line in the upper third, dense amber grass catching the
  low sun, the **mown trail visible** cutting through the field, the painterly posterize/smooth grade
  giving the oil-paint flattened-region look (green palette note mixing into the ambers).
- **The drift moves**: frame 1 vs 56 vs 112 show the camera advancing down the trail while staying
  composed on the field (the drift is capped to 0.45 of the rail so it never overruns into empty sky).
- **The lucid seam works live** — `lucid_engine.py start … --image out/graded/g_0001.png` created the
  session and copied the painterly frame into ComfyUI's input dir as the dream-tree root anchor +
  wrote `tree.json` (test session then cleaned up).
- **Geometry determinism** — proven by mesh-hash equality across two builds.
- **Contract gate** — 10/10 unit tests: off-vocabulary `camera.move`/`path.render_as`/binding-value,
  dangling `camera.subject`, bad hex, unknown top-level key all **rejected**; palette clamp
  deterministic + lands in-palette.

**NOT verified / didn't run as first designed (and why):**
- **The compositor painterly route blows to white on this build.** A *passthrough* scene-compositor
  graph clips the frame to white (bisected: not Kuwahara/posterize — the scene-compositor output
  skips the view transform on this 5.1.2 flatpak). So the painterly grade runs as a **numpy
  post-pass over the display-referred PNGs** (which works and looks right). `painterly.apply()` (the
  compositor route) is kept for builds that color-manage the compositor correctly. *The
  design-council should note the canonical "EEVEE compositor Kuwahara" path is build-fragile; the
  numpy pass is the portable one.*
- **No Cycles, so no photoreal.** Phase 0 is procedural/EEVEE by design (Design-0023 §[1]); this is
  the intended trade, not a gap.
- **No GPU power/VRAM numbers.** The render is light (instanced scatter, EEVEE, low res) and ran in
  seconds; I did not attach NVML deltas because the spike is unleashed and the box was shared. A
  leashed run under `agentosd lease` would be where to measure marginal cost.
- **Live `bindings` (window-drag → wind) not wired.** The brief carries + validates `bindings`, but
  the live shader-uniform loop is a *parallel track* (Design-0023 Interactivity) needing the KWin
  window-drag producer; out of Phase-0 scope. The baked film's wind is one deterministic direction
  (correct — ADR-0009's argument for the shader, not video, carrying live motion).

## Gaps the design-council should scrutinize
1. **`camera.subject` is intent, and code MUST dispose the aim.** Tracking the literal subject object
   (here the `horizon` *ground plane*) pointed the lens straight down → a flat colour wash. The fix
   (a code-chosen far, level framing aim) is the right "code disposes" call, but it means the brief's
   `subject` is weaker than it looks — it selects *what to frame on*, and code owns *how*. Worth an
   explicit note in the schema doc.
2. **Composition tuning is hand-set, not solved.** Vantage height (3.5 m), aim distance, blade height
   (1.4 m), and the drift cap (0.45 of the rail) were tuned by eye to keep the field composed. A
   real "N-element layout solver" (Design-0023 open-q 3) would derive these; today they're per-style
   constants. A different brief (taller elements, a `reveal` move) will need its own tuning table —
   the same "per-wallpaper grammar table" lesson from the reactive shader.
3. **The compositor Kuwahara path is build-fragile.** It is the design's *preferred* painterly route
   ("via EEVEE's compositor nodes"); it does not survive this flatpak's color management. Decide
   whether the house grade is the portable numpy pass (recommend) or a compositor graph gated on a
   per-build smoke check.
4. **Renderer-pixel nondeterminism vs decision-determinism.** *(addressed P0.3/P2.11 pass)* The
   geometry is byte-reproducible but the rendered PNGs are not (EEVEE AA). The checked-in gate hashes
   the **scene/decision geometry**, not the pixels (`clip_verdict.scene_geometry_sha256`, golden in
   `tests/test_clip_verdict.py`), exactly the hills-reactive idle-diff discipline applied to decisions.
5. **Validator strictness.** *(resolved this pass — P2.11)* The grass-specific thresholds are gone; the
   occlusion check is now a **pure function of the brief's element kinds**
   (`clip_verdict.thresholds_for_kinds`): grass ignores near/low hits, a `column`/`vault` does not, and
   a scene takes its strictest occluder's tolerance so a pillar can't hide behind the grass rule.
   Unit-tested without `bpy` (clean field / planted occluder rejected / recovered-by-widen). *Open:*
   `build_scene.build` still only places grass + ground, so a `column` brief validates the thresholds
   but does not yet *place* the column geometry — placing non-grass kinds is the next build step.
