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
- Verification: **logic + graph-injection verified; GPU end-to-end NOT.** Unit suites green
  (`test_lucid_linear` 21, `test_lucid_warmkeep` 6, `test_lucid_engine_10eros` 5); the `LTXVAddGuide`
  splice is proven offline against the real `10eros-i2v.api.json`. Confirming the guides actually
  *land* on a render needs a 4090/ComfyUI run (`verify_guides.py --run`); the whole guide path is
  additive + fail-open so the unverified case degrades to today's single-anchor render, never a break.

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
