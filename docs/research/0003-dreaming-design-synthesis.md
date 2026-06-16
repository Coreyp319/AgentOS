# 0003 — Dreaming (ADR-0008) Design Synthesis

- Date: 2026-06-16
- Mediator: design-discourse-mediator (this agent — neutral facilitator, owns no lane)
- Scope: reconcile the **DESIGN TEAM** review of the uncommitted "dreaming via local
  video" pivot (ADR-0008 + `dreaming/` ComfyUI scaffold + `docs/research/0001`) with the
  **reviewer/rater HOLD** already captured in `docs/research/0002-dreaming-panel-scorecard.md`
  (weighted 4/10, two live caps, recommends ADR-0008 `Accepted → Proposed`).
- Inputs reconciled: the 9 design-team positions below + the panel scorecard (0002) + the two
  **new design spikes** built *during* this review:
  - `spikes/video-wallpaper/` (design-technologist — measured the Surface A renderer on this box)
  - `spikes/dream-as-texture/` (generative-artist — the texture-input counter-proposal)
- Mode: **MIXED, design-only.** `feed.rs`/`main.rs` are shipped code; the entire video path
  (`dreaming/`) is a **proposal with throwaway scaffold**; the VRAM coordinator and apply/rollback
  tx that ADR-0008 leans on are **zero lines of Rust** (`main.rs:16`). Per the panel's rule #2 and
  my own "verify the proposal against the code, not the ADR," nothing here is scored as if the
  coordinator exists. Where a decision lands on the unbuilt substrate I label it **[SUBSTRATE-BLOCKED]**.

---

## 1. Question

ADR-0008 §1 (Decision §30–33) demotes the **proven** shader reactive grammar
(`vision.md:87–131`, `spikes/hills-reactive/`, shipped `feed.rs` producer) to the
**"reduced-motion / fallback renderer"** and makes a `Video` consumer "keyed by `agent.json`
state" the primary dreaming surface. The rating panel escalated the resulting cross-lane conflict
to me (scorecard §5, *Next move 3*): does demoting the shader to a fallback invert the embodiment
vision, and if so, what does ADR-0008 become?

Sub-questions folded in:
- **Shader-vs-video primacy** — and the rewrite of §32–33.
- The design team's **three-way split** on where video belongs: on-demand KRunner only
  (art-director, brand-identity-designer) vs idle-only ambient (design-researcher) vs
  texture-input the shader warps (generative-artist, motion-designer, design-technologist).
- Folding in the two **live caps** the panel found, which **design does not lift**: the
  auto-open consent/agency breach and the empirically-failed `/free` eviction lever.

## 2. Participants

Design team (positions reconciled, by exact name):
**art-director · design-researcher · design-technologist · generative-artist · motion-designer ·
interaction-designer · visual-systems-designer · content-voice-designer · brand-identity-designer ·
sound-designer.**

Reviewer/rater half already captured in 0002 (folded, not re-litigated): the rating-panel
synthesis plus **ux-reviewer, ui-accessibility-reviewer, ambient-embodiment-reviewer,
responsible-ai-privacy-skeptic, resource-safety-reviewer, security-reviewer,
determinism-safety-reviewer, reversibility-tx-reviewer, rust-performance-reviewer,
ai-generation-reviewer, personalization-loop-reviewer.**

Missing voices I will not decide for (named, not filled): **channels-integration-reviewer** (the
KRunner D-Bus surface is a channel; its auth/lifecycle hand-off is unowned here) and
**ai-product-reviewer** (the KRunner generate verb as a product surface). See §8.

---

## 3. Agreements — where the room already converges

This is the striking convergence the brief flagged. Ten design agents and the eleven-reviewer
panel, working independently, land the **same seven points**. I state each once, in the lane that
owns it, and let the others defer (per the shared rule — no double-counting).

1. **The shader grammar is load-bearing, not a fallback. Do NOT demote it.** Owned by
   **ambient-embodiment-reviewer** → **generative-artist** / **motion-designer** (the float→look
   mapping). Asserted by every design agent and by panel item #6. The proven, shipped, continuous
   `{busy,warm,snag}`→look mapping (`feed.rs:78–96` → `aurora.frag`) is the embodiment vision
   (`vision.md:87–131`); calling it "reduced-motion / fallback" inverts the spec. **Unanimous.**

2. **Idle must stay byte-identical to the unmodified baseline.** Owned by **art-director** (the
   calm-tech requirement) with the invariant proven by **generative-artist**
   (`dream-as-texture/README.md:29–36`: all-zero → identity) and **design-technologist**
   (`video-wallpaper/README.md:49–56`: `grade.frag` all-uniforms-0 = passthrough). Idle is ~90% of
   screen-time; reactivity is strictly additive, zero-footprint at rest. Any proposal that makes
   idle *differ* from baseline breaks "calm & honest ambient mapping" **and** the zero-footprint
   property at once — a non-negotiable collision, not a preference.

3. **The continuous mood signal must not be discretized.** Owned by **motion-designer** (the eased
   parameter is the signal; a baked loop bakes one motion-rate into pixels = a step function;
   crossfade *is* the discontinuity, not its cure). Reinforced by **design-researcher** (vision
   science: motion onset/seam transients capture attention above the calm threshold) and resolved
   constructively by both spikes: the floats stay continuous whether they post-grade a video
   (`video-wallpaper`) or warp a dream texture (`dream-as-texture`). What may be discrete is the
   **clip choice**, never the **mood**.

4. **The auto-open is a consent/agency breach and must die.** Owned by **interaction-designer**
   (notification-as-control: Preview / Set-as-wallpaper / Discard) with copy by
   **content-voice-designer**. This is panel cap #1 and is **confirmed in code**:
   `krunner_video_runner.py:64–65` unconditionally `xdg-open "$F"` and, on failure, dumps raw
   stderr into a toast (`else … "Video failed" "$OUT"`), from a global launcher with
   `EnabledByDefault=true`. The whole design team independently flags it; nobody defends it.

5. **NSFW must be imperative-only, hard-walled from every ambient surface.** Owned by
   **brand-identity-designer** ("the environment must always be safe to glance at" = brand
   non-negotiable) with the SFW-only-ambient gate owned by **design-technologist** and the red-line
   consent copy by **content-voice-designer**. Matches responsible-ai-privacy-skeptic and panel cap
   #1. **Unanimous and a tie-break-grade safety floor**, not taste.

6. **A system-owned post-grade is mandatory before any clip plays.** Owned by
   **visual-systems-designer** (a generated mp4 owns its own palette → foreign visual object; the
   system must own the grade as a token — a LUT/subgraph — and warm/snag must be the *same
   parametric grade on both paths, never a prompt word*). This is the mechanism that makes points
   2–3 true on the video path and is exactly what both spikes implement (`grade.frag`,
   `dream_field.frag`). Convergent with the panel's "honest grammar" item #6.

7. **Silence is the design.** Owned by **sound-designer**: wallpaper video plays **muted
   unconditionally** (a hard contract — some models bake audio), with at most two opt-in earcons
   reserved for `needs_you` + `snag`. No dissent; folds cleanly into the muted-by-contract decision.

**Convergence with the reviewers:** the design team and the panel agree on the two cap-removers
(kill auto-open / consent + SFW-ambient wall; make eviction real) and on "don't demote the shader."
The design team *adds* the constructive resolution the panel could only name as an open conflict:
**how** video and the shader coexist without either demotion or discretization.

---

## 4. Tensions — conflict · owner lane · resolution (+ tie-break where used)

### T1 — Shader-vs-video primacy: the headline §32–33 conflict
**Conflict.** ADR-0008 §32–33 makes video primary and the shader a fallback. The entire design team
objects. **art-director** and **brand-identity-designer** formally object to the demotion.
**Owner lane.** Embodiment grammar → **ambient-embodiment-reviewer** consult; **generative-artist** /
**motion-designer** own the float→look mapping; **art-director** owns visual direction.
**Resolution — RESOLVED, against the ADR.** This resolves on a **non-negotiable**, not on taste:
*calm & honest ambient mapping* + *reversible/zero-footprint at idle*. The shader is the only
renderer proven to (a) sit below the attention-capture threshold continuously (design-researcher's
evidence) and (b) be byte-identical at idle (proven in both spikes). Video as the *primary,
state-keyed* surface satisfies neither. **The shader grammar is the primary dreaming renderer,
permanently. §32–33 is reversed: the shader is not the fallback — it is the spine.** This is not a
matter of preference; a decision that wins on "video looks richer" but loses on the calm/honest
mapping is a drift, and drift needs the ADR re-proposed (CLAUDE.md: changing behavior → re-propose,
don't silently drift).

### T2 — WHERE video belongs: the three-way design split
**Conflict.** With the shader restored as spine, the team splits on video's role:
- **(a) On-demand KRunner only** — art-director ("relocate to Surface B"), brand-identity-designer.
  Video's strengths (photographic richness/novelty) are *anti-requirements* for a calm always-on
  layer but *assets* for an explicit, user-invoked surface.
- **(b) Idle-only ambient, never keyed to `agent.json`** — design-researcher (a clip is fine as
  affective ambience *if it never carries state*; state stays the shader's job).
- **(c) Texture-input the shader warps** — generative-artist, motion-designer, design-technologist
  (the dream is a `sampler2D` the floats domain-warp via the existing grammar; the clip is the
  substrate, not the output).
**Owner lane.** Cross-lane — visual direction (art-director) × grammar (generative-artist/
motion-designer) × feasibility (design-technologist) × evidence (design-researcher).
**Resolution — STAGED, not flattened.** These three are **not mutually exclusive; they are three
surfaces with different ownership and different risk**, and forcing one "consensus look" would
repeat my worst pitfall (flattening a real conflict to ship). They resolve by **separation of
surface**, which every camp can sign:
- **Surface B (KRunner, on-demand)** takes camp (a) wholesale — this is the *only* place video is the
  deliverable, behind explicit invocation, preview, and consent. The panel already rates this seam
  sound (scorecard Axis C). **Ship-track.**
- **Surface A (ambient)** resolves to camp (c) as the design target, with camp (b) as its
  **degenerate, must-work-first case**. The `dream-as-texture` spike proves (c) collapses to (b)
  for free: with `busy=warm=snag=0` the procedural layer is pass-through, so "dream played
  untouched at idle" (b) is literally the all-zero state of (c) (`dream-as-texture/README.md:29–36`).
  So we do not choose between (b) and (c) — **(c) contains (b)**. Camp (a)'s objection ("video has
  no zero") is answered: under (c) the *shader* owns the zero and the dream is a static field at
  idle, not a playing loop.
**Why this is the synthesis and not a fudge:** (c) is the only framing that simultaneously keeps the
shader primary (T1), keeps the floats continuous (Agreement 3), keeps idle byte-identical
(Agreement 2), and still lets video earn its place (representational richness, which generative-artist
concedes is video's one genuine win). art-director/brand get their hard wall (video-as-deliverable
lives only on Surface B); design-researcher gets state kept off the clip; the texture camp gets the
warp grammar. **One staged decision, no camp overruled.**

### T3 — Is the Surface A renderer even buildable as a shader-primary thing?
**Conflict.** If the shader is primary, can a *generated video* participate at all without the fragile
NVIDIA HW-decode path the box is known to break?
**Owner lane.** Feasibility → **design-technologist** (ran `spikes/video-wallpaper/`),
**resource-safety-reviewer** / **rust-performance-reviewer** consult.
**Resolution — RESOLVED with measured evidence.** `video-wallpaper/README.md:31–56`: HW decode via
VA-API is **broken** on this NVIDIA stack (`EGL_BAD_MATCH`) — **and irrelevant**, because *software*
H.264 decode runs **4K @ ~400fps / ~13× realtime headroom** on this box, keeping decode off the
contended NVDEC/VRAM path entirely (NVDEC, when engaged, is ~397 MiB/stream — bounded, small vs the
~1.5 GB RT eviction lever). The `VideoOutput → ShaderEffect` grade seam holds and stays
idle-byte-identical. So the renderer for either (b) or (c) is buildable as **"SW-decode the clip +
grade/warp it with a ShaderEffect."** **Caveat recorded:** one unproven gap remains — the **seamless
loop seam** (does `MediaPlayer.Infinite` re-arm with zero black frame) needs a single on-session
eyeball run, not a re-architecture. This is a *de-risked spike result*, not built crate code — I log
it as such (the risk is largely retired; the implementation is unwritten).

### T4 — The eviction lever: `/free` does not work (and design does NOT lift this)
**Conflict.** ADR-0008 §4 makes "dreaming is evictable, inference always wins" true via `POST /free`.
The panel **empirically disproved** it: at idle, VRAM 21540 → 21571 MiB (no drop); ComfyUI kept
17110 MiB. The design team's spikes explicitly **do not** claim to fix this
(`dream-as-texture/README.md:44–48`: "does not solve VRAM eviction — that's the substrate's job").
**Owner lane.** Resource safety → **resource-safety-reviewer** + **design-technologist** (design),
**rust-performance-reviewer** consult.
**Resolution — UNRESOLVED in code; the design is owed; [SUBSTRATE-BLOCKED] for the running form.**
This is a **tie-break by non-negotiable** (*fail-open supervised, ADR-0003* + the substrate's whole
reason to exist, ADR-0001): a safety substrate may not ship a path whose safety mechanism provably
does not work and can OOM the desktop under inference. The correct lever is **agentosd owning the
ComfyUI PID with admission-control (predict-before-load) + SIGKILL release**, with `/free` demoted to
a best-effort hint. But the VRAM coordinator is **zero lines of Rust** (`main.rs:16`), so this round
can only land the *design*, not the running coordinator. **The texture framing (T2c) materially
lowers the stakes** (generative-artist's point): a single short cached clip held as one texture is far
cheaper to hold than a per-state library of loops, and the procedural layer degrades to pure-shader
(no `dreamTex`) for free — but "cheaper" is not "evictable," and the eviction design is still owed.

### T5 — The `acting` state (3) the video mapping would key on does not exist
**Conflict.** Any "clip keyed by `agent.json` state" mapping that includes an `acting` look has no
producer. **Confirmed in code:** `derive_feed` (`feed.rs:78–96`) emits only states **0/1/2/4** —
**never state 3**, though `state_word` defines it (`feed.rs:185–193`).
**Owner lane.** Producer mapping → **design-technologist** (owns `derive_feed`);
**wayland-computeruse-reviewer** consult (the actuation path that *would* emit `acting` is the
unbuilt computer-use backend, `vision.md:41–49`).
**Resolution — RESOLVED by the T1 reversal, with one open dependency.** Reversing §32–33 (no clip
keyed by state) removes the dependency entirely on Surface A — the shader carries state, and it
already correctly treats `acting` as a no-op (`vision.md:96`). I record the residual as an **open
dependency on the actuation path**, not a resolved design point: if any future surface wants an
`acting` look it must wait on `derive_feed` emitting state 3, which waits on the computer-use backend.
Named owners to close it: **design-technologist** + **wayland-computeruse-reviewer**.

### T6 — The word "dreaming" — ambient vs the KRunner verb
**Conflict.** ADR-0008 and the runner both label the on-demand generation "dream"-ish; the runner UI
says "Generate video."
**Owner lane.** Naming/voice → **content-voice-designer**; identity → **brand-identity-designer**.
**Resolution — RESOLVED, agreed across both lanes.** "Dreaming" names the agent's **resting face /
ambient layer** (brand: "the agent's resting face") and is **kept there**; the KRunner verb is
**"generate / make"**, not "dream." content-voice-designer's rewritten notification set and the
failure copy (replace the raw-traceback dump with "Couldn't finish that video / Nothing changed on
your desktop / Try again") land as the Surface B copy. The missing first-run consent copy (names NSFW
+ the red line plainly) is adopted. No tie-break needed; the two owning lanes already agree.

### T7 — The dreaming glyph
**Conflict.** Surface B currently borrows the generic `video-x-generic` icon
(`krunner_video_runner.py:50,89`). visual-systems-designer: dreaming needs its **own ghost/shimmer
glyph** consistent with the system visual language (`vision.md:78`: *ghost+shimmer = a
proposal/simulation*).
**Owner lane.** **visual-systems-designer** (token/glyph system).
**Resolution — RESOLVED, low-stakes, deferred to brief.** Adopt a system-owned ghost/shimmer glyph;
tracked as a craft item, not a gate. Defers cleanly.

---

## 5. Decision — the recommended direction, concrete

**The shader reactive grammar is the permanent spine of dreaming. Video is admitted on two
separated surfaces, never as the primary state-carrier.** Concretely:

1. **Reverse ADR-0008 §32–33.** The shader is the **primary** dreaming renderer; it is *not* a
   fallback. State is carried by the proven continuous `{busy,warm,snag}`→look grammar only.

2. **Surface A (ambient) = dream-as-texture (T2c), with idle-only (T2b) as its must-pass case.**
   The dream is a **`sampler2D` the floats warp** via the existing grammar (generative-artist /
   motion-designer / design-technologist own it), rendered as **SW-decode + ShaderEffect** (measured
   feasible). **Idle = the procedural layer is pass-through → dream as a static field, or nothing.**
   Mandatory **system-owned post-grade** (visual-systems-designer): warm/snag are the *same
   parametric grade on both the shader and video paths, never a prompt word*. Video is **muted by
   contract** (sound-designer). **SFW-only on this surface, hard-walled (architecturally) from any
   NSFW path** (brand-identity-designer / design-technologist).

3. **Surface B (KRunner) = on-demand video, the only place video is the deliverable.** Verb is
   **"generate / make"** (content-voice-designer). Replace the auto-open with
   **notification-as-control** (Preview / Set-as-wallpaper / Discard — interaction-designer); consent
   is a **KRunner Action**, not the Enter key; progress/cancel surfaced. NSFW is **imperative-only**
   here and never reachable from any ambient surface. First-run consent copy names NSFW + the red
   line plainly (content-voice-designer).

4. **Eviction design is owed before any clip is held under inference** (T4): agentosd owns the
   ComfyUI PID, admission-control + SIGKILL release, `/free` demoted to a hint. **Design this round;
   the running coordinator is [SUBSTRATE-BLOCKED] on `main.rs:16`.**

5. **No state-3 (`acting`) dependency** anywhere on the dreaming path until `derive_feed` emits it
   (T5).

### Sequenced path
- **Gate 0 — Cap removers (block everything; design+code where code exists):**
  1. Kill the auto-open; opt-in (`EnabledByDefault=false`); notification-as-control + consent + cancel;
     SFW-only ambient; fail-closed red-line guard; replace the raw-traceback toast.
     *(interaction-designer + design-technologist + content-voice-designer; closes panel cap #1.)*
  2. Eviction **design** (PID-ownership + admission-control + SIGKILL); re-run the `/free` test to
     confirm SIGKILL reclaims. *(design-technologist; resource-safety-reviewer consult; closes panel
     cap #2 as a design.)*
- **Gate 1 — Honest grammar + safety correctness (design now):**
  3. Land §32–33 reversal in a re-proposed ADR; pin the float→texture grammar (dream-as-texture) and
     the mandatory system-owned post-grade as the Surface A contract.
  4. Surface B safety: argv exec (not `bash -lc`), bound/authenticate `Run()`, bind artifact to
     `prompt_id`, validate path. *(design-technologist; security- + determinism-safety-reviewers.)*
- **Gate 2 — Prove the renderer:**
  5. One on-session run of `spikes/video-wallpaper/probe.qml` to close the **loop-seam** gap (the only
     unproven renderer bit). *(design-technologist.)*
- **Gate 3 — Craft/contract:**
  6. System-owned ghost/shimmer glyph; versioned schema for the `agent.json` contract (now two
     consumers — feed→shader and feed→texture); apply/restore install pair; test `ui_to_api`.
     *(visual-systems-designer + design-technologist; reversibility-tx-reviewer consult on the contract.)*

## 6. Accepted tradeoffs

- **Video never carries agent state on the ambient surface.** We give up the "richer, photographic
  mood that changes with the fleet" framing of ADR-0008 §1. Knowingly: design-researcher's evidence
  and motion-designer's step-function argument say a state-keyed loop captures attention and bakes a
  fixed motion-rate; the calm/honest mapping is worth more than the richness on the always-on layer.
- **Surface A ships later than Surface B.** The texture path needs the post-grade contract, the
  loop-seam run, and (for any held clip under load) the eviction design. Surface B's D-Bus seam is
  sound today and can lead — but only *after* Gate 0. We accept a staggered ship.
- **The headline VRAM guarantee remains a proposal.** We accept that ADR-0008's load-bearing
  eviction is [SUBSTRATE-BLOCKED] and ship the *design*, not the running coordinator. The human
  plans against a longer horizon than the ADR's current "Accepted" implies.
- **We hold one short cached clip in VRAM as a texture, not a per-state library.** Cheaper, but not
  free, and not yet evictable — accepted only behind the Gate-0 eviction design.

## 7. Recorded dissent (never erased)

- **art-director** and **brand-identity-designer** want video confined to Surface B **only** (camp a),
  and may read the Surface A texture path (camp c) as still letting video too close to the ambient
  layer. The synthesis answers their core objection (video-as-deliverable is Surface-B-only; the
  shader owns the zero; NSFW is hard-walled) — but **the dissent stands** that *any* generated pixels
  on the ambient surface is a risk to "always safe to glance at." Mitigation: the SFW-only
  architectural wall + system-owned grade. If the loop-seam run (Gate 2) shows *any* visible onset
  transient, their position becomes the fallback: Surface A reverts to pure procedural and video stays
  on B. **This dissent is the tripwire, recorded as such.**
- **design-researcher** would keep video strictly idle-only (camp b) and may regard the *warping* of a
  dream texture as still introducing managed motion. The synthesis treats (b) as the contained case of
  (c); if measurement shows the warp itself crosses the capture threshold, design-researcher's stricter
  line governs.
- No dissent on the cap-removers, the muted contract, the NSFW wall, or the §32–33 reversal —
  those are unanimous.

## 8. Open questions for the human (options + cost + recommendation)

**Q1 — ADR-0008 status.** *Recommendation below in §9.* Surfaced here because it is the one decision
the room cannot make (model proposes, code disposes): only you set ADR status.

**Q2 — Does Surface A (ambient video texture) earn its complexity at all, or do we ship
shader-only ambient and confine ALL video to Surface B?**
- *Option A (synthesis as written):* ambient = dream-as-texture, on-demand = Surface B.
  Cost: the post-grade contract + loop-seam run + holding one clip under the (unbuilt) eviction
  design. Gain: representational richness on the always-on layer, the one thing video genuinely wins.
- *Option B (art-director/brand fallback):* ambient stays **pure procedural** (today's proven path);
  video lives **only** on Surface B. Cost: ambient never gets photographic dream content; we ship the
  smaller, safer thing. Gain: removes the entire T3/T4 risk surface from the always-on layer and
  honors "always safe to glance at" maximally.
- **My recommendation: build Surface B first (Option B's posture) and treat Surface A as a *gated
  experiment* behind the loop-seam run (Gate 2).** This costs nothing extra now (Surface B leads
  either way) and lets the measurement — not taste — decide whether ambient video crosses the calm
  threshold. If Gate 2 shows a visible seam, we have already shipped the valuable, safe half.

**Q3 — Missing voices.** The KRunner D-Bus surface is an integration channel with no owner present;
**channels-integration-reviewer** should review its bus-name/auth/lifecycle, and **ai-product-reviewer**
the "generate" verb as a product surface, before Surface B ships. I will not decide their lanes.
Recommendation: route both at Gate 1.

---

## 9. ADR-0008 status recommendation

**Recommend: `Accepted` → `Proposed`, and supersede §1/§32–33 in the re-proposal.** This *aligns
with* the panel's recommendation (scorecard §6) and the design team's near-unanimous objection, now
on **three independent grounds**, any one sufficient:

1. **A core decision is empirically false (§4).** `/free` does not reclaim VRAM (live test); the
   load-bearing eviction mechanism is disproven. An ADR cannot stand `Accepted` on a disproven lever.
2. **A core decision inverts the embodiment vision (§32–33).** Demoting the *proven* shader grammar
   to "fallback" contradicts `vision.md:87–131` and is rejected by the whole design team on a
   non-negotiable (calm & honest ambient mapping). Per CLAUDE.md, a behavior change must be
   re-proposed, not silently drifted.
3. **The default posture breaches consent/agency (§6).** The red line has zero enforcement while the
   autonomous surface (`EnabledByDefault=true` runner with unconditional `xdg-open`) is the default.

**What survives the re-proposal (keep the intent):** the *direction* is sound and should carry over —
**3D as the long-term north star; video as the pragmatic-now medium; one ComfyUI backend / two
surfaces; dreaming-as-cached-artifact outside the tx.** Re-issue ADR-0008 as `Proposed` with the
amended decisions:
- **§32–33 reversed:** shader grammar is the primary, permanent dreaming renderer; it is not a
  fallback.
- **New §:** Surface A = dream-as-texture (shader warps a `sampler2D`), SW-decode + ShaderEffect,
  idle byte-identical, mandatory system-owned post-grade, muted by contract, SFW-only + NSFW
  architecturally walled.
- **New §:** Surface B = on-demand "generate" verb with notification-as-control, consent as a KRunner
  Action, opt-in default, NSFW imperative-only.
- **§4 amended:** real eviction = agentosd owns the ComfyUI PID + admission-control + SIGKILL;
  `/free` demoted to a hint. **Mark [SUBSTRATE-BLOCKED]:** ADR-0008's guarantees depend on the
  **unbuilt VRAM coordinator (`main.rs:16`) and apply/rollback tx (ADR-0005)** — until those exist in
  Rust, ADR-0008 is a proposal on top of a proposal, and its status text must say so.

**This is a design synthesis — I propose; code and the human dispose.** The ADR status flip itself is
Q1, escalated above.

---

## Artifacts
- This synthesis: `docs/research/0003-dreaming-design-synthesis.md`
- Upstream reconciled: `docs/research/0002-dreaming-panel-scorecard.md` (reviewer/rater HOLD)
- Evidence relied on: `spikes/video-wallpaper/README.md`, `spikes/dream-as-texture/README.md`,
  `crates/agentosd/src/feed.rs` (derive_feed 78–96; state_word 185–193),
  `dreaming/krunner_video_runner.py` (auto-open 64–65), `docs/vision.md` (87–131).
- Next artifact owed (not written here — proposes a behavior change, so the human disposes): the
  **re-proposed ADR-0008** carrying the amended §§ above. Drafting it is Gate-1 item 3.
