# ADR-0045: Anticipatory model residency — pre-warm the models you're about to need

- Status: **Proposed — Phase 1 (Lucid on-open pre-warm) BUILT (uncommitted).** The substrate pattern
  and the policy ladder are decided here so implementation does not drift; the first concrete instance
  (Lucid warms its narrator + VLM when the app is opened) is built and unit-tested (15 tests;
  regressions green: safety 31, linear 43, honest-failure 23, warmkeep 7, drain 32). On-box GPU e2e is
  the user's call. The user steered the intent explicitly: *"if I open the lucid app, the text model and
  the image analysis model should load in anticipation … I want to layer on intelligence to the model
  management."*
- Date: 2026-06-23
- Deciders: Corey (binding product steer — see Status), research synthesis from four investigations
  (pre-warming mechanics across the local-AI runtime stack; predictive-prefetch / app-prelaunch
  literature; the AgentOS lease/queue substrate; the Lucid model dependency graph).
- **Reaffirms (does NOT reopen): ADR-0023 §9** — the rejection of a `Tier::Speculative` lease class.
  That decision (speculative GPU work must never be a competing *lease holder* on a single 24 GB card;
  it rides existing residency, serially, instantly reclaimable) is the load-bearing precedent for this
  ADR. Pre-warming is speculative residency; it obeys the same rule and adds **no new tier**.
- Relates to: ADR-0001 (substrate not orchestrator — pre-warm is a *resource* optimization, not new
  intent), ADR-0002 (don't reinvent — Ollama already does residency/keep-alive; we add only the
  *trigger* and the *policy*), ADR-0003 (fail-open supervised — a warm that doesn't happen is a no-op,
  never a failure), ADR-0010/0006 (the VRAM coordinator + lease — the warm is subordinate to it),
  ADR-0015 §3 (the B1 force-evict before the i2v lease — extended here), ADR-0018 (the warm-pool / small-
  model lane this lives in), ADR-0041 (keep new/speculative logic *out* of the SIGKILL daemon).

---

## 1. Context — the cold-load tax

Every model load in AgentOS today is **lazy/reactive**: the first request that needs a model pays the
full disk→VRAM weight-load latency inline. For Lucid this lands squarely on the moment the user is most
attentive — the opening menu. A fresh open serially cold-loads the VLM (`qwen2.5vl:3b`, ~3.2 GB, for
`/api/openings` + frame grounding) and the narrator (`MN-12B-Mag-Mell-R1`, ~8.7 GB, for the beat menu):
~12 GB off disk before the first suggestion appears. The GPU is otherwise **idle** during that window —
the user is reading, nothing is generating.

The research is unambiguous on what warming buys and costs (see §6 sources):

- **Warming is cheap and compute-free.** A resident-but-unused model holds VRAM but burns **zero** GPU
  cycles. The only standing cost of a warm model is the VRAM it occupies — exactly the resource the
  lease arbiter already rations.
- **Warming weights ≠ warming the prefill.** Pre-loading skips the disk→VRAM load (the big, felt
  latency); the first real request still computes its own prompt. That's fine — the weight load is the
  pain. (KV-prefix priming is a future refinement, §5d.)
- **On one GPU, a wrong guess has a real cost.** Unlike a phone's "wasted joules," a wrong warm here can
  contend with live work. So pre-warming must be **strictly subordinate** to real demand.

## 2. Decision

Add an **anticipatory residency** capability: when a signal predicts a model will be needed *soon*, warm
its weights into VRAM *ahead* of the request — as the **lowest-priority, instantly-reclaimable** thing on
the GPU, never a reservation that can delay or OOM real work.

Two parts, decided together:

**(a) The mechanism — speculative residency, not a new tier.** A pre-warm is a weights-only Ollama
preload (`POST /api/generate` with **no prompt** → weights load, no tokens, no content) pinned with a
**bounded** `keep_alive`. It is safe because every existing guard already binds it:
- The coordinator admits heavy leases against **live NVML free VRAM**, so a warm is *visible as reduced
  free VRAM* — it cannot hide from admission.
- The coordinator's graceful reclaim cold-first `ollama stop`s resident models before any SIGKILL; a
  never-used warm is the natural LRU victim.
- A bounded `keep_alive` (default 4 min, **never `-1`**) self-heals an abandoned warm.
- The generate path force-evicts the beat models **before** the i2v lease (ADR-0015 §3, extended in §5c).

For the **general/ambitious** form (cross-app warming, or an MCP `gpu_prewarm` verb), the warm should
become an explicit `AcquireAgent("best-effort", est)` participant so it passes the `admit()` gate up
front and appears in the keyhole. For **Lucid v1**, the models are small and comfortably fit, and the
four guards above already bound the risk, so the lighter **direct-preload** is sufficient (smallest
version — ADR-0002). Promoting it to a best-effort admission check is the Phase-2 hardening (§5e).

**(b) The intelligence — start deterministic, earn the smarts.** The predictive-prefetch literature is
clear that for a *single user* (tiny, low-rate data), simple beats ML: the explicit app-open event is
the strongest, near-certain signal, and most learned predictors overfit a one-person log. So the policy
ladder is:

1. **Now — explicit triggers.** `app opens → warm its known model set`. A static map; ~zero risk; most
   of the value. (Chrome's "conservative eagerness — act on confirmed intent.")
2. **Later — a small recency/frequency table + time-of-day priors** to decide what to *keep* warm and to
   break ties when one app maps to several models.
3. **Only if an in-app sequence emerges — a first-order Markov table** `P(next_model | last_action)` (a
   dict of counts; no training, no GPU). Stop there — deeper sequence models aren't justified for one
   user.

Every rung feeds the **same gate**: warm iff `P·latency_saved > (1−P)·(wasted_work + contention)` **and**
there's VRAM headroom **and** no real lease is live — with hysteresis and a TTL so a wrong guess decays
instead of squatting.

## 3. Why no new tier (reaffirming ADR-0023 §9)

ADR-0023 already litigated and rejected `Tier::Speculative` on measured VRAM grounds: a second
speculative *holder* on a 24 GB card would either self-preempt real work or need VRAM that physically
isn't there. Pre-warming is the same shape of problem, so it takes the same answer — speculative work is
not a competing lease; it is the lowest-priority residency, reclaimed by the mechanisms that already
exist. This ADR adds a *trigger* and a *policy*, not a new arbitration class.

## 4. Privacy — why pre-warm is NOT gated like glimpses

The speculative **glimpse** renderer (ADR-0023 §9) is off-by-default, opt-in, and hard-off in private
mode because it *generates speculative content* — "rendering paths you won't take, on a box you live on."
A pre-warm generates **nothing**: it loads weights with no prompt, produces no tokens, writes no clip,
leaves no residue. There is nothing to leak. Pre-warm is therefore **default-ON** with a kill-switch
(`LUCID_PREWARM=0`), and needs no private-mode gate. This distinction is deliberate and is the reason the
two speculative features have opposite defaults.

## 5. The Lucid v1 instance (the worked example)

- **Signal (§5a):** the first `GET /api/state` poll after a *gap*. An open tab polls every 2.5–5 s, so a
  gap longer than `LUCID_PREWARM_GAP` (default 90 s) means the app was closed and reopened (or this is
  the first touch). `_maybe_prewarm()` (`lucid_web.py`, in the `/api/state` handler) detects the gap and
  fires `_prewarm_bg()` in a daemon thread so the response never blocks. No new endpoint.
- **What's warmed:** `qwen2.5vl:3b` (VLM/grounder, also the B2 safety check) and `MN-12B-Mag-Mell-R1`
  (the active narrator). `prewarm_models()` (`lucid_engine.py`) de-dups (narrator==VLM → one warm) and
  is wholly **fail-open** — any error is swallowed; a warm that doesn't happen just means the first menu
  pays the cold load it pays today.
- **Gates (§5b):** skip if Ollama is down, skip while a beat is generating (`TURN["phase"]=="dreaming"`),
  and skip any model already resident (`/api/ps`, no redundant reload).
- **The OOM guard (§5c):** the generate path's B1 evict (`lucid_linear._evict_targets`) now evicts
  **both** the VLM and the narrator before the ~17 GB i2v lease — pre-warm can keep the ~8.7 GB narrator
  resident, and 8.7 + 17 GB OOMs a 24 GB card. `OLLAMA_MAX_LOADED_MODELS` is raised 1→2 so the two small
  models co-reside during the dwell (config/ollama.env; honesty bound below).
- **Bounded keep-alive (§5d):** `LUCID_PREWARM_KEEP_ALIVE` default `4m` — long enough for the read-the-
  menu dwell, short enough that an abandoned open frees the VRAM. KV-prefix priming (warm with the real
  system-prompt prefix to also flatten first-token latency) is a noted future refinement, not v1.
- **Phase-2 hardening (§5e):** promote the direct preload to an explicit `AcquireAgent("best-effort")`
  admission check; expose an MCP `gpu_prewarm` verb so any agent/app can warm under the same gate;
  render an "anticipating · warming …" line in the keyhole (the ambient tie-in); add the recency/time
  table then the Markov layer per §2(b) once there are logs to justify them.

## 6. Consequences

- **Honesty bound (ADR-0018 carries):** `OLLAMA_MAX_LOADED_MODELS=2` lets the two *small* beat models
  co-reside (~12 GB). The original "ONE large model" caution still holds — two 17–21 GB models would
  thrash — but Ollama is fit-aware (it evicts under pressure rather than OOMing) and the i2v lease evicts
  both beat models before a heavy load, so the cap never stacks a big LLM on top of the ~17 GB video.
  Operators who do not run Lucid pre-warm can leave it at 1.
- **Failure mode is a no-op.** Every path is fail-open: kill-switch, Ollama-down, already-resident,
  mid-generation, or a warm that errors all degrade to *today's* lazy behavior. Pre-warming can never
  break the loop or wedge the GPU.
- **The pattern generalizes.** Anticipatory residency is a sibling to the lease/queue/keyhole substrate.
  Lucid is the first instance; the §5e seams (best-effort admission, MCP verb, keyhole signal) let it
  serve any future app without re-litigating the safety story.

### Sources (research feeding this decision)
- Pre-warm mechanics: Ollama preload + `keep_alive` semantics (Ollama FAQ / prompt-caching), the
  weights-vs-prefill distinction, zero-idle-compute cost; ComfyUI smart-memory; vLLM/llama.cpp/LM
  Studio/Triton warmup contrast.
- Predictive intelligence: FALCON app-prelaunch (recent-app is the strongest signal; cost-benefit gate
  keeps energy <2%; ~6 s saved); Android App Standby + prefetch jobs; Chrome Speculation Rules
  confidence thresholds; Markov/ARC/readahead families; small-data overfitting evidence (simple beats
  deep for one user).
