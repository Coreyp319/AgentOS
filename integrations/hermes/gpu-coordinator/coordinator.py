"""LeaseCoordinator — the impure driver around the LeaseState core.

This is the thin imperative shell: it owns the `threading.Lock`, performs the busctl
I/O *off* that lock (panel M2), runs a single lifetime renewer that self-heals on token
loss (M1/M5), counts fail-open events as the silent-degradation canary (M8), and presents
one method — `wrap(next_call, request)` — for the `llm_execution` middleware to call.

Concurrency model (verified by determinism-safety, docs/research/0007): `on_llm_execution`
runs on plain OS threads (gateway workers + subagent ThreadPoolExecutor), never the event
loop — so a `threading.Lock` is correct and nothing awaits across it. The lock guards ONLY
the LeaseState transitions; every Acquire/Release/Renew subprocess runs unlocked.

The driver is *total*: `_enter`/`_exit` never raise, so a sick coordinator can only fail
to *serialize* the dream lane — it can never block or break a live inference.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Optional

try:  # works both as a Hermes package member and as a standalone module under test
    from . import lease_state as _ls
except ImportError:  # pragma: no cover - test/standalone path
    import lease_state as _ls

logger = logging.getLogger("agentos.gpu_coordinator")

_MAX_PUMP = 8                  # backstop against pathological re-acquire flapping
_FAIL_OPEN_LOG_EVERY = 20      # rate-limit the canary warning


def _env_disabled() -> bool:
    # Read fresh each call so an operator can disable a misbehaving plugin without a restart.
    return os.environ.get("AGENTOS_GPU_COORD_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}


def _is_lazy_stream(result: Any) -> bool:
    """M6: the wrap's correctness depends on next_call returning a fully-realized response
    (Hermes returns a materialized SimpleNamespace, chat_completion_helpers.py:1567). A
    generator/iterator would mean tokens generate AFTER we Release — collapsing the
    preemption guarantee. Detect it so we can warn loudly (never raise)."""
    import collections.abc as _abc
    if isinstance(result, (str, bytes, bytearray, list, tuple, dict, set, frozenset)):
        return False
    return isinstance(result, _abc.Iterator)


class LeaseCoordinator:
    def __init__(
        self,
        client: Any,
        *,
        tier: str = "interactive",
        estimate_mib: int = 1024,
        renew_interval_s: float = 60.0,
    ) -> None:
        self._client = client
        self._tier = tier
        self._est = int(estimate_mib)
        self._renew_interval = float(renew_interval_s)

        self.state = _ls.LeaseState()
        self._lock = threading.Lock()

        self._renewer_lock = threading.Lock()
        self._renewer_started = False
        self._stop = threading.Event()

        self.fail_open_count = 0
        self._lazy_warned = False

    # ----------------------------------------------------------------- hot path
    def wrap(self, next_call: Callable[[Any], Any], request: Any) -> Any:
        """The `llm_execution` middleware body: hold the interactive lease across the
        provider call (refcounted), fail-open on everything."""
        if _env_disabled():
            return next_call(request)
        self._enter()
        try:
            result = next_call(request)
            self._check_realized(result)
            return result
        finally:
            self._exit()

    # ------------------------------------------------------------- enter / exit
    def _enter(self) -> None:
        """Total: increments the refcount and, on the 0→1 edge, kicks an off-lock acquire.
        Never raises — a failure here must not break the inference."""
        with self._lock:
            plan = self.state.enter()
        if plan is not None:
            try:
                self._pump(plan)
            except Exception:  # pragma: no cover - defensive; client is already fail-open
                logger.warning("gpu-coordinator: acquire pump failed; proceeding without lease", exc_info=True)

    def _exit(self) -> None:
        """Total: decrements; the last leaver releases the token off-lock."""
        try:
            with self._lock:
                outcome = self.state.exit()
            if outcome.release is not None:
                ok = self._client.release(outcome.release)
                self._log_event("release", outcome.release, extra=("ok" if ok else "stale"))
        except Exception:  # pragma: no cover - defensive
            logger.warning("gpu-coordinator: exit/release failed", exc_info=True)

    # ------------------------------------------------------------- acquire pump
    def _pump(self, plan: "_ls.AcquirePlan") -> None:
        """Run the off-lock Acquire for `plan`, commit it under the lock, and recover from
        a lost-update (epoch advanced) by re-acquiring — bounded by _MAX_PUMP."""
        iterations = 0
        while plan is not None and iterations < _MAX_PUMP:
            iterations += 1
            granted, token = self._client.acquire(self._tier, self._est)   # OFF-LOCK
            with self._lock:
                outcome = self.state.commit_acquire(plan.epoch, granted, token)

            if outcome.release_stale is not None:
                self._client.release(outcome.release_stale)                # OFF-LOCK
            if outcome.fail_open:
                self._note_fail_open()
            if outcome.stored:
                self._ensure_renewer()
                self._log_event("acquire", token)

            if outcome.pump:
                with self._lock:
                    plan = self.state.begin_acquire_if_needed()
            else:
                plan = None

    # ---------------------------------------------------------------- renewer
    def _ensure_renewer(self) -> None:
        """Start the single lifetime renewer the first time we hold a token. One thread for
        the process — no start/stop races (M5); it reads the live token under the lock each
        tick and stops renewing when there is none."""
        with self._renewer_lock:
            if self._renewer_started:
                return
            self._renewer_started = True
        try:
            t = threading.Thread(target=self._renew_loop, name="gpu-coord-renew", daemon=True)
            t.start()
        except Exception:  # pragma: no cover - thread exhaustion; renew is best-effort
            logger.warning("gpu-coordinator: could not start renewer; relying on next 0→1 to refresh")

    def _renew_loop(self) -> None:
        while not self._stop.wait(self._renew_interval):
            try:
                self._renew_tick()
            except Exception:  # pragma: no cover - defensive; never let the renewer die quietly
                logger.warning("gpu-coordinator: renew tick failed", exc_info=True)

    def _renew_tick(self) -> None:
        """One renewer iteration (factored out so tests can drive it deterministically).
        Renews the live token; on rejection (TTL expiry / daemon restart) self-heals by
        re-acquiring (M1)."""
        if _env_disabled():
            return
        with self._lock:
            tok = self.state.token
        if tok is None:
            return
        ok = self._client.renew(tok)                                       # OFF-LOCK
        if ok:
            return
        # token lost out from under us
        with self._lock:
            pump = self.state.renew_failed(tok)
        self._log_event("renew_failed", tok)
        if pump:
            with self._lock:
                plan = self.state.begin_acquire_if_needed()
            if plan is not None:
                self._pump(plan)

    # ------------------------------------------------------------- observability
    def _note_fail_open(self) -> None:
        self.fail_open_count += 1
        if self.fail_open_count == 1 or self.fail_open_count % _FAIL_OPEN_LOG_EVERY == 0:
            logger.warning(
                "gpu-coordinator: fail-open #%d — coordinator unreachable/denied; inference "
                "proceeded WITHOUT the GPU lease (the dream lane was not preempted). If this "
                "climbs, the plugin is effectively a no-op — check `agentosd lease` and busctl.",
                self.fail_open_count,
            )

    def _check_realized(self, result: Any) -> None:
        if not self._lazy_warned and _is_lazy_stream(result):
            self._lazy_warned = True
            logger.warning(
                "gpu-coordinator: next_call returned a lazy iterator, not a realized response. "
                "The lease is Released in `finally` BEFORE lazy tokens generate, so the dream "
                "lane is no longer preempted mid-stream. The llm_execution wrap assumes a "
                "materialized reply (chat_completion_helpers.py:1567); this Hermes build broke "
                "that assumption — the wrap needs to consume the stream before returning."
            )

    def _log_event(self, kind: str, token: Optional[int], extra: str = "") -> None:
        logger.info("gpu-coordinator: %s token=%s %s", kind, token, extra)

    def stop(self) -> None:  # pragma: no cover - process-lifetime cleanup
        self._stop.set()
