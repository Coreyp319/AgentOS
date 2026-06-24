# ADR-0028: Lucid — save & reopen dream sequences, and an encrypted private stash

- Status: Proposed
- Date: 2026-06-19
- Extends: [ADR-0015](0015-lucid-mvp-linear-chain-through-the-lease.md) (the append-only chain),
  [ADR-0025](0025-lucid-dream-tree-and-spatial-feedforward-annotations.md) (the tree). Those made a
  dream *durable as a file*; this ADR makes the **collection** of dreams first-class — a library you
  reopen, not a single hardcoded session that every "start" clobbered.
- **Supersedes the "ephemeral-only" clause of** [ADR-0016](0016-lucid-privacy-ephemeral-private-mode.md):
  ADR-0016 said a private dream is RAM-only and **burned on logout, never persisted**. The user needs
  to *save and reopen a private dream*. This ADR adds an **opt-in, passphrase-locked, encrypted-at-rest
  stash** as the ONLY way a private dream persists — and keeps ADR-0016's live posture intact: a private
  dream is still worked on only as a RAM-backed, sealed, burned-on-logout session. The persistent form
  is sealed ciphertext, never plaintext.
- Relates to: ADR-0001 (local-first; the stash is on-disk, single-user, no network), ADR-0005
  (reversible artifacts — a saved dream is a kept artifact; the stash never overwrites a sibling),
  ADR-0017 + the red-line gate (B2/B3 still gate every seed/prompt; reopening a dream replays kept
  gated content, it does not re-open the gate), ADR-0019 (the durable held queue is orthogonal — it
  defers *creation requests*; this persists *finished dreams*).
- Evidence: `apps/dreaming/lucid/` — new `lucid_crypto.py` (stdlib authenticated encryption),
  `lucid_stash.py` (the encrypted stash), additions to `lucid_store.py` (`list_persistent`,
  `new_session_id`) and `lucid_linear.py` (`start(name=…)`, chain `name`/`created`), the multi-session
  refactor of `lucid_web.py` (mutable current session + `/api/library`, `/api/open`, `/api/rename`,
  `/api/library/thumb`, `/api/stash[/init|unlock|lock|save|open|rename|delete|passphrase]`); frontend
  `web/src/Library.tsx` (split into `DreamGallery` + `StashPanel`) + `api.ts`/`App.tsx`/`Start.tsx`/`components.tsx`/`theme.css`.
  A persistent section **menu** (`components.Nav` + a `view` model with hash routing in `App.tsx`:
  `#/new|library|stash|settings`, the live dream leading by name) ties New / Your dreams / Stash /
  Settings together — replacing the earlier ad-hoc showHome toggle, back-link, and per-screen engine
  disclosure; screenshot-verified against the instrument register. A `ui-accessibility-reviewer` pass
  then hardened the new surfaces (WCAG 2.2 AA): the dream tiles are now a plain container with a real
  `<button class="lib-open">` overlay + a sibling delete (no `div[role=button]` nesting real buttons);
  tile text rides a deepened/opaque scrim + text-shadow so it clears AA over a bright generated frame
  (guarded by a new `test_g7_contrast` row); per-view `<h2>` headings receive focus on section change
  (SR announces the section); passphrase/`Name` inputs carry `aria-label`; delete is touch-discoverable
  (resting, not hover-only) at a ≥24px target; stash errors are `role="alert"`. Tests:
  `test_lucid_crypto.py` (25), `test_lucid_stash.py` (38), `test_lucid_store.py` (+library, 55),
  `test_lucid_web_library.py` (end-to-end HTTP, 25), `test_g7_contrast.py` (11, +scrim guard).
- Verification: **offline-verified end-to-end (2026-06-19).** The whole lucid offline suite is green
  (17 files), including the new four. The HTTP smoke test drives the REAL `lucid_web` Handler: start
  two named dreams (they coexist — no clobber), list/reopen/rename/thumbnail the library, then
  init/unlock/save/lock/(wrong-pass)/unlock/open/delete the encrypted stash. tsc clean; the React
  bundle builds. The one thing this environment cannot do — a GPU beat — is unchanged by this ADR
  (save/reopen is pure storage around the same leased generation path).

## Context

The lucid web app was hardcoded to a single session (`SESSION = "web"`), and every "start a dream"
ran `lucid_store.clear(SESSION)` first — so there was only ever ONE saved dream, and beginning a new
one **destroyed** the old. The "your dream library" was copy with no library behind it; private mode
(ADR-0016) was deliberately unsaveable (burned on logout). The storage layer (`lucid_store`) was
already fully multi-session (every function takes `session`); only the web surface pinned it to one.

Two asks, one feature: **save & reopen any dream**, and **a private stash** — a place to keep private
dreams you come back to. A "stash" implies persistence, which directly contradicts ADR-0016's
burned-on-logout guarantee. Persisting private dreams as plaintext would quietly weaken the strongest
privacy promise in the project. So the stash persists **encrypted**, gated by a passphrase, and the
*live* working copy stays exactly as ephemeral as ADR-0016 demands.

## Decision

### 1. The current dream is mutable; dreams are a named library (no clobber)

- `lucid_web` replaces the constant `SESSION` with a lock-guarded current session (`cur_session()` /
  `set_session()`). Switching always rides `_end_session()` (epoch bump + lease release) so a beat in
  flight for the old dream can neither clobber the new one nor leak its ~17 GB lease; the dream worker
  captures its session at request time and `step()`'s `is_current()` still guards the persist.
- `/api/start` **mints a new session id** (`lucid_store.new_session_id(name)` — a slug of the name +
  random suffix, always `valid_session`) and switches to it **after** start succeeds. It no longer
  clears anything: the previous dream stays in the library. A chain now carries `name` + `created`.
- New surface: `GET /api/library` (metadata rows, path-free), `GET /api/library/thumb?session=`
  (the tip frame, a saved non-private dream only), `POST /api/open {session}` (reopen → switch),
  `POST /api/rename {name}`, and `POST /api/delete {session?}` (any saved dream, or the current one).
- On startup the server reopens the most-recently-edited saved dream (or a clean slate), honoring
  `LUCID_WEB_SESSION` when explicitly set.

### 2. The private stash: encrypted at rest, decrypted into the existing ephemeral sinks

- A stash is opt-in and passphrase-protected. On disk (`~/.local/share/agentos/lucid-stash/`) it holds
  ONLY ciphertext: plaintext `meta.json` (public salt + a check token), an **encrypted index** (so even
  the dream *names* are not on disk in cleartext), and one **encrypted tar per dream** (chain + clips +
  sealed frames). Blob filenames are random ids, never the name.
- `save_session` seals a live private dream into the stash; `open_into` decrypts a stash entry back into
  the **same tmpfs + sealed-input sinks `lucid_store` already manages** (ADR-0016) and rewrites the
  chain's paths so it is self-consistent under the restored session — i.e. while open, it is a normal
  ephemeral private dream. `lock()` drops the key; `reseal_opened()` re-encrypts an open working copy
  back to its blob and burns the tmpfs (the lock/logout path, run in the serving process where the key
  lives). `change_passphrase` re-keys with a fresh salt and re-encrypts the index + every blob.
- The master key lives ONLY in the serving process's memory; it is never written, logged, or passed to
  a subprocess. There is no recovery — a forgotten passphrase means the dreams cannot be opened (stated
  in the UI). Wallpaper/sharing of a private dream remains forbidden (ADR-0016).

### 3. Crypto: stdlib-only authenticated encryption (honest about it)

- This environment has no `cryptography`/`libsodium`/`age`/`nacl` — only the Python stdlib. So
  `lucid_crypto` **composes standard primitives in standard constructions** (it invents no cipher):
  scrypt (memory-hard passphrase KDF, public salt) → master key; per-message keyed-BLAKE2b subkeys;
  keyed-BLAKE2b counter-mode keystream for confidentiality; keyed-BLAKE2b **encrypt-then-MAC** for
  integrity/authenticity (a wrong passphrase or any tampering fails the constant-time tag, so `decrypt`
  raises rather than returning garbage — this is also how an unlock attempt is verified).
- The on-disk format is **versioned** (an 8-byte magic). When a vetted AEAD becomes available
  (libsodium `crypto_secretbox`, or `age`), the format can be migrated. This trade-off is recorded
  openly for the security reviewer: hand-composed-but-standard EtM, chosen because a dependency-free
  spike could not pull in a vetted library.

### 4. Invariants preserved

- **Model proposes, code disposes / fail-open:** unchanged. Save/reopen is storage around the same
  leased, evict-confirmed, preemptible beat. Reopening replays kept gated content; it never re-opens
  the red-line/likeness gates.
- **Private stays private:** the live copy is RAM-only + burned on logout (ADR-0016); the only
  persistent form is sealed ciphertext; names are encrypted; nothing private is wallpaper-eligible.
- **No-clobber reversibility (ADR-0005):** starting/reopening never destroys another dream; delete is
  the only removal and it is explicit + two-step in the UI.

### 5. Honest at-rest residuals (what the encryption does NOT hide)

The stash encrypts dream content AND names (the index is ciphertext, blob filenames are random ids).
But a local reader of the `0700` stash dir — the same user, root, or a backup — can still infer
**metadata** without the passphrase, and that must be on the record rather than implied away by
"names are encrypted":

- **how many** private dreams exist (one `<id>.luciddream` per dream);
- **roughly how big** each is — the stream cipher preserves length (`len(ciphertext) == len(plaintext)`);
- **when** each was last saved (file mtimes are not pinned);
- **that a private stash exists at all** (`meta.json` present; `/api/state` even reports
  `stash.exists` / `stash.unlocked` to a loopback caller).

These are accepted for a single-user local box (ADR-0001); size-bucketing and mtime-pinning are
available hardenings if the posture must tighten. Disclosure here is the contract.

Carried forward from ADR-0016, now **load-bearing** because dreams are deliberately KEPT:

- **swap.** The live working copy is plaintext in tmpfs; on a host with on-disk **unencrypted** swap,
  decrypted pages can reach durable storage. This box uses zram (RAM-compressed, never on disk) so it
  is benign here; on other installs, encrypted/zram swap (or `mlock`) is the precondition for the
  strong "never plaintext on disk" claim.
- **hard SIGKILL.** A `kill -9` of the serving process leaves the sealed-input frames
  (`input/.lucid-priv-*`, `0700`, real disk) until the next-launch `reap_orphans()` / logout tmpfs
  wipe — the same bounded window ADR-0016 already owns, not a widening. Chain + clips live in tmpfs
  and die with the session.

## Consequences

- **Good.** "Save and reopen" is real: many named dreams in a library you switch between, with
  thumbnails; private dreams can be kept across logout **without** weakening the live-private posture,
  because persistence == encryption. The substrate's "don't reinvent" line holds for the *library*
  (the storage layer was already multi-session) and is honestly flagged for the *crypto* (stdlib EtM,
  versioned for migration).
- **Cost / risk.** (a) Hand-composed AEAD — mitigated by standard constructions + a versioned format +
  a migration path; a vetted lib is the upgrade. (b) The master key resides in process memory while
  unlocked (Python can't reliably zero it) — accepted for a single-user local spike. (c) On a hard
  SIGKILL of the serving process an open working copy is not resealed (changes since the last explicit
  save are lost, but the encrypted blob and the on-logout reap keep the posture safe). (d) The library
  `list_persistent` reads N chain.json files — bounded, fetched on demand (not in the polled state).
- **Deferred / owed.** A GPU end-to-end (start → save private → lock → unlock → reopen → continue a
  beat) on the real box; per-dream passphrases (today one passphrase per stash); migrating to a vetted
  AEAD if one becomes available in the runtime. The `responsible-ai-privacy-skeptic` +
  `security-reviewer` gate has now **run** — see the Review section below.

## Review (2026-06-20): security + privacy gate → SHIP-AFTER-FIX (must-fixes applied)

The Proposed→Accepted gate (a `security-reviewer` + `responsible-ai-privacy-skeptic` pass on the
construction and the at-rest threat model) ran. Both returned **SHIP-AFTER-FIX**. Confirmed sound:
the hand-composed scrypt + keyed-BLAKE2b **EtM** (verify-before-decrypt, MAC over `MAGIC‖nonce‖ct`,
constant-time tag, enc/mac key separation, fresh 128-bit random nonce — no reuse path found); the tar
extraction is **traversal-safe** (authenticated blob, `isfile`-only, basename-only, manual write — no
`extractall`); and **ADR-0016's guarantee is preserved, not weakened** (encrypt-before-write verified
end to end; the live copy stays tmpfs + sealed + burned; the master key lives only in process memory,
never logged or passed to a subprocess).

Must-fixes applied (`lucid_stash.py`):

- **`change_passphrase` is now crash-atomic.** New-key blobs + index are staged to `.rekey` sidecars;
  a single atomic `meta.json` write (a `rekey` flag) is the commit point; then sidecars are promoted.
  `_resolve_pending_rekey()` (run at unlock) finishes a committed-but-interrupted rotation or discards
  pre-commit orphans — so the stash is never split across two keys (closes the silent-brick / ADR-0005
  reversibility gap).
- **A present-but-corrupt index fails closed** (`_read_index` lets `BadData` propagate) instead of
  reading as empty and letting the next write orphan every blob.
- Hardening: `_write_atomic` sets `0600` at creation (no world-readable temp window); `open_into`
  allowlists `clips/*` by `valid_name` (matching `frames/*`) and bounds member count/size.
- Tests: `test_lucid_stash.py` 38 → 52 (added crash-after-commit, crash-before-commit, clean-rotation,
  and corrupt-index cases); crypto 25, web-library e2e 25, store 55 all still green.

Deferred (non-blocking): bind the blob id into the MAC as associated data (closes an intra-stash
swap/rollback that is below the single-user threat model — needs a versioned-format migration so it
doesn't strand existing blobs); raise scrypt cost / `mlock` the key if the posture tightens. Still
owed elsewhere: the GPU end-to-end, and confirming `StashPanel` surfaces "no recovery — a forgotten
passphrase means these dreams are gone" at set-passphrase time.
