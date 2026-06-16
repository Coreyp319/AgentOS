"""gpu-coordinator — Hermes plugin (ADR-0006). Holds the agentosd interactive VRAM lease
around inference so live AI preempts the overnight dream (ADR-0010).

It registers ONE middleware: ``llm_execution``. Around every interactive provider call it
holds the exclusive interactive lease on ``org.agentos.Coordinator1`` — refcounted
process-wide, so concurrent Hermes inferences share one lease (never serialized) yet any
running batch/dreaming job (ComfyUI) is preempted the moment inference starts and the lane
reopens when the last call finishes.

Design + review: ``docs/research/0006-*`` (brief) and ``docs/research/0007-*`` (5-reviewer
scorecard). The logic lives in ``lease_state.py`` (pure core) + ``coordinator.py`` (driver)
+ ``lease_client.py`` (hardened busctl transport); this file is just the Hermes wiring.

Fail-open everywhere (ADR-0003): if the coordinator is down/slow/denying, inference simply
proceeds without the lease — a broken plugin can only fail to *serialize* the dream lane,
never block or break a live turn.

Activation: standalone plugin, opt-in via ``hermes plugins enable gpu-coordinator``.
Kill-switch: ``AGENTOS_GPU_COORD_DISABLE=1`` makes the hook a pure passthrough (read fresh
per call). Tuning: ``AGENTOS_GPU_ESTIMATE_MIB`` (default 1024), ``AGENTOS_GPU_RENEW_S``
(default 60), ``AGENTOS_GPU_BUSCTL_TIMEOUT_S`` (default 1.0).
"""
from __future__ import annotations

import logging
import os
import threading

try:  # package member under Hermes; standalone under tests
    from . import coordinator as _coordinator
    from . import lease_client as _lease_client
except ImportError:  # pragma: no cover
    import coordinator as _coordinator
    import lease_client as _lease_client

logger = logging.getLogger("agentos.gpu_coordinator")

_COORD = None
_COORD_LOCK = threading.Lock()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _get_coordinator():
    global _COORD
    if _COORD is not None:
        return _COORD
    with _COORD_LOCK:
        if _COORD is None:
            client = _lease_client.BusctlLeaseClient(
                timeout_s=_env_float("AGENTOS_GPU_BUSCTL_TIMEOUT_S", 1.0),
            )
            _COORD = _coordinator.LeaseCoordinator(
                client,
                tier="interactive",
                estimate_mib=_env_int("AGENTOS_GPU_ESTIMATE_MIB", 1024),
                renew_interval_s=_env_float("AGENTOS_GPU_RENEW_S", 60.0),
            )
    return _COORD


def on_llm_execution(**kwargs):
    """`llm_execution` middleware: wrap the provider call in the interactive lease.

    Calls ``next_call`` EXACTLY ONCE on every path (the single-use contract). A provider
    exception propagates unchanged; only coordinator-init failure is caught — and only
    *before* the call has run — to bypass cleanly.
    """
    next_call = kwargs.get("next_call")
    request = kwargs.get("request") or {}
    if not callable(next_call):
        return request
    try:
        coord = _get_coordinator()
    except Exception:  # pragma: no cover - never block inference on our own init
        logger.warning("gpu-coordinator: init failed; bypassing the lease", exc_info=True)
        return next_call(request)
    return coord.wrap(next_call, request)


# --- v1.1 priority tag — intentionally NOT registered ------------------------------------
# There is no proxy that reads X-GPU-Priority yet: ADR-0002's enforcing proxy is unbuilt,
# the fidelity spike forwards request headers verbatim, and Ollama ignores them. Registering
# this now would ship a no-op that reads as a feature and bakes a false success signal into
# the demo (panel verdict, docs/research/0007). Re-register it in register() the day the
# enforcing proxy lands AND reads the header — that is its own work item.
#
# def on_llm_request(**kwargs):
#     request = kwargs.get("request") or {}
#     headers = dict(request.get("extra_headers") or {})
#     headers["X-GPU-Priority"] = "interactive"   # best-effort; never preemptive (Ollama is FIFO)
#     return {"request": {**request, "extra_headers": headers}}


def register(ctx) -> None:
    ctx.register_middleware("llm_execution", on_llm_execution)
    logger.info(
        "gpu-coordinator: llm_execution middleware registered — interactive VRAM lease wrap "
        "(disable with AGENTOS_GPU_COORD_DISABLE=1)"
    )
