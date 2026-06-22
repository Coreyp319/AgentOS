"""gpu-coordinator — Hermes plugin (ADR-0006). Holds an agentosd VRAM lease around inference so live
AI preempts the overnight dream (ADR-0010), while BACKGROUND inference queues behind it (ADR-0041).

It registers ONE middleware: ``llm_execution``. Around every provider call it holds an exclusive lease
on ``org.agentos.Coordinator1`` at a PER-CALL tier (ADR-0041 — was hardcoded ``interactive``):
  * a LIVE turn (cli/telegram/discord/… — a human is waiting) → ``interactive``: it preempts the dream
    and is never preempted;
  * a BACKGROUND turn (``cron``/``subagent``, or any platform in ``AGENTOS_GPU_BATCH_PLATFORMS``) →
    ``batch``: it QUEUES behind live turns and shares the heavy lane with dreaming (one consumer at a
    time), instead of competing as interactive.
Each tier has its OWN refcounted coordinator, so concurrent same-tier inferences share one lease (never
serialized) and the lane reopens when the last call of that tier finishes. The single-exclusive daemon
lease then does the rest: an interactive acquire preempts a batch holder; a batch acquire behind a live
interactive holder simply fails open (runs un-preempting) — correct by construction.

Design + review: ``docs/research/0006-*`` (brief) and ``docs/research/0007-*`` (5-reviewer
scorecard). The logic lives in ``lease_state.py`` (pure core) + ``coordinator.py`` (driver)
+ ``lease_client.py`` (persistent jeepney transport, busctl fallback); this file is just
the Hermes wiring.

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

# One refcounted coordinator PER tier (ADR-0041): live turns share the interactive coordinator, background
# turns share the batch one; the single-exclusive daemon lease arbitrates between them (interactive
# preempts batch). Lazily created, cached process-wide.
_COORDS: dict = {}
_COORD_LOCK = threading.Lock()

# Default BACKGROUND platforms → Tier::Batch. Only the platforms that are NEVER a live human turn: a live
# chat is cli/telegram/discord/tui/slack/imessage/feishu/api_server/gateway/acp; `cron` (the overnight
# batch sequence, ADR-0010 §6) and `subagent` (a delegated child) are background. Conservative BY THE
# ASYMMETRY: mis-tagging a live turn as batch would make the user's turn yield to the dream (a latency
# regression), so when unsure we stay interactive. NB neither `task_id` nor `platform` ALONE is a clean
# signal — Hermes fills a UUID task_id for live turns (turn_context.py `effective_task_id = task_id or
# uuid4()`) and a kanban task inherits its submitter's platform — which is exactly why we key only on the
# unambiguous-background platforms. Tunable via AGENTOS_GPU_BATCH_PLATFORMS (comma list; set empty to
# restore interactive-always).
_DEFAULT_BATCH_PLATFORMS = frozenset({"cron", "subagent"})


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


def _batch_platforms() -> frozenset:
    """Platforms whose inference acquires Tier::Batch. Default `{cron, subagent}`; override the WHOLE set
    via AGENTOS_GPU_BATCH_PLATFORMS (comma list — empty string ⇒ interactive-always)."""
    raw = os.environ.get("AGENTOS_GPU_BATCH_PLATFORMS")
    if raw is None:
        return _DEFAULT_BATCH_PLATFORMS
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def _classify_tier(**kwargs) -> str:
    """Per-call lease tier (the ADR-0041 fix for the hardcoded `interactive`). Background platform →
    `batch` (queues behind live, shares the dream lane); everything else → `interactive` (today's
    behavior). Asymmetry-safe: a live chat platform can never be classified batch."""
    platform = (kwargs.get("platform") or "").strip().lower()
    return "batch" if platform in _batch_platforms() else "interactive"


def _get_coordinator(tier: str):
    coord = _COORDS.get(tier)
    if coord is not None:
        return coord
    with _COORD_LOCK:
        coord = _COORDS.get(tier)
        if coord is None:
            client = _lease_client.make_lease_client(
                timeout_s=_env_float("AGENTOS_GPU_BUSCTL_TIMEOUT_S", 1.0),
            )
            coord = _coordinator.LeaseCoordinator(
                client,
                tier=tier,
                estimate_mib=_env_int("AGENTOS_GPU_ESTIMATE_MIB", 1024),
                renew_interval_s=_env_float("AGENTOS_GPU_RENEW_S", 60.0),
            )
            _COORDS[tier] = coord
    return coord


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
        tier = _classify_tier(**kwargs)          # ADR-0041: per-call (live → interactive, background → batch)
        coord = _get_coordinator(tier)
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
        "gpu-coordinator: llm_execution middleware registered — per-call VRAM lease wrap "
        "(live→interactive, background→batch via platform %s; disable with AGENTOS_GPU_COORD_DISABLE=1)",
        sorted(_batch_platforms()),
    )
