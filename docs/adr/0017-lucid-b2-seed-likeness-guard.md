# ADR-0017: Lucid B2 — the seed-image likeness guard

- Status: Proposed
- Date: 2026-06-16
- Closes: the B2 blocker owed by [ADR-0015](0015-lucid-mvp-linear-chain-through-the-lease.md) and
  flagged by the design-council scorecard [0008](../research/0008-lucid-review-scorecard.md)
  (responsible-ai/privacy, 3/10): *lucid is image-to-video, so a seed photo of a real person
  animates that person — the deepfake/NCII mechanism, and the text red-line never looks at the image.*
- Relates to: ADR-0001 (local-first), ADR-0014/0015 (the red-line is owed at the image vector too),
  ADR-0016 (private mode *contains* but does not *prevent* a real-person dream).

## Context

The text gates (ADR-0015 §B3) guard the *prompt*; the seed *image* was unguarded. A user could
upload a photo of a real, identifiable person and animate them. Deterministic CV face detectors
(opencv/mediapipe) aren't installed on this box, and adding a system dependency unprompted is out
of scope. But the local stack already has **vision-capable models** (Ollama: gemma4, qwen3.6).

## Decision

A seed-image guard runs on **every user-supplied seed** before it can become a dream. **Model
proposes, code disposes:** a local vision model classifies the image; a deterministic, fail-closed
rule in `lucid_b2.dispose()` makes the decision. The model runs a *different task* than the narrator
and grades **external input** (the user's image), not its own output — so it is not grading its own
homework.

Disposition (`lucid_b2.py`, pure + unit-tested):

| model proposes | code disposes |
|---|---|
| `possibly_minor` | **BLOCK — hard, never overridable** (the minor red-line; fail-closed if *unsure*) |
| real photographed person (`has_face ∧ real_person`) | **BLOCK by default**; allowed only with **explicit per-seed consent** ("I am this person / have the right to use this image") |
| drawing / render / abstract / no face | **allow** |
| classify failed / unparseable / model down | **BLOCK** ("can't verify ⇒ refuse") |

Surfaces:
- **Web:** the Start card has a file picker. On upload the bytes are **re-encoded to a clean PNG
  (EXIF/GPS/identity metadata stripped)**, size-capped (20 MB), B2-checked, and — for a real-person
  image — a **consent confirm** appears before it proceeds. A possible-minor verdict is a plain hard
  refusal with no override.
- **CLI:** `lucid_linear.py start … --image X [--consent]`; the guard runs unless
  `LUCID_ALLOW_UNVETTED_SEED=1` (a **test-only** escape for CI without a vision model).
- A **server-generated** abstract opening (no upload) is trusted — there's no real person to vet.
- **Right-click "Create Video from Image"** (added 2026-06-16) — two new public entry surfaces onto
  the i2v path, both routed through one governed launcher (`spikes/dreaming/lucid/create_from_image.py`):
  the **Dolphin ServiceMenu** (right-click an image file → *Create* submenu) and a **browser
  WebExtension** (right-click an image on a web page → native-messaging host). Each is the seed-from-a-
  user-image case B2 exists for — a right-click consents to *trying*, not to who is depicted — so the
  launcher runs B2 **exactly once at the surface** (then `start(_trusted_seed=True)`, mirroring the web
  path), strips EXIF by re-encoding to a clean PNG, surfaces the real-person consent via a `kdialog`
  modal (default Cancel) and a possible-minor verdict as a hard refusal, and **fails open** (calm
  skip) when the coordinator is down. Browser images are the *higher-risk* seed (a stranger's photo
  off the web) — the consent gate is doing real work there. `LUCID_ALLOW_UNVETTED_SEED` stays a
  test-only escape, **never set by the shipped launcher**. `--private` keeps the ADR-0016 guarantees
  (tmpfs, not saved, not on the hub, auto-burned); private mode *contains* a real-person dream but is
  never an excuse to relax this gate.

## Honest residuals / still owed

- B2 now runs a **deterministic CV face detector (opencv Haar, frontal+profile) as the PRIMARY
  gate** (`lucid_facecv.py`, shelled to ComfyUI's venv — no new system dependency), with the VLM as
  the second opinion for real-vs-drawn and age. **A face from EITHER detector requires consent**, so
  a VLM false-negative on a real face is still caught by CV. Residuals: Haar misses some
  angles/occlusions (the VLM backstops), it can over-flag a drawn face (a one-click consent, not a
  hard refusal), and **age remains the VLM's call** (CV can't estimate age) — a dedicated
  age/real-person model is the further hardening. `LUCID_B2_MODEL` / `LUCID_CV_PYTHON` are configurable.
- Age estimation from an image is unreliable; `possibly_minor` is deliberately conservative
  (unsure ⇒ true ⇒ block) but is **not** a guarantee.
- Consent is asserted, not verified — it records the user's claim of right; it cannot prove it.
- Once the NSFW opt-in (ADR-0014 §7) exists, **adult-tone + real person is hard-refused**, not
  consent-waivable for a third party.

## Consequences

- Evidence: `lucid_b2.py` (+ `test_lucid_b2.py`, 17 cases, mocked classifier — no model needed).
  `lucid_linear.start()` is the **single B2 chokepoint** (a `SeedBlocked` exception carries the
  verdict to the surface); `_trusted_seed=True` is reachable only for a server-generated abstract
  opening, never a user image. `lucid_web._decode_seed` strips EXIF and rejects decompression bombs;
  uploads are size/dimension-capped, body-length-capped (413), concurrency-bounded (429), and
  short-circuit when the vision model is down.
- An adversarial responsible-ai + security pass (verdict: initially *bypassable* / 6-of-10) drove
  these fixes: the single-chokepoint refactor (no surface routes around B2), removal of the
  `LUCID_ALLOW_UNVETTED_SEED` kill switch (tests inject `_call=` instead), a fail-closed block on a
  keyless/refusal classifier JSON, the decompression-bomb guard, and bounded upload concurrency.
- B2 was the last safety blocker on the lucid scorecard, but it stays **Proposed**: the load-bearing
  residual — a single probabilistic VLM call can **false-negative a real face**, and age estimation
  is weak — is not closed until the **owed deterministic CV face detector** becomes the primary gate
  with the VLM as a second opinion. The right-click "Create from image" launcher
  (`create_from_image.py`, a parallel effort) drives this same `lucid_linear` chokepoint.
