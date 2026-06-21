# ADR-0032: Lucid — click-to-segment spatial markup (segmentation-produced attention masks; extends ADR-0025)

- Status: **Proposed — §S(a) spike PASSED on the box 2026-06-21; §S(b) premise-check + §S(c) LTX render
  still owed before Accepted.** The design below is reviewed (8-lens panel, 2026-06-21, verdict *iterate*).
  The external segmenter — the one thing the ADR could not self-verify — is now **installed + measured on
  the 4090** (`spike_sam2_segment.py run`): node **`Sam2Segmentation`** (loader `DownloadAndLoadSAM2Model`),
  point input **`coordinates_positive`** = JSON `[{"x":int,"y":int}]`, image input `image`, model input
  `sam2_model` (SAM2MODEL), **MASK** output slot 0; checkpoint **`sam2.1_hiera_small` Apache-2.0**;
  **measured peak ≈ 6.6 GB total during a tap (~1.3–1.5 GB SAM increment), 102 MiB resident after**
  (`keep_model_loaded=False` releases — no squat), ~2 s single-shot. A real SAM mask flows through the
  `load_validated_mask` gate (accept on matching aspect, reject-to-disc on mismatch) — verified.
- Date: 2026-06-21
- Extends, does not supersede, [ADR-0025](0025-lucid-dream-tree-and-spatial-feedforward-annotations.md):
  ADR-0025's amendment shipped a *point* on a note (`x,y,r`) that the engine turns into a feathered
  **soft-disc** attention mask (`_ltx_softdisc_mask` → `LoadImageMask → attention_mask` on
  `LTXVAddGuideAdvancedAttention`). That delivered *where on the timeline* + *roughly where on the
  frame*, but not "**mark up part of the screen**" — a soft disc is not the object. This ADR makes a
  **segmentation-produced object mask the primary `attention_mask`**, and **demotes the soft-disc to the
  deterministic fallback floor**. The attention *plumbing* ADR-0025 built — the graph rewrite in
  `_inject_ltx_guides`, the all-or-nothing attention invariant, the neutral region-less siblings, the
  sealed-subdir co-location, the kill-switch — is reused unchanged. What is **net-new** (and where the real
  risk lives) is a one-branch engine change plus a schema migration: `add_note` gains a `mask` param + a
  tag-time persist, the guide tuple grows a 5th element, and `_inject_ltx_guides` gains a *resolve-stored-
  PNG-or-fall-back-to-disc* branch. (Today the engine has **no** external-mask path — it always synthesizes
  the disc internally; "swap the producer" is the spirit, not a zero-downstream change.)
- Relates to: [ADR-0001](0001-substrate-not-orchestrator.md) (substrate **not** orchestrator, and
  **don't reinvent** — the segmenter is ComfyUI's own SAM2 node, the conditioning seam is ComfyUI core's
  `LTXVAddGuideAdvancedAttention`; net-new is glue only),
  [ADR-0005](0005-apply-rollback-transaction.md) (apply/rollback — a note is a kept artifact and its mask
  an append-only kept file: a re-tap writes a **new** content-addressed mask, never mutates a kept one;
  toggling segmentation off reverts to the disc),
  [ADR-0006](0006-hermes-plugin-no-fork.md)/[ADR-0010](0010-vram-coordinator-overnight-batch-lane.md)/
  [ADR-0013](0013-coordinator-ipc-trust-and-lease-lifecycle.md) (the VRAM coordinator lease — the segment
  graph runs **inside the already-leased warm ComfyUI**, under the *existing* batch token, with **no second
  Acquire and no self-preemption**; the only new arbitration is a pre-flight free-VRAM headroom read — see
  §2),
  [ADR-0016](0016-lucid-private-ephemeral-mode.md) (private ephemeral — the extracted frame **and** the
  mask seal in `.lucid-priv-<s>/` via the store API and the burn wipes them),
  [ADR-0017](0017-lucid-b2-seed-likeness-guard.md) (B3 red-line — a mask passes a *different* deterministic
  gate (§3 binarize/area-bound), **not no gate**; the note's free **text** still passes the red-line gate),
  [ADR-0018](0018-vram-coexistence-budget-partition.md) (coexistence — adding ~2 GB of SAM to the resident
  ComfyUI footprint is the warm-pool headroom this partition reasons about; the headroom check is the
  guard),
  [ADR-0020](0020-agent-facing-gpu-mcp-and-admission-feedback.md)/
  [ADR-0022](0022-creative-app-mcp-blender-unreal.md) (a leased GPU client; do **not** let a model squat
  VRAM outside the lease — the `inference-cpu-offload-comfyui` pain — which is exactly why the segment runs
  *inside* the lease-held process, not as a second tenant),
  [ADR-0023](0023-creative-environment-pipeline.md) (the `10eros`/LTX lane the guide conditioning rides;
  the still-open graphics-yield-not-in-lease gap it names is why a live desktop can degrade a tag to the
  disc — see §5).
- Evidence (the seam this lands on, all live): `spikes/dreaming/lucid/lucid_engine.py`
  (`_inject_ltx_guides` ~576–700 already wires an **arbitrary-shape** `LoadImageMask` PNG as the
  `attention_mask`, sized to the **seed** at render time, `lucid_engine.py:628`; `_ltx_softdisc_mask` ~140
  is the generator being demoted; `extract_frame_at` screenshots the clip frame),
  `lucid_linear.py:246` `add_note(...,x,y,r)` (the schema gaining `mask`) + `_anchor_for` ~530 (the render
  seed is the parent's *last* frame or a hold-frame — **not** the tapped frame; the source of the §4
  divergence), `lucid_web.py` (`do_POST` CSRF+Origin allowlist `/api/note`; the new `/api/segment` joins
  it), `comfy_client.py:469,502` (`output_files()` *sees* the `images` key but `generate()` filters to
  `VIDEO_EXTS` and falls back to `_newest_video` — the IO-contract fix in §2),
  `web/src/Chain.tsx` (`placePoint`/`draftPt` ~155, `canTag` ~242, the tap overlay ~459), tests
  `test_lucid_engine_10eros.py` (the `test_inject_guides_plain/attention/killswitch` trio to extend).
- Research provenance (2026-06-21, three sourced web passes; current mid-2026, past the Jan-2026 cutoff):
  SAM 2.1 is **Apache-2.0** with an encode-once / decode-per-click design; kijai's
  `ComfyUI-segment-anything-2` is reported to expose a headless **point-prompt → MASK** node
  (`Sam2Segmentation`, `coordinates_positive` `forceInput` JSON, plus a companion model-loader node);
  **SAM 3 / 3.1** (Nov-2025 / Mar-2026) is the wrong tool here — ~1.1 s/image on a 4090, ~3.5 GB resident,
  gated **non-Apache** "SAM License" — watch-list for a future *text-prompt* mode only; the attention mask
  is **downsampled to the guide latent grid**, so boundary sharpness beyond that grid (SAM-HQ's edge) is
  largely wasted. **All node/weight/license specifics above are unverified on this box** (§S gates them).

## Implementation review (2026-06-21, 5-lens adversarial pass — iterate 6.4 → fixed)

The built vertical slice (engine gate/producer/consumer, `/api/segment`, schema, frontend) was reviewed by
5 lenses (engine bug-hunt, route security, privacy, VRAM safety, frontend a11y). Verdict *iterate*; the
core (deterministic gate, CSRF/Origin inheritance, int-only graph, no-store preview, per-tap fail-open) was
credited correct. Two blockers + three majors + minors were found **and fixed**: (1) **privacy** — the raw
SAM2 `SaveImage` output persisted in the shared `~/ComfyUI/output/lucid/` (no burn/logout reach); now
`segment_at` deletes it unconditionally (verified: 0 leftover). (2) **frontend** — `.aim-mask` lacked
`mask-mode`, so the opaque-RGB mask flooded the whole frame; now `mask-mode:luminance`. (3) **in-flight**
— `/api/segment` now refuses during a live beat (`TURN["phase"]=="dreaming"`) and serialises on a non-
blocking `_SEG_SEM` (closes the admission TOCTOU + the segframe race). (4) **append-only** — the mask is
now **content-addressed** (`blake2b`), so a second tap on a node can't overwrite/alias a kept mask;
`remove_note` only unlinks a mask no surviving note references. (5) **a11y** — a focusable arrow-nudge
crosshair + Enter gives a keyboard path (WCAG 2.1.1). Minors fixed: tightened `_valid_mask_ref` (anchored
prefix + name regex, not substring), boundary pixel clamp (`min(fw-1, …)`), preview-gated saved mask.
Deferred (taste): NVML-global free vs allocator-local `vram_free`. Tests: engine 17, linear 27 green.

## Context

ADR-0025 (and its 2026-06-20 amendment) gave a note a *where*: tap the clip while tagging, store a
normalized point `(x,y)`, and the engine paints a feathered **disc** at that point as the guide's
`attention_mask` — "more/hold *here*, let it change *there*." Shipped and GPU-verified: the masked region
changed 1.12× the rest (a *weak* attention effect — see the §S measurement gate; it bears on whether a
silhouette beats a disc at the coarse latent grid the model actually conditions on).

But a disc is a guess at a shape. Tagging "make the lantern brighter" drops a blurry circle that bleeds
onto the wall behind it and clips the lantern's handle; the steer is *approximately* the object and
*approximately* the background. The user's verdict on the shipped feature: **"you are able to tag but it
does not allow you to mark up part of the screen."** The load-bearing miss is shape — the steer should be
*the object you pointed at*, with the object's own silhouette, not a soft blob centered on it.

The good news (and the reason this is an extension, not a rewrite): **ADR-0025 already built the attention
plumbing.** `_inject_ltx_guides` consumes an *arbitrary-shape* grayscale mask PNG via ComfyUI core's
`LoadImageMask → attention_mask` (consumed at sample time as `weights = pixel_mask * attention_strength`;
white = attend-to-the-guide-here). The disc is just one cheap producer of that PNG. **Produce the PNG with
a segmenter instead and the attention plumbing is untouched** — what changes is one engine branch, the note
schema, and the UI. The three pieces: (1) a segmenter that turns a tap into the tapped object's mask,
(2) that mask carried on the note and used as the guide, (3) a UI that *highlights* the object on click.
The non-negotiable that shapes all three: a GPU segmenter must run **inside the existing lease** and **never
squat VRAM**.

## Decision

### 1. The mask is the unit; the segmenter produces it; the attention plumbing is ADR-0025's, unchanged

Click an object on the clip → a SAM2 **point prompt** at the tap returns that object's mask → the
validated mask PNG becomes the note's `attention_mask`, dropped into the *same* `LoadImageMask` slot the
soft-disc used. `_inject_ltx_guides`'s graph rewrite, the all-or-nothing attention promotion (a chain with
any regional note promotes **all** guides to `LTXVAddGuideAdvancedAttention`; region-less siblings stay
**neutral**, `attention_strength` 1.0, no mask), the per-tag `strength`/`attention_strength` knobs, the
fps/frame-idx math, and the fail-open splice are **carried verbatim**. The net-new code is one branch: the
guide PNG is a *validated stored segmentation mask* if the note has a usable one, else `_ltx_softdisc_mask`.

### 2. Segmenter: SAM 2.1-small, ComfyUI's `Sam2Segmentation` node, run *inside the warm lease* — no second lease, headroom-gated

- **Model: SAM 2.1-small.** Apache-2.0 (MIT-clean); native single-**point**-prompt with the
  encode-once/decode-per-click design; masks ample for a guide *downsampled to the latent grid anyway*
  (which is also why we do **not** pay for SAM-HQ). Its real resident/peak VRAM at lucid's actual extracted-
  frame resolution is **measured by §S**, not assumed (ADR-0004's lesson: self-reported sizes undercount).
- **Integration: kijai `ComfyUI-segment-anything-2` → `Sam2Segmentation` (+ its model-loader node).** The
  dream backend is **already** ComfyUI on the 4090, so the mask is produced in the **same process** that
  holds the lease. We build a **tiny single-purpose segment graph** (load image → load SAM2 → segment at the
  point → `MaskToImage` → `SaveImage` an **RGB PNG with the mask in the red channel**, since the guide reads
  `LoadImageMask(channel='red')` and the engine already stacks RGB), not a hand-rolled segmenter.
- **The lease, correctly (this is the fix the review forced).** The lease as built
  (`crates/.../lease.rs:183`) is **single-exclusive and preemptive**: a higher-tier `Acquire` *preempts →
  SIGKILLs* the holder; admission's predict-before-load gates only a **fresh** grant. ComfyUI is the
  **warm-kept batch-lease holder** (`lucid_web.py _ensure_lease`, ~17–22 GB, idle-reaped at 600 s). So:
  - **No second lease.** The segment graph is POSTed to the **already-leased warm ComfyUI under the
    existing batch token** — same process, same tenant. A new *interactive-tier* lease would preempt and
    **SIGKILL the very ComfyUI we must POST to** (self-preemption, cold ~17 GB restart next beat); that
    design is wrong and is **not** used here.
  - **Headroom check instead of admission.** Riding the warm process means SAM's ~2 GB increment gets **no
    coordinator arbitration** — so before POSTing, a **pre-flight free-VRAM read** (NVML or ComfyUI
    `/system_stats`) gates it: if `free < SEG_PEAK_MIB + SEG_HEADROOM_MIB`, **do not POST — fall back to the
    soft-disc**. A tag can never OOM-kill the warm dream model.
  - (A *true* co-resident second lease would require a substrate ADR adding two-number/co-residency
    admission to agentosd. That is **out of scope**; flagged so a second-lease design can't land as silent
    drift.)
- **IO contract (the second fix).** `comfy_client.generate()` filters outputs to `VIDEO_EXTS` and falls
  through to `_newest_video` — driving the segment graph through it would silently return a **stale clip**
  and poison the gate. The segment graph is therefore driven via a **net-new image-output path**
  (`comfy_client.submit/wait` + read the `SaveImage` filename from `history.outputs[node]["images"]`), and a
  structural test asserts an alpha/grayscale-wiring regression can't be swallowed by the disc fallback.
- **Single-shot is fine.** ComfyUI's `/prompt` is stateless (re-encodes per call), but the gesture is **one
  tap** — exactly one encode per note — so the interactive-SAM "encode-once service" is unnecessary; SAM2-
  small's single pass is cheap on a *warm* process (the cold case is §5's honesty problem, not a throughput
  one).

### 3. Model proposes, code disposes — one pure gate, pinned constants, run at *render* time too

The segmenter **proposes** a mask; a single pure function **disposes** of it deterministically — at tag
time *and again* at render time (a stored artifact is on-disk mutable state; never trust a validated-once
file verbatim). `load_validated_mask(png) → Optional[clean_png]`, evaluated in a **fixed order** with disc
fallback on **any** `None`:

1. unreadable / missing → `None`
2. **binarize** SAM's field at `SEG_BINARIZE = 0.5` → clean 0/255 (the guide reads `channel='red'`)
3. **clamp** to the frame; all-zero after clamp → `None`
4. **area-bound**: fraction `< SEG_MIN_AREA = 0.003` (specks) or `> SEG_MAX_AREA = 0.85` (near-full-frame,
   the ambiguous-tap-grabbed-the-background case) → `None`
5. (optional) `> SEG_MAX_BLOBS` disconnected components → `None`

These are **shipped defaults**, pinned as named module constants like every analogous engine constant
(`LTX_GUIDE_STRENGTH`, `DEFAULT_NOTE_RADIUS` clamp `0.02..0.9`); they are *tunable*, not *undefined*. The
mask index is selected deterministically (**take output index 0**; same `(frame, point, weights)` → same
index). The tap coordinate stays clamped (ADR-0025 invariant) and is **kept on the note** as the fallback
disc center and a stable record. This is the crux: a non-deterministic pixel field is **validated,
thresholded, and bounded by code** into a known-shape artifact, with a deterministic fallback, before it
conditions anything — **at every point it could enter the graph**.

Offline boundary tests (mirroring the existing `test_inject_guides_*` trio): a mask at `SEG_MAX_AREA+ε`
falls back to disc; at `SEG_MIN_AREA−ε` falls back; a stored mask whose aspect mismatches the seed falls
back (§4); the kill-switch ignores a present mask.

### 4. Schema + timing: the note carries a `mask` ref; the tag-time/render-time gap is a gate decision, not a silent resize

- `add_note(..., mask=None)` gains an **optional** session-relative path to the stored validated mask PNG,
  **plus** the tag-time frame's `(w,h)`. The note **keeps** `(x,y,r)` (the fallback disc + the record);
  `mask` is additive. Legacy point-only and time-only notes are byte-identical. `remove_note` unlinks the
  note's mask PNG (and transient frame); a re-tap writes a **new** note-id/content-addressed PNG (append-
  only — ADR-0005).
- **The divergence is real and gated.** ADR-0025's disc never diverges because `_ltx_softdisc_mask` is
  generated **at render time, sized to the seed** (`lucid_engine.py:628`). A segmentation mask must be
  computed **at tag time** (the user must *see* the highlight; the segmenter needs the actual pixels), but
  the next beat conditions on `_anchor_for()` — the parent's **last** frame or a hold-frame, generally **not
  the tapped frame**, possibly a different aspect. So at render `_inject_ltx_guides` resolves the guide PNG
  as: *note has a `mask` AND its stored `(w,h)` aspect matches the seed within tolerance* → **letterbox-
  resize** (never raw-stretch — a stretch relocates the silhouette) to the seed, **re-run §3** (on the final
  seed canvas — the black letterbox padding *dilutes* the area fraction, so a near-MAX mask can fall below
  MIN), use it; *else* → `_ltx_softdisc_mask` from `(x,y,r)`. The disc is always
  render-time + seed-sized, so the fallback is always aspect-correct. (In practice the tapped frame and the
  seed usually share the clip's geometry; the gate makes the exception safe instead of silently warped.)
- **Storage = sealed via the store API, not a raw join.** The segment frame and mask are placed through
  `lucid_store.frame_ref(session, is_private, name)` (**never** a bare `INPUT_DIR` join — the default-
  ergonomic mistake leaks a private frame to the shared `input/` root), and `extract_frame_at` is **always**
  called with the store-owned `out_path`. For a private session both resolve into `.lucid-priv-<s>/`. The
  **transient** segment frame is single-use: **deleted immediately after segmentation** (shrinks the private
  on-disk silhouette to near-zero); the mask is kept (it is the guide). `/api/burn` wipes the sealed dir;
  non-private orphans ride `remove_note` + the `{session}_`-prefix `purge_persistent` sweep (so any kept
  artifact carries the session prefix). An offline test proves: for a private session both the transient
  frame and the mask land under `.lucid-priv-<s>/` and **nothing** session-prefixed appears in `input/` root.

### 5. UI: one tap highlights the object — warm-only, accessible, honest (Chain.tsx)

- Tap the clip → `POST /api/segment {session, node, t, x, y}` — **added to the `do_POST` allowlist and
  passing the same CSRF + Origin check as `/api/note`** (it is state-changing, GPU-touching, disk-writing).
  The backend extracts the frame, runs the segment graph (§2), applies §3, persists, and returns the mask
  **inline in the gated POST response** as a downscaled data-URL with `Cache-Control: no-store` — **not** a
  new unauthenticated `GET` ref (which would inherit the existing unauthenticated frame-serve gap). It logs
  only structural facts (area fraction, accept/reject, timing) — **never frame/mask bytes**.
- **Warm-only, by gate.** `/api/segment` only segments when ComfyUI is **warm** (`CURRENT_TOKEN` live AND
  `/system_stats` OK). When ComfyUI is **cold**, a tap must **not** silently spawn ~17 GB (READY_TIMEOUT
  180 s ≠ "tens of ms"): it **saves the legacy point immediately** and offers *"refine after the first beat
  warms the segmenter."* `canTag` is tightened to include `!busy` (today it omits `busy = dream.isPending
  || committed` and allows tagging the opening still with no clip), and the server refuses `/api/segment`
  during an in-flight admission/spawn. On a live desktop where a UE/Hexen wallpaper squats VRAM (the open
  ADR-0023/0029 graphics-yield-not-in-lease gap), the headroom check degrades the tag to the disc — surfaced
  as a **legible, calm** signal (*"the GPU was busy — saved as a point"*), never a confusing silent
  downgrade.
- **The highlight is figure/ground, not hue.** On a returned mask the overlay draws a **contrasting
  boundary stroke** on the silhouette **plus a desaturating/darkening scrim** on the *unselected* region,
  with the tag conveyed **redundantly by label/icon** (never tint alone — a blue boundary vanishes on a blue
  night scene), targeting WCAG non-text-contrast on worst-case same-hue frames. The `aim-{tag}` CSS the code
  references is actually written. A **keyboard/switch path** exists (focusable crosshair + arrows + Enter,
  and the chip-based frame-wide note stays an equal first-class route) so segmentation is **never the only**
  way to leave a spatial note. An `aria-live="polite"` status narrates the lifecycle (*"finding the
  object…" → "lantern highlighted, confirm to keep" → "couldn't isolate that — placed a point instead"*),
  and the "segmenting…" shimmer is **reduced-motion-safe** (a static dimmed scrim under
  `prefers-reduced-motion`).
- **Ambiguity — single best mask in v1 (on-box finding re-defers the cycle).** The review promoted a
  "free 3-mask scale-cycle" on the premise that the decoder returns all three IoU-ranked scales for free.
  **Confirmed on the box 2026-06-21 this is NOT free with the stock node:** `Sam2Segmentation` *does* call
  `model.predict(multimask_output=True)` but then `out_masks[sorted_ind][0]  # choose only the best result
  for now` (`nodes.py:333`) — it **discards the other two** and returns one mask. So v1 ships the **single
  best mask** (which also honors the original click-only decision); the wrong-granularity recourse is
  **re-tap**. The cycle is a clean **follow-up**: a one-line node patch (return all three sorted by score)
  + a `/api/segment` that emits three refs + a confirm-cycles overlay — still one tap, but it needs that
  node change, so it is **deferred with its exact seam recorded**, not asserted as free. `+/-` refinement
  points and lasso stay deferred (they add encode passes).

### 6. Invariants preserved

- **Kill-switch.** `LUCID_SEGMENT_ENABLED=0` forces the legacy soft-disc path even when notes carry masks
  (fail-safe to pre-0032 behaviour), composing with ADR-0025's `LUCID_LTX_ATTENTION=0` (which drops *all*
  attention back to plain `LTXVAddGuide`).
- **Fail-open is total.** Segmenter/node unavailable, **ComfyUI cold** (→ point, refine later), **headroom
  short / VRAM contended** (→ legible disc), lease/process gone, frame unreadable, empty/degenerate/aspect-
  mismatched mask, or engine ≠ LTX ⇒ soft-disc ⇒ single anchor. No path can break a render.
- **Inside the lease, reversible, local.** Every GPU touch happens inside the lease-held ComfyUI (no second
  tenant, no squat); every kept artifact is a sealed, append-only file; SAM2 runs entirely on the local
  4090.

## Consequences

- **Good.** "Mark up part of the screen" is delivered as the object's own silhouette, not a blob (single
  best mask in v1; re-tap is the granularity recourse, the 3-mask cycle a recorded follow-up). Don't-
  reinvent holds (ComfyUI's own
  SAM2 node + ComfyUI core's attention seam + the existing lease; net-new is a small graph + an image-output
  helper + a schema field + an overlay). Reversibility holds (note = kept, sealed, append-only artifact; a
  flag reverts to the disc). It plugs into the *exact* attention plumbing ADR-0025 already GPU-verified.
- **Cost / risk.** A new ComfyUI custom-node + SAM 2.1-small weights to install (license/hash confirmed by
  §S). The segment graph is graph surgery like the guide injection — mitigated by §3 + fail-open + offline
  structural tests, but the real proof is a GPU render (owed). **Single-tap ambiguity** is mitigated by the
  v1 3-mask cycle, not eliminated. **Cold-start latency** is real and handled by warm-only gating, not
  hidden. On a contended live desktop the tag degrades to the disc until the ADR-0023/0029 graphics-yield-
  into-lease gap closes — surfaced honestly.
- **Privacy surface (routed to the skeptic).** The segmenter *sees rendered dream frames* — local only;
  for private sessions the transient frame (deleted immediately) and the mask seal in `.lucid-priv-<s>/` via
  the store API and burn with the session; the preview rides the gated POST as a `no-store` data-URL, not a
  new unauthenticated GET; nothing logs pixels. A mask is *pixels*, so it passes the §3 deterministic gate
  rather than the B3 red-line text gate — a *different* gate, **not no gate**; the note's free text still
  gates.
- **Deferred / tunable.** `+/-` refinement points and freehand **lasso** (add encode passes); **MobileSAM**
  swap if warm-pool headroom is tight (lighter, Apache-2.0, ~12 ms encode+decode); **EdgeTAM** if the
  feature ever needs temporal/video masks; **SAM 3 text-prompt "segment all instances of X"** as a *separate
  future mode* (its concept power, not its latency — gated license, watch-list only). The §3 constants are
  tunable (their *defaults are shipped*, not deferred).
- **Verification gate §S (the spike that ungates Accepted, restated honestly).** *Owed on the 4090, in
  order:* (a) **on-box segmenter spike (~30 min)** — clone `kijai/ComfyUI-segment-anything-2` +
  `sam2.1_hiera_small`, POST a `load → <seg-node>(hardcoded point) → MaskToImage → SaveImage` graph through
  the net-new image path, and **paste back into this ADR** the exact node class names + every required input
  socket/type, the coordinate JSON shape, the **measured peak VRAM** at lucid's real extracted-frame
  resolution. **This is automated:** `spikes/dreaming/lucid/spike_sam2_segment.py` self-discovers the node
  schema from `/object_info` (it does not trust the web-research names), runs the graph, measures peak VRAM
  with `nvidia-smi`, and prints a paste-ready `§S CAPTURE` block; `spike_sam2_segment_SPEC.md` is the
  run-and-capture checklist. **DONE 2026-06-21:** node installed (`kijai/ComfyUI-segment-anything-2`,
  Apache-2.0) + `sam2.1_hiera_small` auto-downloaded; the spike ran a real point→MASK→SaveImage graph and
  captured the schema above; **`SEG_PEAK_MIB` set to 2048** (measured ~1.3–1.5 GB SAM increment + ADR-0004
  margin; resolves the old 0.2/1–2/2 GB inconsistency to the one measured number). Still TODO by hand:
  record the checkpoint **sha256**;
  (b) a **cheap offline premise check** — rasterize the SAM mask, a bbox-fitted disc, and the legacy disc,
  **downsample each to the actual LTX guide latent grid**, report IoU + active-cell delta, and record a
  numeric **kill line** (*if >40% of real taps fall back to the disc, or the latent-grid IoU delta vs. a
  bbox-disc is within a few cells, cut SAM and ship sized discs*); (c) the **end-to-end render** — the mask
  bites the LTX attention and ADR-0025's seed-keyframe invariant (`model.py`: `total_pre_filter_count ==
  keyframe_grid_mask`) holds. Offline-testable here and not owed: the schema (`mask`+`(w,h)` persist/seal),
  `load_validated_mask` boundary tests, the resolve-or-disc branch, the warm-only/cold-fallback logic, the
  kill-switch.
