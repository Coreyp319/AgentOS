# ADR-0025: Lucid — the dream tree realized (branching takes) + spatial feed-forward annotations

- Status: Proposed
- Date: 2026-06-19
- Fulfills: [ADR-0014](0014-lucid-interactive-branching-dream-loop.md) — its headline claim
  ("Reversibility is native. Every clip is a cached file, so the story is a **tree**: branch any
  beat") that [ADR-0015](0015-lucid-mvp-linear-chain-through-the-lease.md) **deliberately deferred**
  ("build a *linear* chain through the coordinator lease first; **defer branching**"). This ADR turns
  on the deferred half.
- Extends, does not supersede, ADR-0015: the append-only, atomic, private-aware chain and the
  held-per-frame "no reroll" menu are kept verbatim; the chain's existing `parent` pointer is the
  seam the tree grows from, and the held menu generalizes from per-*tip* to per-*node*.
- Relates to: ADR-0001 (substrate **not** orchestrator — the branching beat menu still comes from
  Ollama, the *spatial* decomposition reuses an Ollama vision model, and the keyframe conditioning
  reuses ComfyUI's own `LTXVAddGuide`; we built none of those), ADR-0005 (apply/rollback — a take is
  a kept artifact; branching never overwrites a sibling), ADR-0006/0010/0013 (coordinator lease —
  every generated beat, branched or guided, still runs leased + preemptible), ADR-0009 Surface B
  (on-demand generate; video is texture), [ADR-0017](0017-lucid-b2-seed-likeness-guard.md) +
  red-line gate B3 (annotation **free text feeds the model, so it is gated like any prompt**),
  [ADR-0023](0023-creative-environment-pipeline.md) (the `wan`/`10eros` engine toggle — the LTX lane
  this guide-conditioning rides on).
- Evidence: `spikes/dreaming/lucid/` — backend `lucid_linear.py` (tree fork + per-node menu + note
  persistence + step feed-forward), `lucid_engine.py` (`extract_frame_at`, `decompose_notes`,
  `_inject_ltx_guides`), `lucid_web.py` (`/api/note`, `?node=`, `parent`); frontend
  `web/src/Chain.tsx` (the git-graph dream-tree + tag-a-moment UI) + `api.ts`; tests
  `test_lucid_linear.py` (held-menu, branch, notes, decompose, guides), `test_lucid_engine_10eros.py`;
  harness `verify_guides.py`. Design provenance: the claude.ai/design "Lucid Dream Viewer" DC.
- Verification: **GPU-verified end-to-end (2026-06-19).** A non-private 10eros dream rendered a guided
  beat through the live daemon — the journal logged `LTX guides: injected 2 LTXVAddGuide node(s)
  (fps=25.0)` and the beat completed (a real clip appended to the tree, conditioned on two tagged
  moments). Unit suites green (`test_lucid_linear` 21, `test_lucid_warmkeep` 6,
  `test_lucid_engine_10eros` 5); injection also proven offline via `verify_guides.py`. The GPU run
  found + fixed one real bug: guides must chain on the **pre-concat VIDEO latent**
  (`LTXVImgToVideoInplace`), not the audio+video `NestedTensor` the sampler consumes
  (`LTXVConcatAVLatent`) — which threw `'NestedTensor' object has no attribute 'clone'`.
- Private × 10eros seed path — **fixed + GPU-verified (2026-06-19)**. Was: `ltx.build` basenamed the
  seed, dropping a private session's sealed subdir (`.lucid-priv-<s>/…`) so ComfyUI couldn't find it
  (any private LTX beat failed). Fix: `build` sets the seed `LoadImage` to the input-**relative** path
  (subdir preserved, both private + non-private), and `_inject_ltx_guides` co-locates guide frames in
  that same sealed subdir. Verified: a private 10eros dream rendered a beat **and** a guided beat;
  every frame (seed, hold-anchor, guides, VLM-grounding) stayed sealed (no leak to `input/` root); and
  `/api/burn` wiped the sealed dir empty — guide frames destroyed with the session. Fail-open holds
  throughout (a bad seed/splice → skip, never a crash).

## Context

ADR-0015 shipped the lucid loop as a **line**: each beat forks from `nodes[-1]`, the i2v anchor is the
clip's *last* frame, and the held "what happens next" menu is pinned to the single tip. That was the
correct MVP narrowing of ADR-0014 — but ADR-0014's actual bet was authorship: *the story is a tree you
steer, and reversibility is native because every take is a kept file.* The line gives you undo-by-
re-dream; it does not give you **alternate takes** (fork beat N two ways, keep both) — the feature that
makes the loop feel like directing rather than a slot machine.

Two pushes, in one session, closed the gap:

1. **The tree.** The data model already carried `parent` on every node; nothing branched it. Realizing
   the tree is a UI + a single backend seam (fork from an arbitrary node), not a re-architecture.

2. **Annotations had to feed *spatial* information forward.** A "tag a moment" affordance is worthless
   as a sticky note. The load-bearing requirement: when you tag a moment of the clip you're watching,
   the *frame at that moment* — its actual composition — must steer the next beat. The first cut routed
   tags to text only; that throws the pixels away. The corrected insight (and the reason this ADR
   exists): **ComfyUI is not the limit — the workflow was.** The LTX/10Eros graph already ships
   `LTXVAddGuide`, a keyframe-conditioning node that pins an image at a frame position. Feeding a
   tagged frame straight in is lossless and is the *cleanest* signal; a language round-trip is the
   fallback, not the design.

## Decision

### 1. The chain is a tree; branching is a node fork, reversible by construction

- `step(..., parent_id=None)` forks the new beat from **any** node, not just the tip. `parent_id=None`
  preserves the linear default (every legacy caller unchanged). The new node id is `max(id)+1` over the
  whole tree (collision-free — `parent+1` clashes the moment a node has a second child). A take never
  mutates a sibling: forking only **appends**, so ADR-0014 §3 / ADR-0005 reversibility holds — "your
  other take is kept."
- The content-rating floor and the LLM story context are scoped to the **new beat's branch**
  (`root → parent` ancestry), so a SFW take off a SFW ancestor is not dragged mature by an unrelated
  sibling, and a branch is proposed against its own line.
- The held "no reroll" menu (ADR-0015) generalizes: `beats_for_node(session, node_id)` holds a menu on
  *each* node; `/api/beats?node=` and `/api/dream {parent}` carry the node. Menus stay deterministic
  per frame; scrolling the tree is cheap because every past node already sealed its menu when it was
  the tip.
- The surface is one instrument: a cinematic 9:16 player + a git-graph of the chain — **lit
  checked-out path / dim alternate takes / glowing future branches**, with "what happens next" folded
  in as branches growing from the *selected* beat (continue at the tip, branch from anywhere earlier).

### 2. Spatial feed-forward annotations — frame-as-conditioning first, VLM decomposition as fallback

A note is `{t, tag, text}` (`tag ∈ more|less|hold|change`), pinned to a clip timestamp, persisted on
the node (`/api/note`, append-only alongside `beats`). When the next beat is generated **from that
node**, its notes feed forward, by engine:

- **LTX / 10Eros (the clean signal).** Each tagged moment is screenshotted (`extract_frame_at(clip,t)`)
  and injected as a chained `LTXVAddGuide` keyframe into the graph — the diffusion model conditions on
  the **actual pixels**, no language in the loop. `hold` pins `frame_idx=0` ("continue *from* this
  composition") and also seeds the i2v anchor; `more`/`less`/`change` pin at `round(t·fps)` as
  reference keyframes. The **tag sets the guide strength** (`hold` .9 / `more` .7 / `change` .55 /
  `less` .4), so intent is a magnitude, not a hard cut. Injection is additive and **fail-open**: any
  inability to splice (unexpected graph, copy failure, engine ≠ LTX) silently reverts to the single
  anchor.
- **Wan 2.2 (fallback).** Its i2v graph takes one `start_image`; multi-frame would need an FLF/VACE
  variant (deferred). So for Wan — and for purely semantic intent — the tagged frames + intents go to
  an **image-capable model for task decomposition**: an Ollama vision pass (the `ground_frame` seam)
  *sees* the moments and decomposes them into one grounded i2v prompt.
- **Principle (the one the user drove):** prefer feeding the real frame into the workflow over a
  language round-trip. The VLM decomposition is the safety net, not the primary path.

### 3. Invariants preserved (model proposes, code disposes; fail-open; leased)

- Every model output is gated: the LLM-proposed beats, the VLM-decomposed prompt, **and the note's free
  text** all pass the deterministic red-line gate (B3, ADR-0017) before any GPU work; the LTX guide
  `frame_idx`/`strength` are deterministic + clamped (code disposes).
- Fail-open is total: guides skip → single anchor; VLM unavailable → deterministic text-steering
  suffix; Wan → ignores guides; no notes → byte-identical to ADR-0015. None of it can break a render.
- Generation is unchanged where it matters: still one leased, evict-confirmed, preemptible beat
  (ADR-0006/0010/0013); branching and guides ride *inside* that dance, never around it.

## Consequences

- **Good.** ADR-0014's authorship bet is real: alternate takes, kept and switchable; nudge-by-tagging
  that reaches the model as pixels (LTX) or as a grounded prompt (Wan). The substrate's "don't
  reinvent" line holds — the tree is `parent` pointers, the steer is ComfyUI's own `LTXVAddGuide` and
  Ollama's own vision pass.
- **Cost / risk.** The `LTXVAddGuide` splice is graph surgery on the generation seam — mitigated by
  fail-open + an offline structural check, but the real proof is a GPU render. Per-node menus mean an
  Ollama roll per never-seen frame (bounded; past tips are already sealed). The VLM decomposition adds
  one vision call before the evict/lease (it runs while Ollama is still resident — consistent with the
  existing grounding pass).
- **Deferred / tunable (knobs `verify_guides.py` exists to settle on a real render).** Wan multi-frame
  conditioning (FLF/VACE); the `frame_idx` mapping (a `hold` currently pins frame 0 *and* seeds the
  anchor — benign reinforcement, possibly dedupe); whether `more/less/change` belong as hard keyframes
  vs. text-only; the strength values.
- **Verification gate (restated, honestly).** Everything up to the diffusion step is verified
  (logic + offline graph injection). The end-to-end LTX render with guides is **owed** — the single
  thing this environment cannot run.

## Amendment (2026-06-20): regional attention steering + the verification gate, closed

Two things changed.

**1. The attention path is GPU-verified.** The plain-`LTXVAddGuide` render was already proven (the
verification bullets above, 2026-06-19). This amendment adds a guard those plain guides never trip:
ComfyUI core's seed-keyframe accounting (`comfy/ldm/lightricks/model.py`:
`total_pre_filter_count == keyframe_grid_mask`) activates *only* once an **attention** entry exists. On the
4090 — with the wallpaper yielded (a manual `systemctl --user stop` of the Hexen wallpaper unit; see the
gap below) — a guided 10Eros beat carrying attention entries rendered end-to-end through the lease and the
invariant **holds** with the seed + guides in one chain. Spike:
`spikes/dreaming/lucid/spike_ltx_attention.py` (`--dry-run`/`--run`/`--ab`).

**2. Annotations gain a real *where*.** ADR-0025 shipped "spatial" feed-forward that was really *temporal*
(which moment) + semantic (which tag); a note carried `{t, tag, text}`, no point. The amendment adds an
**optional normalized region `(x, y, r)`** to a note — the pixel the viewer tapped — and upgrades the LTX
guide from `LTXVAddGuide` to **`LTXVAddGuideAdvancedAttention`** (already installed; ComfyUI-LTXVideo
@4f45fd6). Its per-region `attention_mask` is consumed by core as `weights = pixel_mask * attention_strength`
(pixel_mask=1 ⇒ attend-to-the-guide *here*), so a soft-disc mask localizes the steer: *more/hold here, let
it change there*. The tag now drives a second knob, `attention_strength` (`LTX_ATTN_STRENGTH`: hold 1.0 /
more .85 / change .40 / less .25), alongside the unchanged keyframe `strength`. GPU A/B at one seed: the
masked region changed 1.12× the rest — localization confirmed, magnitude is the next tuning knob (the curve
is now *measured*, not untestable).

**Invariants kept.** (a) **All-or-nothing per chain** — core requires every keyframe to carry an attention
entry, so a chain with *any* regional note promotes *all* its guides to attention nodes; region-less ones
become **neutral** (`attention_strength` 1.0, no mask = model no-op), preserving their prior behaviour.
(b) **Byte-identical legacy path** — no note carries a region ⇒ the plain `LTXVAddGuide` chain, unchanged.
(c) **Kill-switch** `LUCID_LTX_ATTENTION=0` forces the legacy path even with regions (fail-safe). (d) Still
LTX-only, additive, fail-open; Wan keeps its VLM-decomposed prompt. (e) Coords are **clamped, never trusted**
(code disposes). Surfaces: `lucid_engine._inject_ltx_guides` (branch + `_ltx_softdisc_mask`),
`lucid_linear.add_note(x,y,r)`, `/api/note`, and a tap-to-point overlay in `Chain.tsx`. Tests:
`test_lucid_engine_10eros` (plain/attention/kill-switch branches) + `test_lucid_linear` (region persist/clamp).

**Surfaced gap (not this ADR's to fix).** The lease *admission* is correct (it DENIED at 16.4 GB free vs
17 GB est, GRANTED after the yield), but the daemon's **graphics-yield is not wired into the lease** — it
auto-reclaims only via `ollama stop`, never the wallpaper/UE. So ComfyUI-under-lease starves whenever a live
graphics holder squats VRAM; the manual `systemctl` stop above stands in for the eviction the coordinator
should perform. That integration is the next substrate step (relates to ADR-0004/0023).

## Amendment (2026-06-21): the fuse-review surface + per-region text — annotations become a *visible, editable* part of the prompt

The user's ask: "ensure the UX/UI of adding annotations to be a part of the overall prompt prior to sending
to start the next segment — think deep on how we represent that information." A 6-perspective design pass
(interaction / content / applied-AI / UX / visual / delight) converged on one finding and one fix.

**The finding.** Annotations *did* feed the prompt — but **invisibly**. At a choice moment Lucid committed
four hidden authors into the rendered prompt: the typed line, the notes (silently rewritten by the VLM
`decompose_notes`, or the deterministic `_steering_suffix` fallback), the persistent **subject** prefix
(`_with_subject`), and a possibly-relocated **anchor** frame — then spent minutes of GPU on a string the user
never saw or could correct. That is the one place "model proposes, code disposes" was broken: the one model
output that actually drives the render was the one output never surfaced or gated *before the user committed*.

**The fix (built + tested, this commit).**

1. **The Shot Card readback ("how Lucid reads this").** When the selected beat carries notes, the compose
   card grows: the notes render as composite rows, and a readback tier shows the **exact prompt the next beat
   will run** (notes decomposed + the subject folded *in*), editable before "Dream it." With no notes none of
   it mounts — the type-and-go fast path is byte-for-byte unchanged.
   - `lucid_linear.fuse_direction(session, parent_id, prompt)` assembles that exact gated string with **no
     ComfyUI lease and no heavy admission** — but it is **not free**: it runs one local VLM pass that **loads
     and evicts** the 3B narrator (`keep_alive:0`, `lucid_engine.py`), the *same residency profile as a menu
     roll* (a transient load/evict, **not** standing residency). The real, unmeasured cost is **load/evict
     thrash under eager debounced re-firing**, bounded by the TURN-phase backpressure below. It returns
     `{ok, fused, subject, source ('decompose'|'suffix'), rows, notes_digest}`; `_collect_note_frames` is
     shared with `step()` so the readback shows the *same* decomposition the render uses. A red-lined model
     fusion falls back to the deterministic suffix and re-gates (the readback is never an ungated string).
   - `POST /api/fuse` (no lease/turn/chain write) serves it, **cached** by `(session, parent, notes_digest,
     prompt)` so reopening — or committing right after — is instant and stable. Fired eagerly during the
     dwell from a debounced copy of the typed line (never per-keystroke). **TURN-phase backpressure** (council
     P1.6): while a beat is generating, `_fuse_cached` returns the deterministic *suffix* reading
     (`allow_model=False`, uncached) so an eager fuse never competes with the in-flight render for the
     narrator slot; the `_FUSE_LOG` hit/miss/backpressured counters + a per-miss wall-time log instrument the
     cost on real typing (the on-box p50/p95 stays Owed).
2. **Edit-wins, gated, staleness-checked.** `/api/dream` accepts an optional `fused_edited` + `notes_digest`.
   When present, `step(..., fused_edited=...)` runs it **verbatim** — no re-decompose, no subject re-prefix
   (so what the user saw is what renders) — while the LTX guides + hold-anchor still derive from the notes
   (a text edit never disables a mask). The edit is re-gated by the red-line (entry + step), and a
   **notes-only** `_notes_digest` refuses an edit reviewed against a since-changed note set. The digest is
   deliberately *prompt-independent* (editing your words can't false-trigger the staleness gate); the fuse
   *cache* keys on the prompt separately so the readback still updates as you type.
3. **The subject author is surfaced, not silently re-added.** `_with_subject` now folds the persistent
   identity *into* the shown/editable text in the `fused_edited` path, closing the hole where a user would
   review one prompt and run another (the prefix used to be re-applied after the edit).
4. **Honest across engines.** `decompose_notes` authors the prompt for *both* engines, so the readback is
   truthful as "the prompt" for both; the only engine difference — surfaced per row, never as plumbing — is
   whether a placed region *also* steers pixels: **10eros** = "steers the picture here" (the LTX attention
   guide), **wan** = "shapes the words" (decomposition only). A `hold` reads "grows from here" on both.
5. **Per-region text (honesty-gated).** A note's text now binds to the region it was placed on: a coarse,
   deterministic location phrase (`_region_phrase`, a 3×3 grid) is threaded into `decompose_notes`
   (`SYS_DECOMPOSE` updated) and into `_steering_suffix`, so "make it brighter" applies to *the upper-left*,
   not the whole frame. Built on the SAM2 segmentation that already exists (ADR-0032). Object **captioning**
   ("the lantern") is deliberately deferred — it needs a caption pass; the on-frame **numbered pin** is the
   precise spatial referent instead, so we never speak an object name the pipeline wasn't told.
6. **The spatial↔textual link is a number.** Notes that carry a place get a 1-based pin ①②③ on the frame,
   echoed as the leading badge of their composite row; hovering either side lights the pair. A number
   survives a same-hue frame, colour-blindness, and a screen reader — unlike a colour.
7. **Vocabulary collapse (labels only; the `tag` enum is unchanged, no migration).** "Tag a moment" → **Note
   this moment**; Hold here/More like this/Less of this/Change this → **Keep this / More of it / Less of it /
   Change it**; "Save tag" → **Save note**; "Notes → next beat" → **Your notes for the next moment**. The
   four directions each gain a grayscale-safe shape glyph (◆ hold / ▲ more / ▼ less / ✎ change).

**Invariants kept.** Fast path untouched (no notes ⇒ no Shot Card, no `/api/fuse`, legacy `step`). Fail-open
total (`/api/fuse` degrades to the deterministic suffix reading on an unreachable narrator OR a red-lined
fusion; a missing/stale reading just sends the bare line and lets `step` fuse). No ComfyUI lease / no heavy
admission on the review path (the one local narrator load/evict is throttled, above). Every string that
reaches the renderer still passes `gate_prompt` — model-authored, deterministic-fallback, or user-edited.
Private unchanged (fuse reads the user's own notes; transient frames seal via the store API).

**Honesty invariants (council 2026-06-21, the iterate-round).** The readback is the value-prop surface, so it
may not over-claim: (a) the `'…steer the picture…'` footer phrasing is gated on a real region/mask note —
frame-wide notes read `'…guide the picture…'`, never a spatial steer that isn't happening; (b) the
deterministic *floor* reading gets a **plain, instant** appearance (no `sc-settle`) — the "Lucid composed
this" settle is reserved for a genuine model fusion, so the gesture *means* authorship; (c) the floor
disclosure is **cause-agnostic** ("a plain reading — kept to your exact notes"), never "offline" (false on
the red-line branch); (d) a stale-against-current-notes edit cannot be committed — "Dream it" is disabled
while the reading recomputes, pre-empting the server notes-digest refusal client-side.

**Silence invariant.** The Shot Card adds **no completion sound or haptic**. The honest completion signal
belongs to the desktop `agent.json` `warm` edge (ambient layer), not this surface — no future contributor
should add a commit `ding`. (Recorded at `sound-designer`'s request; the council scored the silence a 9.)

**Surfaces.** `lucid_linear` (`fuse_direction`, `_collect_note_frames`, `_notes_digest`, `_region_phrase`,
`_steering_suffix` localization, `step(fused_edited=…)`), `lucid_engine` (`decompose_notes` region line +
`SYS_DECOMPOSE`), `lucid_web` (`/api/fuse`, `_fuse_cached`, `/api/dream` edit+staleness gate, `_run_turn`
threading), `web/src/{api.ts,Chain.tsx,theme.css}` (the Shot Card, numbered pins, readback, vocabulary).
**Tests:** `test_lucid_linear` (fuse_direction decompose/suffix/red-line/no-notes, notes-only digest,
`fused_edited` runs-verbatim/skips-subject, region phrase + localized suffix — 39), `test_lucid_web_fuse`
(cache hit/miss + notes-only token — 4); full backend sweep + `tsc`/`vite build` green.

**Council verdict (2026-06-21).** Design discourse mean 8.3; rating panel **7.6 → ITERATE** (target 9), no
hard cap fired. The flagged un-fired AA cap was **measured and clears** (readback ink 12.1:1, `.sc-txt`
muted over the worst bright-spill-plus-warm card 5.3:1, effect label 5.5:1 — all ≥ 4.5; the gutter scrim
keeps the card dark enough). The iterate-round edits (honesty-surface P0, trust/cost P1, token P2) are
**applied this commit**; market-fit's structurally-correct 6 (spike-local UX, touches no substrate moat) and
the headline e2e are the standing gaps. Full brief: `docs/design/0025-lucid-shot-card-FINAL-council-brief.md`.

**Owed.** On Corey's GPU box: the live end-to-end of an edited reading rendering to screen; the on-box
eager-fuse cost number (the `_FUSE_LOG` p50/p95 + load/evict-per-compose count — the seam is instrumented,
the number is GPU-gated). Decision-deferred (carried, not dropped): the engine-toggle-mid-compose honesty
seam (a wan↔10eros flip should re-derive the footer/effect copy); the subject-uncaptured-at-first-beat
consistency test; deterministic-path span provenance; the object-caption follow-up (path A) **only if** the
3×3 location phrase proves too coarse in real use (a wrong object name breaks honest-mapping faster than
honest vagueness — do not rush it). Human-disposed: §9.2 (is deep Lucid-lane design investment serving the
substrate doctrine or scope-drifting — recommend treating the Shot Card as a sanctioned *doctrine demo* of
the reversibility wedge, consult `ai-product-reviewer` before any external use of the 10-second clip).
