"""LeaseState — the deterministic, I/O-free core of the GPU-coordinator plugin.

The plugin holds the agentosd interactive VRAM lease while ≥1 interactive inference is
in flight and releases it when the last finishes (a process-wide refcount), so a busy
Hermes never serializes its own concurrent calls yet still preempts the overnight dream.
This module is the *functional core*: it owns the refcount/token/epoch bookkeeping and
decides what D-Bus action the impure shell must take. It performs NO subprocess/D-Bus
I/O and starts NO threads — the shell (``coordinator.py``) does that, off the lock.

Why a state machine and not just ``acquire(); ...; release()``: the lease is exclusive
(single token) and ``Acquire`` is non-blocking, while Hermes runs many concurrent
inferences. Three races the panel (docs/research/0007) demanded we close, all encoded
here as transitions:
  * M3 lost-update — an Acquire started for one "busy generation" must not store its
    token if the refcount hit 0 and came back since (tracked by ``epoch``).
  * M1 self-heal — a Renew/Release that the daemon rejects mid-hold (TTL expiry or a
    daemon restart) clears the token so the shell re-acquires (``renew_failed``).
  * M4 balance/total — every ``enter`` is paired with one ``exit``; ``exit`` floors at 0;
    the token is released exactly once by the last leaver.

The invariant the shell maintains around this core: *if refcount > 0 then the token is
held, OR an Acquire is in flight, OR (fail-open) the daemon is unreachable.*
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AcquirePlan:
    """The shell must perform an Acquire for this generation (do it OFF the lock)."""
    epoch: int


@dataclass(frozen=True)
class CommitOutcome:
    """What the shell does after an Acquire returns and is committed under the lock."""
    stored: bool                     # token is now the live holder
    start_renewer: bool              # ensure the lifetime renewer is awake for it
    fail_open: bool                  # count a fail-open event (not granted)
    release_stale: Optional[int]     # a token the shell must Release (abandoned/duplicate)
    pump: bool                       # try another Acquire now (lost-update recovery; daemon is up)


@dataclass(frozen=True)
class ExitOutcome:
    release: Optional[int]           # token the last leaver must Release


class LeaseState:
    def __init__(self) -> None:
        self.refcount: int = 0
        self.token: Optional[int] = None
        self.epoch: int = 0          # bumped each time refcount falls to 0 (generation boundary)
        self.acquiring: bool = False  # an Acquire is in flight (≤1 at a time)

    # -- the "should I start an Acquire?" kicker, shared by enter() and the pump loop --
    def _maybe_begin_acquire(self) -> Optional[AcquirePlan]:
        if self.refcount > 0 and self.token is None and not self.acquiring:
            self.acquiring = True
            return AcquirePlan(epoch=self.epoch)
        return None

    def begin_acquire_if_needed(self) -> Optional[AcquirePlan]:
        """Pump entry point: the shell calls this after a commit/renew-failure asked to
        re-establish the hold. Returns a plan (and marks acquiring) or None."""
        return self._maybe_begin_acquire()

    def enter(self) -> Optional[AcquirePlan]:
        """A call entered the inference path. Always increments the refcount; returns an
        AcquirePlan iff this is the 0→1 transition that must seed the lease."""
        self.refcount += 1
        return self._maybe_begin_acquire()

    def commit_acquire(self, epoch: int, granted: bool, token: Optional[int]) -> CommitOutcome:
        """Fold an Acquire result back in under the lock. ``epoch`` is the generation the
        Acquire was started for (from the AcquirePlan)."""
        self.acquiring = False

        if not granted or token is None:
            # Unreachable / queued / denied → fail open. Do NOT spin; the next 0→1 retries.
            return CommitOutcome(False, False, True, None, False)

        if epoch != self.epoch:
            # The refcount hit 0 (and maybe came back) while we were acquiring: this token
            # belongs to an abandoned generation. Release it. If calls are active again,
            # pump to re-establish (the daemon is clearly reachable — it just granted).
            pump = self.refcount > 0 and self.token is None and not self.acquiring
            return CommitOutcome(False, False, False, token, pump)

        if self.token is not None:
            # Already holding a token for this generation (shouldn't happen under the
            # single-in-flight guard) — release the duplicate, keep the incumbent.
            return CommitOutcome(False, False, False, token, False)

        # Same generation, no incumbent → store it. (epoch unchanged ⇒ refcount never hit
        # 0 ⇒ refcount ≥ 1 here, so this is a live, wanted hold.)
        self.token = token
        return CommitOutcome(True, True, False, None, False)

    def exit(self) -> ExitOutcome:
        """A call left the inference path. Decrements (floored). On the 1→0 transition,
        closes the generation (bumps epoch) and releases the token iff one is held."""
        if self.refcount > 0:
            self.refcount -= 1
        if self.refcount == 0:
            self.epoch += 1
            if self.token is not None:
                tok, self.token = self.token, None
                return ExitOutcome(release=tok)
        return ExitOutcome(release=None)

    def renew_failed(self, token: int) -> bool:
        """M1 self-heal. The daemon rejected a Renew/Release for ``token`` while we still
        thought we held it (TTL expiry or a daemon restart reissued tokens). Clear it so
        the shell re-acquires. Returns whether the shell should pump now."""
        if self.token == token:
            self.token = None
            return self.refcount > 0 and not self.acquiring
        return False
