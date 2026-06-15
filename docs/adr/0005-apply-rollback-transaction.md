# ADR-0005: Apply/rollback as a hybrid transaction API in the daemon

- Status: Accepted
- Date: 2026-06-15

## Context
Hermes guides behavior via prompts and blocks dangerous commands via Tirith, but offers
no deterministic apply/rollback primitive. The Nimbus `ui-audit` agent hand-rolls one
(per-file backup, verify-by-re-read, append-only ledger, earned-autonomy, revert) and it
works well. No btrfs CoW snapshot layer is available on this box (Timeshift runs in rsync
mode), so rollback must be at the file/op level. Generalizing the proven `ui-audit`
pattern into a shared substrate primitive is high-value.

## Decision
`agentosd` exposes a **transaction API**: `tx begin → ops → commit | rollback`.
- **Hybrid rollback:** auto file-backup is the default; ops may additionally register an
  explicit inverse where a file backup can't capture the effect (services, packages,
  live config).
- Append-only **ledger**, **earned-autonomy** staging (ported from `ui-audit`), and a
  **Timeshift checkpoint** before high-risk (package/service) batches as a coarse
  backstop.
- The tx engine lives *inside* `agentosd` → one central ledger, one revert authority,
  cross-agent "what changed today / undo it".

## Consequences
- Agents/skills opt in by calling the tx API (exactly as `ui-audit` shells to its apply
  script today); nothing is forced.
- Two rollback code paths (file-backup + inverse ops) instead of one.
- Provides the correctness floor that lets the inference path fail-open safely (ADR-0003).
