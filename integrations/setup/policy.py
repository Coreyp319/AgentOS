#!/usr/bin/env python3
"""AgentOS model policy — which model families you trust, and the one red line you cannot cross (ADR-0049).

A pure, stdlib-only resolver over a local data file (policy.json). It answers ONE question —
``permits(ref) -> (bool, reason)`` — and it is the gate the research→adoption loop and (phase 2) the
agent-default propagation consult before anything is proposed, adopted, or set live.

Two layers, in strict precedence:

    safety-DENYLIST  >  family_block  >  allow_any_ollama  >  family_allow

* The **safety DENYLIST** (CSAM / non-consensual real-person likeness) is a CODE CONSTANT, checked
  FIRST and UNCONDITIONALLY, on the bare ref. No policy setting — not ``allow_any_ollama``, not a
  family allow — can reach under it. This is the ADR-0008 red line.
* **Curation** is everything else and is the user's to set: block a family outright, open the whole
  Ollama library (``allow_any_ollama``), or narrow what's acceptable to an explicit allowlist.

``family`` is **derived in code** from the ref's name segment — it is never a model-supplied field, so a
research model cannot mislabel its way past a block. Registry models carry an authoritative ``family``
tag (passed in as ``family=``); only raw, non-registry refs fall back to the heuristic here.

A missing or malformed policy file fails **closed** to *curated-only* (registry models permitted, raw
refs denied) — never to allow-any.

policy.json is a sensitive taste profile (it can reveal an interest in uncensored/NSFW families). It is
stored 0600 and MUST NEVER enter a feed, a job log, or a research prompt.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# ── the inviolable safety red line (ADR-0008 / ADR-0044 §19) ──────────────────────────────────────
# Substrings matched anywhere in a lowercased ref. CSAM / non-consensual real-person-likeness repos are
# categorically off-limits — no policy setting can override this. Kept in sync with setup.DENYLIST
# (setup.py imports this tuple so there is ONE source of truth).
DENYLIST: tuple[str, ...] = ("deadman44",)

# ── family taxonomy (derived, never model-supplied) ──────────────────────────────────────────────
# Ordered (first match wins): more-specific markers BEFORE the generic family they belong to
# (mistral-nemo before mistral; illustrious before sdxl). Scanned over the ref's NAME segment only
# (last '/'-component, tag stripped) so a typosquatted namespace ("qwen-safe/backdoor") does NOT
# inherit the family of its namespace — it resolves to 'other' and is not auto-allowed by a family
# allowlist. Registry models pass their authoritative tag in directly and never touch this map.
_FAMILY_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ── creative (image / video) ──
    ("illustrious", ("illustrious", "noobai")),
    ("pony", ("pony",)),
    ("flux", ("flux",)),
    ("hunyuan", ("hunyuan",)),
    ("ltx", ("ltx", "10eros", "ltxv")),
    ("wan", ("wan2", "wan-2", "wan_2", "wan22")),
    ("sdxl", ("sdxl", "sd-xl", "sd_xl")),
    ("sd15", ("sd1.5", "sd-1.5", "sd_15", "v1-5")),
    # ── text / vision LLMs ──
    # Brand families that ride on a base (dolphin = uncensored Mistral/Qwen finetune; hermes = Nous
    # finetune of Llama/Qwen) rank FIRST — that brand is what a user blocks, not the base
    # (dolphin3.0-mistral → 'dolphin', Hermes-3-Llama → 'hermes'). Pure markers like abliterate/huihui
    # are NOT families (they leave the base family intact) — they only drive the mature gate.
    ("dolphin", ("dolphin",)),
    ("hermes", ("hermes",)),
    # specific merges before their base family (mistral-nemo before mistral)
    ("mistral-nemo", ("mistral-nemo", "mistral_nemo", "mn-12b", "mn12b", "mag-mell", "magmell",
                      "rocinante", "-nemo", "_nemo", "nemomix")),
    ("mixtral", ("mixtral",)),
    ("mistral", ("mistral",)),
    ("qwen", ("qwen", "qwq")),
    ("llama", ("llama", "meta-llama", "codellama")),
    ("gemma", ("gemma",)),
    ("phi", ("phi-", "phi3", "phi4", "phi:", "phi_")),
    ("deepseek", ("deepseek",)),
    ("command-r", ("command-r", "command_r", "c4ai")),
    ("yi", ("yi-", "yi_", "01-ai")),
    ("granite", ("granite",)),
    ("moondream", ("moondream",)),
)

# The family names the wizard renders as allow/block chips (deduped, in declaration order).
KNOWN_FAMILIES: tuple[str, ...] = tuple(dict.fromkeys(f for f, _ in _FAMILY_MARKERS))

# Substrings that mark a model as uncensored / mature — they trigger the ADR-0008 age affirmation
# (conservative set; the base family is unchanged, e.g. huihui_ai/qwen2.5-abliterate stays 'qwen').
MATURE_MARKERS: tuple[str, ...] = ("abliterate", "uncensored", "nsfw", "lewd", "dolphin", "huihui")

DEFAULT_POLICY: dict = {
    "allow_any_ollama": False,
    "family_allow": [],
    "family_block": [],
    "mature_affirmed_at": None,
}

# Registry hosts a ref may be PULLED from under allow_any (ADR-0049 Phase 3). The default Ollama
# registry (no host in the ref) is always allowed; hf.co is Ollama's native GGUF source. An arbitrary
# host (a model-supplied, web-derived ref pointing at evil.com/…) is refused — that is the supply-chain
# pivot the host-pin closes. NOTE: only governs PULLING a non-present ref; a present ref the user already
# pulled themselves is unaffected.
ALLOWED_HOSTS: frozenset[str] = frozenset({"hf.co", "huggingface.co"})


# ── ref parsing ──────────────────────────────────────────────────────────────────────────────────
def _name_segment(ref: str) -> str:
    """The lowercased model NAME of a ref: last '/'-component, tag (':...') stripped. Family is read
    from this — NOT from the host/namespace — so 'qwen-safe/backdoor:latest' → 'backdoor' → 'other'."""
    s = (ref or "").strip().split()[0] if (ref or "").strip() else ""
    s = s.split("/")[-1]
    s = s.split(":", 1)[0]            # drop an ollama tag / quant suffix
    return s.lower()


def derive_family(ref: str) -> str:
    """The model family of a ref, derived deterministically from its name segment. 'other' if unknown.
    Registry models should pass their authoritative tag to permits(family=…) instead of relying on this."""
    name = _name_segment(ref)
    if not name:
        return "other"
    for family, markers in _FAMILY_MARKERS:
        if any(m in name for m in markers):
            return family
    return "other"


def is_denied_ref(ref: str) -> bool:
    """True iff the ref hits the inviolable safety DENYLIST (matched anywhere, case-insensitive)."""
    blob = (ref or "").lower()
    return any(d in blob for d in DENYLIST)


def ref_host(ref: str) -> str | None:
    """The registry HOST a ref names, or None for the default Ollama registry. A host is the part before
    the first '/' iff it looks like a domain/port ('hf.co', 'localhost', 'evil.com:5000') — so a plain
    'qwen3.6:27b' or a 'namespace/name' has no host (default registry), but 'hf.co/bartowski/X' → 'hf.co'."""
    s = (ref or "").strip().split()[0] if (ref or "").strip() else ""
    if "/" not in s:
        return None
    first = s.split("/")[0]
    if "." in first or ":" in first or first == "localhost":
        return first.lower()
    return None


def host_allowed(ref: str) -> bool:
    """May this ref be PULLED (ADR-0049 Phase 3)? The default registry (no host) + ALLOWED_HOSTS only."""
    h = ref_host(ref)
    return h is None or h in ALLOWED_HOSTS


def is_mature_marker(ref: str) -> bool:
    """True iff the ref carries an uncensored/mature marker (drives the age-affirmation gate)."""
    blob = (ref or "").lower()
    return any(m in blob for m in MATURE_MARKERS)


# ── policy file (0600, XDG) ──────────────────────────────────────────────────────────────────────
def policy_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "agentos" / "policy.json"


def _sanitize_family(name: str) -> str:
    """A family token reduced to a safe slug (lowercase, [a-z0-9-] only); '' if nothing survives."""
    out = "".join(c for c in (name or "").strip().lower() if c.isalnum() or c in "-_")
    return out


def normalize_policy(raw: object) -> dict:
    """Coerce arbitrary JSON into a well-typed policy, dropping junk — fail CLOSED on every field
    (a non-bool allow_any → False; a non-list of families → []). Never raises."""
    pol = dict(DEFAULT_POLICY)
    if isinstance(raw, dict):
        pol["allow_any_ollama"] = bool(raw.get("allow_any_ollama") is True)
        for key in ("family_allow", "family_block"):
            v = raw.get(key)
            if isinstance(v, list):
                fams = [_sanitize_family(x) for x in v if isinstance(x, str)]
                pol[key] = sorted({f for f in fams if f})
        ts = raw.get("mature_affirmed_at")
        pol["mature_affirmed_at"] = ts if isinstance(ts, (int, float)) else None
    return pol


def load_policy(path: Path | None = None) -> dict:
    """Read + normalize the policy. Any error (missing, malformed, unreadable) → the fail-closed
    default (curated-only, allow_any=False) — NEVER allow-any."""
    p = path or policy_path()
    try:
        return normalize_policy(json.loads(p.read_text()))
    except Exception:
        return dict(DEFAULT_POLICY)


def save_policy(pol: dict, path: Path | None = None) -> bool:
    """Atomically write a normalized policy at 0600 (same-dir temp + rename). Returns ok."""
    p = path or policy_path()
    data = normalize_policy(pol)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".policy.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, p)
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            return True
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        return False


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────
def permits(ref: str, *, in_registry: bool = False, family: str | None = None,
            pol: dict | None = None) -> tuple[bool, str]:
    """May this ref be proposed / adopted / set-default? Returns (allowed, reason_code).

    Precedence (fixed; rendered in the UI):  safety-DENYLIST > family_block > allow_any_ollama > family_allow.
    reason codes: 'safety-denied' | 'blocked-family' | 'curated' | 'allow-any' | 'not-in-allowlist' | 'not-curated'.

    `in_registry` = this exact ref is a curated registry model (authoritative). `family` = its
    authoritative tag (registry models pass it; raw refs leave it None → derived here).
    """
    pol = pol if pol is not None else load_policy()

    # 1 — the inviolable red line, FIRST and unconditional (independent of allow_any / allowlist).
    if is_denied_ref(ref):
        return False, "safety-denied"

    fam = (family or derive_family(ref) or "other").lower()

    # 2 — an explicit block stays sticky even under allow-any (a block is a stronger signal than a broad allow).
    if fam in set(pol.get("family_block", [])):
        return False, "blocked-family"

    # 3 — curated models are permitted (this is what 'curated-only' means).
    if in_registry:
        return True, "curated"

    # 4 — a raw, non-registry ref needs allow-any switched on at all.
    if not pol.get("allow_any_ollama"):
        return False, "not-curated"

    # 5 — allow-any, narrowed by a non-empty allowlist (empty allowlist = allow-any, not allow-none).
    allow = set(pol.get("family_allow", []))
    if allow and fam not in allow:
        return False, "not-in-allowlist"
    return True, "allow-any"


def is_affirmed(pol: dict | None = None) -> bool:
    """Has the user affirmed 18+ for the mature/uncensored surface?"""
    pol = pol if pol is not None else load_policy()
    return bool(pol.get("mature_affirmed_at"))


def requires_mature_affirm(ref: str, pol: dict | None = None) -> bool:
    """An adopt/pull of `ref` needs an age affirmation iff it carries a mature marker, OR allow_any is
    on (which opens the uncensored surface wholesale) — per ADR-0049 §9 / decision D5."""
    pol = pol if pol is not None else load_policy()
    return is_mature_marker(ref) or bool(pol.get("allow_any_ollama"))


# Human-readable, calm one-liners for each reason code — the UI must NEVER conflate a
# blocked-by-policy state (overridable) with a blocked-by-safety state (no override).
REASON_TEXT: dict[str, str] = {
    "safety-denied": "blocked by the safety red line — this cannot be overridden by any setting",
    "blocked-family": "blocked by your policy — this model family is on your blocklist",
    "not-curated": "not in your curated set — turn on “all Ollama models” to allow it",
    "not-in-allowlist": "its family is not on your allowlist",
    "curated": "allowed (a curated model)",
    "allow-any": "allowed (all Ollama models)",
}

# Reasons the user can act on by changing policy (vs. the safety red line, which they cannot).
OVERRIDABLE_REASONS: frozenset[str] = frozenset({"blocked-family", "not-curated", "not-in-allowlist"})
