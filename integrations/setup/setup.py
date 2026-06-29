#!/usr/bin/env python3
"""AgentOS onboarding engine (ADR-0044) — detect what's already here, fetch only the gaps.

The brownfield-first model provisioner. A fresh box and a box that already has Ollama models +
a ComfyUI checkout with weights are the SAME flow: reconcile a curated *bundle* against what's
present, then download only what's missing — never re-fetch, never clobber an existing install.

It is a DETECTOR + thin fetch orchestrator, not a model manager (ADR-0001 / ADR-0044): it shells
out to the tools that already do downloading well —
  * Ollama text/vision     → `ollama pull <ref>`        (no auth, native resume)
  * HuggingFace weights     → `curl` on `…/resolve/main/…` (no auth for open repos; Bearer for gated)
  * Civitai (mature) weights→ `curl` on `/api/download/models/<versionId>` with a keyring token
— and reads the SAME source of truth the rest of AgentOS uses, integrations/models/registry.json
(extended here with per-model `fetch` artifacts + `bundles`). stdlib-only; curl/ollama/secret-tool
are the only externals (all already on the box).

Safety (inherits ADR-0008/0009): the Mature lane is explicit opt-in (--mature, age-affirmed at the
CLI), credentials live in the OS keyring (never a file/argv/log), and a hard denylist blocks the
CSAM/real-likeness repos (the `deadman44/*` exclusion from the local-video-gen skill).

CLI:
  setup.py detect                 brownfield report — every model: have ✓ / partial / fetch ✗
  setup.py bundles                list the curated bundles
  setup.py plan <bundle>          what a bundle needs vs. what's present (the gap), with sizes
  setup.py fetch <bundle> [--yes] download the gap (real). --mature to include the mature lane.
  setup.py creds set|clear <svc>  store/clear a HuggingFace/Civitai token in the OS keyring
  setup.py onboard                interactive: pick → plan → confirm → fetch → ready
  setup.py policy                 show the model family/safety policy + the live Hermes default (ADR-0049)
  setup.py research-json [text]   research the latest models → STRICT JSON candidates (--out PATH)
  setup.py adopt <ref> [--yes-mature]   adopt a present, permitted ollama ref into the registry (reversible)
  setup.py revert <id>            undo an adopt by id
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import policy  # ADR-0049 — the family/safety gate; shares the canonical DENYLIST

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")     # strip terminal control (ollama leaks cursor moves)

HERE = Path(__file__).resolve().parent
REGISTRY = HERE.parent / "models" / "registry.json"

# Hard exclusions — CSAM / non-consensual-real-likeness repos are categorically off-limits, never
# fetched regardless of bundle or token (the local-video-gen skill's denylist; ADR-0008 red line).
# The canonical list lives in policy.py (ADR-0049) so the gate + onboarding share ONE source of truth.
DENYLIST = policy.DENYLIST

HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{file}"
CIVITAI_DL = "https://civitai.com/api/download/models/{version_id}"
_OLLAMA_LIST_CACHE: list[str] | None = None

# ComfyUI runtime bootstrap (image/video need it; text does not — that's why text goes first).
COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI"
TORCH_INDEX = "https://download.pytorch.org/whl/cu124"
# AMD ROCm PyTorch wheel index (ADR-0048 Phase 2). ROCm/torch versions move fast and must match the
# user's installed ROCm — override with AGENTOS_TORCH_INDEX_ROCM. Default tracks a recent stable wheel.
TORCH_INDEX_ROCM = os.environ.get("AGENTOS_TORCH_INDEX_ROCM", "https://download.pytorch.org/whl/rocm6.2")


def _torch_index(vendor: str | None) -> str:
    """The PyTorch wheel index for this GPU vendor: ROCm for AMD, CUDA otherwise (ADR-0048)."""
    return TORCH_INDEX_ROCM if vendor == "amd" else TORCH_INDEX
# The core custom-node packs the shipped dream workflows need; the long tail is handled by
# ComfyUI-Manager. Continue-on-failure — a missing pack degrades one workflow, not the install.
NODE_PACKS = [
    ("ComfyUI-Manager", "https://github.com/ltdrdata/ComfyUI-Manager"),
    ("ComfyUI-GGUF", "https://github.com/city96/ComfyUI-GGUF"),
    ("ComfyUI-KJNodes", "https://github.com/kijai/ComfyUI-KJNodes"),
    ("ComfyUI-LTXVideo", "https://github.com/Lightricks/ComfyUI-LTXVideo"),
]
_DEFAULT_PROMPT = {"image": "A young girl in a red dress dancing gracefully on a sunlit beach.",
                   "video": "A young girl in a red dress dancing gracefully on a sunlit beach."}


# ── registry ────────────────────────────────────────────────────────────────────────────────
def load_registry(path: Path | None = None) -> dict:
    return json.loads((path or REGISTRY).read_text())


def models(reg: dict) -> list[dict]:
    return reg.get("models", [])


def bundles(reg: dict) -> list[dict]:
    return reg.get("bundles", [])


def find_model(reg: dict, mid: str) -> dict | None:
    return next((m for m in models(reg) if m.get("id") == mid), None)


def find_bundle(reg: dict, bid: str) -> dict | None:
    return next((b for b in bundles(reg) if b.get("id") == bid), None)


# ── environment probes (brownfield detection) ────────────────────────────────────────────────
def comfy_root() -> Path:
    return Path(os.environ.get("COMFY_ROOT") or (Path.home() / "ComfyUI"))


def ollama_models(refresh: bool = False) -> list[str]:
    """The refs `ollama list` reports (full names incl. tag), or [] if ollama is absent."""
    global _OLLAMA_LIST_CACHE
    if _OLLAMA_LIST_CACHE is not None and not refresh:
        return _OLLAMA_LIST_CACHE
    out = []
    if shutil.which("ollama"):
        try:
            r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15, check=False)
            for line in r.stdout.splitlines()[1:]:               # skip the header
                tok = line.split()
                if tok:
                    out.append(tok[0])
        except Exception:
            pass
    _OLLAMA_LIST_CACHE = out
    return out


def _invalidate_ollama_cache() -> None:
    global _OLLAMA_LIST_CACHE
    _OLLAMA_LIST_CACHE = None


def _ollama_has(ref: str) -> bool:
    have = ollama_models()
    if ref in have:
        return True
    # ollama normalises `:latest`; compare base names too
    base = ref.split(":")[0]
    return any(h == ref or h.split(":")[0] == base and (":" not in ref or h == ref) for h in have)


def _comfy_path(dest: str) -> Path:
    return comfy_root() / "models" / dest


# ── ComfyUI runtime (image/video need it; clone + uv venv + torch + core node packs) ─────────
def comfyui_present() -> bool:
    return (comfy_root() / ".venv" / "bin" / "python").exists()


def comfyui_setup(dry: bool = False, run=subprocess.run, vendor: str | None = None) -> dict:
    """Bootstrap the ComfyUI runtime if absent: clone, make the uv venv, install torch (CUDA on
    NVIDIA, ROCm on AMD — ADR-0048) + requirements, then the core node packs. Idempotent
    (skip-if-present); continue-on-failure for node packs. Returns a result dict; `steps` when dry."""
    root = comfy_root()
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.exists():
        return {"ok": True, "skipped": "present", "root": str(root)}
    uv = shutil.which("uv")
    steps: list[list[str]] = []
    if not (root / "main.py").exists():
        steps.append(["git", "clone", "--depth", "1", COMFY_REPO, str(root)])
    if uv:
        steps.append([uv, "venv", str(root / ".venv"), "--python", "3.12"])
        pip = [uv, "pip", "install", "--python", str(venv_py)]
    else:
        steps.append(["python3", "-m", "venv", str(root / ".venv")])
        pip = [str(venv_py), "-m", "pip", "install"]
    if vendor is None:                              # ADR-0048: pick CUDA vs ROCm wheels by GPU vendor
        vendor = detect_hardware().get("vendor")
    steps.append(pip + ["torch", "torchvision", "torchaudio", "--index-url", _torch_index(vendor)])
    steps.append(pip + ["-r", str(root / "requirements.txt")])
    if dry:
        return {"ok": True, "steps": steps, "node_packs": [u for _, u in NODE_PACKS]}
    for cmd in steps:
        r = run(cmd, check=False)
        if getattr(r, "returncode", 1) != 0:
            return {"ok": False, "failed": cmd, "root": str(root)}
    cn = root / "custom_nodes"
    cn.mkdir(parents=True, exist_ok=True)
    for name, url in NODE_PACKS:                       # continue-on-failure — Manager covers the tail
        d = cn / name
        if not d.exists():
            run(["git", "clone", "--depth", "1", url, str(d)], check=False)
        reqs = d / "requirements.txt"
        if reqs.exists():
            run(pip + ["-r", str(reqs)], check=False)
    return {"ok": True, "root": str(root)}


# ── "text aids the rest": the local text model writes the first image/video prompt ───────────
def _text_model_present() -> str | None:
    reg = load_registry()
    for m in models(reg):
        if m.get("modality") == "text":
            for a in artifacts(m):
                if a.get("via") == "ollama" and _ollama_has(a.get("ref", "")):
                    return a["ref"]
    return None


def suggest_opening_prompt(modality: str = "image", model: str | None = None, run=subprocess.run) -> str:
    """Use the already-installed text model to write a vivid opening prompt for the first image/
    video — the concrete payoff of doing text FIRST. Falls back to a calm default with no model."""
    model = model or _text_model_present()
    default = _DEFAULT_PROMPT.get("video" if modality == "video" else "image", _DEFAULT_PROMPT["image"])
    if not model:
        return default
    meta = ("Write ONE short, vivid, SFW visual prompt (max 18 words) for a "
            + ("short video scene" if modality == "video" else "single image")
            + ". Reply with only the prompt — no preamble, no quotes.")
    try:
        r = run(["ollama", "run", model, meta], capture_output=True, text=True, timeout=60, check=False)
        lines = [ln.strip().strip('"').strip() for ln in _ANSI.sub("", r.stdout or "").splitlines() if ln.strip()]
        cand = (lines[0] if lines else "")[:200]
        return cand or default
    except Exception:
        return default


# ── hardware detection (so the wizard can say what fits your GPU) ─────────────────────────────
def _amd_vram_mib() -> tuple[int, int]:
    """(total, free) AMD VRAM in MiB from sysfs, or (0, 0) if no AMD GPU is present. sysfs is the
    dependency-free, root-free primary (ADR-0048): `mem_info_vram_{total,used}` are bytes and
    free = total − used. We read it directly rather than take a ROCm/amd-smi dependency, because
    sysfs keeps reporting even when amd-smi lags new silicon."""
    for dev in sorted(Path("/sys/class/drm").glob("card[0-9]*/device")):
        try:
            if (dev / "vendor").read_text().strip() != "0x1002":          # 0x1002 = AMD
                continue
            total = int((dev / "mem_info_vram_total").read_text().strip()) // (1024 * 1024)
            used = int((dev / "mem_info_vram_used").read_text().strip()) // (1024 * 1024)
            if total:
                return total, max(total - used, 0)
        except (OSError, ValueError):
            continue
    return 0, 0


def detect_hardware(run=subprocess.run) -> dict:
    vram_total = vram_free = 0
    vendor = None
    if shutil.which("nvidia-smi"):
        try:
            r = run(["nvidia-smi", "--query-gpu=memory.total,memory.free",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8, check=False)
            ln = (r.stdout or "").strip().splitlines()
            if ln:
                t, f = ln[0].split(",")
                vram_total, vram_free = int(t.strip()), int(f.strip())   # MiB
                vendor = "nvidia"
        except Exception:
            pass
    if not vram_total:                              # no NVIDIA datum → try AMD via sysfs (ADR-0048)
        t, f = _amd_vram_mib()
        if t:
            vram_total, vram_free, vendor = t, f, "amd"
    ram_gb = 0.0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) / 1024 / 1024              # kB → GiB
                break
    except Exception:
        pass
    # `vendor`: "nvidia" | "amd" | None — lets the wizard flag AMD as experimental (ADR-0048).
    return {"vram_mib": vram_total, "vram_free_mib": vram_free,
            "vram_gb": round(vram_total / 1024, 1), "ram_gb": round(ram_gb, 1), "vendor": vendor}


# A model's loaded VRAM footprint (GB) — what it actually holds at runtime, NOT its download size.
# Explicit `vram_gb` wins (a measured peak, e.g. the 12B beat-writer ≈13 GB; ADR-0045). Else an Ollama
# LLM's runtime VRAM (weights + KV cache + CUDA context) runs well over its on-disk weights → derive
# ~1.5× size_gb; ComfyUI registry sizes are ALREADY loaded peaks → used verbatim.
def model_vram_gb(m: dict) -> float:
    explicit = m.get("vram_gb")
    if explicit:
        return float(explicit)
    size = float(m.get("size_gb", 0) or 0)
    return round(size * 1.5, 1) if m.get("runtime") == "ollama" else size


def _fits(gb: float, hw: dict) -> bool:
    vram = hw.get("vram_gb", 0)
    return bool(vram) and gb <= vram * 0.92          # 0.92 = headroom for the desktop + KV slack


def bundle_peak_gb(reg: dict, bundle: dict, hw: dict | None = None) -> float:
    """The heaviest single-model VRAM footprint (GB) of the models that will ACTUALLY run on this box
    — the 'peak' a render must hold at once. With hw, reflects the tier-aware downselect (so the UI
    draws an honest peak-vs-VRAM bar for THIS GPU); without hw, the full bundle."""
    return round(max((model_vram_gb(m) for m in resolve_bundle(reg, bundle, hw)), default=0.0), 1)


def bundle_fit(reg: dict, bundle: dict, hw: dict) -> str:
    """Does the bundle (after the tier-aware downselect) fit this GPU? 'fits' | 'tight' | 'too-big' |
    'unknown'. A bundle whose defining model IS its hero (the video lane) can't downselect, so it
    honestly reads 'too-big' on a small GPU rather than silently dropping the lane."""
    vram = hw.get("vram_gb", 0)
    if not vram:
        return "unknown"
    biggest = bundle_peak_gb(reg, bundle, hw)
    if biggest <= vram * 0.92:
        return "fits"
    if biggest <= vram:
        return "tight"
    return "too-big"


# ── optional: a quick research agent suggests the latest models for this hardware ────────────
# Model PROPOSES (web-search reasoning), human DISPOSES (we print suggestions; never auto-edit the
# registry). Inherits the dispatch tool-discipline: run `claude` headless, allow only web tools.
def research_models(modality: str = "video", hw: dict | None = None, run=subprocess.run,
                    timeout: int = 200, claude: str | None = None) -> dict:
    hw = hw or detect_hardware()
    claude = claude or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not Path(claude).exists():
        return {"ok": False, "error": "the `claude` CLI isn't installed — needed to research models"}
    prompt = (
        f"You are AgentOS's model-currency scout. Recommend the BEST current local, open-weights "
        f"{modality}-generation model(s) for a single GPU in the {vram_bucket(hw)} VRAM class, runnable via "
        f"{'ComfyUI' if modality in ('image', 'video') else 'Ollama'} as of today. Give 2-3 concrete "
        f"picks: name, HuggingFace repo (or Ollama ref), approx size/quant that fits this VRAM, and a "
        f"one-line why. Prefer permissive licenses. Exclude any minor-targeted or non-consensual "
        f"real-person-likeness model. Be concise — a short ranked list, no preamble."
    )
    cmd = [claude, "-p", prompt, "--model", os.environ.get("AGENTOS_RESEARCH_MODEL", "claude-sonnet-4-6"),
           "--allowedTools", "WebSearch WebFetch", "--output-format", "text"]   # one arg — separate args drop WebFetch
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as e:
        return {"ok": False, "error": f"research failed: {e}"}
    out = (r.stdout or "").strip()
    if not out:
        return {"ok": False, "error": "no output — is `claude` authenticated?"}
    return {"ok": True, "modality": modality, "hardware": hw, "suggestions": out}


# ── artifact / model presence ────────────────────────────────────────────────────────────────
def artifact_present(art: dict) -> bool:
    via = art.get("via")
    if via == "ollama":
        return _ollama_has(art.get("ref", ""))
    if via in ("hf", "civitai"):
        p = _comfy_path(art.get("dest", ""))
        # present iff the file exists and is plausibly complete (not a half-written .part)
        return p.is_file() and p.stat().st_size > 0 and not p.with_suffix(p.suffix + ".part").exists()
    if via == "manual":
        dest = art.get("dest")
        return bool(dest) and _comfy_path(dest).is_file()
    return False


def artifacts(model: dict) -> list[dict]:
    f = model.get("fetch")
    return f if isinstance(f, list) else ([f] if isinstance(f, dict) else [])


def model_status(model: dict) -> dict:
    arts = artifacts(model)
    if not arts:
        return {"state": "unknown", "have": 0, "need": 0, "missing": []}
    have = [a for a in arts if artifact_present(a)]
    missing = [a for a in arts if not artifact_present(a)]
    state = "have" if not missing else ("partial" if have else "fetch")
    return {"state": state, "have": len(have), "need": len(arts), "missing": missing}


# ── denylist + auth ──────────────────────────────────────────────────────────────────────────
def is_denied(art: dict) -> bool:
    blob = " ".join(str(art.get(k, "")) for k in ("repo", "ref", "version_id", "file", "dest"))
    return policy.is_denied_ref(blob)        # ADR-0049: the bare-ref form, shared with the policy gate


def artifact_auth(art: dict) -> str:
    """'none' | 'hf' | 'civitai' — the credential a fetch needs."""
    if art.get("via") == "civitai":
        return "civitai"
    return art.get("auth", "none")


# ── OS keyring (Secret Service / KWallet) — never a file ─────────────────────────────────────
def _token_file(svc: str) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "agentos" / f"{svc}.token"


def keyring_get(svc: str) -> str | None:
    if shutil.which("secret-tool"):
        try:
            r = subprocess.run(["secret-tool", "lookup", "service", svc, "key", "api-token"],
                               capture_output=True, text=True, timeout=10, check=False)
            if r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    try:                                                   # disclosed 0600 fallback (no keyring)
        p = _token_file(svc)
        return (p.read_text().strip() or None) if p.exists() else None
    except Exception:
        return None


def keyring_set(svc: str, token: str) -> bool:
    if shutil.which("secret-tool"):
        try:
            # token on stdin — never argv (it would show in /proc/<pid>/cmdline)
            r = subprocess.run(["secret-tool", "store", "--label", f"AgentOS {svc} API token",
                                "service", svc, "key", "api-token"],
                               input=token, text=True, timeout=10, check=False)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    # Fallback: a DISCLOSED 0600 file (ADR-0044 §133) when no OS keyring is reachable.
    try:
        p = _token_file(svc)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        print(f"  (no OS keyring found — token stored in {p} at 0600, not the keyring)", file=sys.stderr)
        return True
    except Exception:
        return False


def keyring_clear(svc: str) -> bool:
    ok = False
    if shutil.which("secret-tool"):
        try:
            ok = subprocess.run(["secret-tool", "clear", "service", svc, "key", "api-token"],
                                capture_output=True, text=True, timeout=10, check=False).returncode == 0
        except Exception:
            pass
    try:
        p = _token_file(svc)
        if p.exists():
            p.unlink()
            ok = True
    except Exception:
        pass
    return ok


# ── reversibility manifest (ADR-0044 §152: what this wizard fetched, so it can be listed) ─────
def manifest_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "agentos" / "setup-manifest.json"


def read_manifest() -> dict:
    try:
        return json.loads(manifest_path().read_text())
    except Exception:
        return {"v": 1, "fetched": []}


def record_fetch(art: dict, dest: str) -> None:
    """Append the inverse record for a completed fetch — the 'what's stored' audit + the basis for
    a future refcount-gated Remove (ADR-0044). Best-effort, atomic; never blocks a download."""
    try:
        p = manifest_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = read_manifest()
        data.setdefault("fetched", [])
        if any(e.get("dest") == dest for e in data["fetched"]):
            return
        size = 0
        if art.get("via") in ("hf", "civitai"):
            try:
                size = Path(dest).stat().st_size
            except Exception:
                pass
        data["fetched"].append({"dest": dest, "via": art.get("via"), "bytes": size})
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, p)
    except Exception:
        pass


# ── fetch (the real download; idempotent skip-if-present) ────────────────────────────────────
def _curl_cmd(url: str, dest: Path, auth: bool) -> list[str]:
    """A resumable curl to a .part file (atomic rename on success in fetch_artifact). When auth is
    needed, `--config -` makes curl read the Authorization header from STDIN — so the token NEVER
    lands in argv (it would otherwise be world-visible in /proc/<pid>/cmdline)."""
    cmd = ["curl", "-fL", "--retry", "3", "--retry-delay", "2", "-C", "-",
           "-A", "agentos-setup", "-o", str(dest) + ".part"]
    if auth:
        cmd += ["--config", "-"]
    cmd.append(url)
    return cmd


def fetch_artifact(art: dict, token: str | None = None, run=subprocess.run, dry: bool = False) -> dict:
    """Download one artifact. Idempotent: a present artifact is skipped. Returns a result dict.
    Real download streams progress to the terminal (stdout/stderr inherited)."""
    if is_denied(art):
        return {"ok": False, "skipped": "denied", "reason": "on the CSAM/real-likeness denylist"}
    if artifact_present(art):
        return {"ok": True, "skipped": "present"}
    via = art.get("via")

    if via == "ollama":
        ref = art["ref"]
        if dry:
            return {"ok": True, "cmd": ["ollama", "pull", ref]}
        r = run(["ollama", "pull", ref], check=False)
        ok = getattr(r, "returncode", 1) == 0
        if ok:
            _invalidate_ollama_cache()          # the new model changes presence — re-read next time
            record_fetch(art, ref)
        return {"ok": ok, "ref": ref}

    if via in ("hf", "civitai"):
        dest = _comfy_path(art["dest"])
        if via == "hf":
            url = HF_RESOLVE.format(repo=art["repo"], file=art.get("file") or Path(art["dest"]).name)
        else:
            vid = art.get("version_id")
            if not vid:
                return {"ok": False, "skipped": "manual",
                        "reason": "Civitai version id not set — download by hand from the model page"}
            url = CIVITAI_DL.format(version_id=vid)
        need_tok = artifact_auth(art) != "none"
        if need_tok and not token:
            return {"ok": False, "skipped": "needs-token", "reason": f"needs a {artifact_auth(art)} token"}
        cmd = _curl_cmd(url, dest, auth=need_tok)
        if dry:
            return {"ok": True, "cmd": cmd, "stdin_auth": need_tok}
        dest.parent.mkdir(parents=True, exist_ok=True)
        # the token (if any) goes to curl on STDIN as a config directive — never argv (/proc leak)
        stdin = f'header = "Authorization: Bearer {token}"\n' if need_tok else None
        r = run(cmd, input=stdin, text=True, check=False) if stdin is not None else run(cmd, check=False)
        if getattr(r, "returncode", 1) == 0 and Path(str(dest) + ".part").exists():
            os.replace(str(dest) + ".part", str(dest))            # atomic: only a complete file lands
            record_fetch(art, str(dest))
            return {"ok": True, "dest": str(dest)}
        return {"ok": getattr(r, "returncode", 1) == 0, "dest": str(dest)}

    if via == "manual":
        return {"ok": False, "skipped": "manual", "reason": art.get("note", "install by hand")}
    return {"ok": False, "skipped": "unknown-via"}


# ── bundle resolution + planning ─────────────────────────────────────────────────────────────
def select_models(reg: dict, bundle: dict, hw: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(keep, deferred): the models to fetch/run on THIS GPU, and the hero-tier models deferred because
    they don't fit. A non-fitting hero is deferred ONLY if dropping it leaves another model of its OWN
    modality in the bundle — so a bundle's defining model (the video lane's i2v hero, the only video
    model) is never dropped; it stays and the bundle honestly reads 'too-big'. Minimum-tier models are
    always kept. No hw / unknown VRAM → (full bundle, []): fail-open, never cut blind."""
    full = [m for mid in bundle.get("models", []) if (m := find_model(reg, mid))]
    if not hw or not hw.get("vram_gb"):
        return full, []
    deferred, deferred_ids = [], set()
    for m in full:
        if m.get("tier") != "hero" or _fits(model_vram_gb(m), hw):
            continue
        mod = m.get("modality")
        if any(x["id"] != m["id"] and x["id"] not in deferred_ids and x.get("modality") == mod for x in full):
            deferred.append(m)
            deferred_ids.add(m["id"])
    keep = [m for m in full if m["id"] not in deferred_ids]
    return keep, deferred


def resolve_bundle(reg: dict, bundle: dict, hw: dict | None = None) -> list[dict]:
    return select_models(reg, bundle, hw)[0]


def plan_bundle(reg: dict, bundle: dict, include_mature: bool = False, hw: dict | None = None,
                pol: dict | None = None) -> dict:
    """Reconcile a bundle against the box: which models are present, which need fetching, what
    auth/size that implies. The mature lane is excluded unless include_mature. With hw, hero-tier
    models that don't fit this GPU are deferred (not fetched) and reported under `deferred` — so a
    small-VRAM box gets the fitting set, with the heavy upgrade surfaced rather than silently pulled.
    ADR-0049: models whose `family` is on the user's blocklist are filtered out of the gap and surfaced
    under `blocked` (the default policy blocks nothing, so the shipped behavior is unchanged)."""
    use, deferred = select_models(reg, bundle, hw)
    pol = pol if pol is not None else policy.load_policy()
    block = set(pol.get("family_block", []))
    blocked = [{"id": m["id"], "family": m.get("family")} for m in use if m.get("family") in block]
    use = [m for m in use if m.get("family") not in block]
    rows, gap_arts, need_auth, denied = [], [], set(), []
    total_gb = 0.0
    for m in use:
        if m.get("rating") == "mature" and not include_mature:
            rows.append({"id": m["id"], "rating": "mature", "state": "skipped-mature"})
            continue
        st = model_status(m)
        rows.append({"id": m["id"], "rating": m.get("rating", "sfw"), "modality": m.get("modality"),
                     "state": st["state"], "have": st["have"], "need": st["need"]})
        for a in st["missing"]:
            if is_denied(a):
                denied.append(a)
                continue
            gap_arts.append(a)
            au = artifact_auth(a)
            if au != "none":
                need_auth.add(au)
        if st["state"] != "have":
            total_gb += float(m.get("size_gb", 0) or 0)
    return {"bundle": bundle["id"], "rows": rows, "gap": gap_arts, "needs_auth": sorted(need_auth),
            "approx_gb": round(total_gb, 1), "denied": denied, "blocked": blocked,
            "deferred": [{"id": m["id"], "name": m.get("role") or m["id"], "vram_gb": model_vram_gb(m)}
                         for m in deferred]}


def fetch_plan(plan: dict, tokens: dict | None = None, run=subprocess.run, dry: bool = False) -> list[dict]:
    """Execute a plan's gap. HF/Ollama run in declared order; Civitai is serialized (it already is,
    being one-at-a-time here). Skips artifacts whose token is absent (honest, not a failure)."""
    tokens = tokens or {}
    results = []
    for art in plan["gap"]:
        au = artifact_auth(art)
        tok = tokens.get(au) if au != "none" else None
        res = fetch_artifact(art, token=tok, run=run, dry=dry)
        res["artifact"] = art.get("dest") or art.get("ref")
        results.append(res)
    return results


# ── policy + research→adoption loop (ADR-0049) ─────────────────────────────────────────────────
# Closes the loop that used to dead-end at "printed suggestions → hand-edit registry.json": research
# returns STRUCTURED candidates, code re-derives every load-bearing field, the family/safety policy
# gates them, and a reviewed candidate is adopted into the registry reversibly. The LIVE Hermes-default
# write is ADR-0049 Phase 2 (gated on a measured canary); here we only READ the current default.

def _ref_of(model: dict) -> str:
    """A registry model's primary ref: its ollama artifact ref if any, else its `model` field."""
    for a in artifacts(model):
        if a.get("via") == "ollama" and a.get("ref"):
            return a["ref"]
    return model.get("model", "") or model.get("id", "")


def registry_match(reg: dict, ref: str) -> dict | None:
    """The registry model a ref names (by `model`, ollama ref, or any artifact ref), or None."""
    ref = (ref or "").strip()
    if not ref:
        return None
    for m in models(reg):
        if m.get("model") == ref or _ref_of(m) == ref or any(a.get("ref") == ref for a in artifacts(m)):
            return m
    return None


def permits_ref(reg: dict, ref: str, pol: dict | None = None) -> tuple[bool, str]:
    """policy.permits for a ref, resolving in_registry + the authoritative family from the registry."""
    m = registry_match(reg, ref)
    fam = m.get("family") if m else None
    return policy.permits(ref, in_registry=bool(m), family=fam, pol=pol)


# ── read-only: the live Hermes inference default (config.yaml is the source of truth) ────────────
def hermes_config_path() -> Path:
    return Path(os.environ.get("HERMES_CONFIG") or (Path.home() / ".hermes" / "config.yaml"))


def hermes_current_default(path: Path | None = None) -> str | None:
    """Read the LIVE Hermes inference default from ~/.hermes/config.yaml (the top-level model: block's
    `default:`). READ-ONLY, stdlib line-parse (no yaml dep, no write). Returns None if absent — an honest
    'unknown', never a guess. The LIVE write is ADR-0049 Phase 2 (the HermesAdapter)."""
    p = path or hermes_config_path()
    try:
        lines = p.read_text().splitlines()
    except Exception:
        return None
    in_model = False
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[:1] not in (" ", "\t"):                      # a top-level key — (re)decide if we're in model:
            in_model = (ln.split(":", 1)[0].strip() == "model")
            continue
        if in_model and ln.strip().split(":", 1)[0].strip() == "default":
            return (ln.split(":", 1)[1].strip().strip('"').strip("'") or None)
    return None


# ── structured research (model PROPOSES; code re-derives + disposes) ─────────────────────────────
_VRAM_BUCKETS = ((24, "≥24 GB"), (16, "16–24 GB"), (8, "8–16 GB"), (1, "<8 GB"))


def vram_bucket(hw: dict) -> str:
    """A COARSE VRAM class — the ONLY hardware fact the research prompt may carry off-box (never the
    exact card, the installed-model inventory, or the family policy; ADR-0049 §4)."""
    g = hw.get("vram_gb") or 0
    for floor, label in _VRAM_BUCKETS:
        if g >= floor:
            return label
    return "unknown"


def _num(v) -> float:
    try:
        return round(float(v), 1)
    except Exception:
        return 0.0


def _parse_candidates(text: str) -> list[dict]:
    """Tolerantly extract a JSON array of candidate objects from model output (handles a ```json fence
    and surrounding prose). Returns normalized {name, ref, approx_gb, license, why}; [] on junk — never
    raises. A model-supplied `family` is IGNORED here (family is derived in code at validation)."""
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)     # unwrap a markdown fence if present
    if m:
        s = m.group(1).strip()
    start, depth, arr = s.find("["), 0, None                # first balanced [...] array
    if start != -1:
        for i in range(start, len(s)):
            depth += 1 if s[i] == "[" else (-1 if s[i] == "]" else 0)
            if depth == 0:
                arr = s[start:i + 1]
                break
    try:
        data = json.loads(arr) if arr else []
    except Exception:
        data = []
    out = []
    for it in (data if isinstance(data, list) else []):
        if not isinstance(it, dict):
            continue
        ref = str(it.get("ref", "")).strip()
        if not ref:
            continue
        out.append({"name": str(it.get("name", "") or ref)[:120], "ref": ref[:200],
                    "approx_gb": _num(it.get("approx_gb")), "license": str(it.get("license", "") or "")[:60],
                    "why": str(it.get("why", "") or "")[:240]})
    return out


def research_candidates(modality: str = "text", hw: dict | None = None, run=subprocess.run,
                        timeout: int = 200, claude: str | None = None) -> dict:
    """Ask a research agent for the latest local, open-weights models for THIS GPU as a STRICT JSON
    array. Privacy (ADR-0049 §4): only the modality + a COARSE VRAM bucket leave the box — never the
    installed-model inventory, never the family policy. The result is a PROPOSAL; every field is
    re-derived/re-checked in code (validate_candidates) before anything can be adopted."""
    hw = hw or detect_hardware()
    claude = claude or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not Path(claude).exists():
        return {"ok": False, "error": "the `claude` CLI isn't installed — needed to research models"}
    bucket = vram_bucket(hw)
    runner = "Ollama" if modality == "text" else "ComfyUI"
    prompt = (
        "You are AgentOS's model-currency scout. Recommend the 3-5 best CURRENT local, open-weights "
        f"{modality} models runnable via {runner} on a single GPU in the {bucket} VRAM class, as of today. "
        "Reply with ONLY a JSON array (no prose, no markdown fence) of objects with EXACTLY these keys: "
        '"name" (str), "ref" (the exact Ollama ref or HuggingFace repo[:quant]), "approx_gb" (number — the '
        'runnable quant that fits this VRAM class), "license" (SPDX-ish str), "why" (one short sentence). '
        "Prefer permissive licenses. Exclude any minor-targeted or non-consensual real-person-likeness model."
    )
    cmd = [claude, "-p", prompt, "--model", os.environ.get("AGENTOS_RESEARCH_MODEL", "claude-sonnet-4-6"),
           "--allowedTools", "WebSearch WebFetch", "--output-format", "text"]   # one arg — separate args drop WebFetch
    try:
        r = run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as e:
        return {"ok": False, "error": f"research failed: {e}"}
    out = (r.stdout or "").strip()
    if not out:
        return {"ok": False, "error": "no output — is `claude` authenticated?"}
    return {"ok": True, "modality": modality, "vram_bucket": bucket, "disclosed": True,
            "candidates": _parse_candidates(out), "raw": out[:4000]}


def validate_candidates(reg: dict, candidates: list[dict], pol: dict | None = None) -> list[dict]:
    """Re-derive `family` in CODE and run the gate — denylist FIRST, then policy — plus a present-check on
    each model-PROPOSED candidate. Marks adoptable | rejected(reason); a model-supplied field NEVER gates.
    Junk in → [] out, never an exception (the degrade-to-idle discipline)."""
    pol = pol if pol is not None else policy.load_policy()
    out = []
    for c in (candidates or []):
        if not isinstance(c, dict):
            continue                                          # tolerate junk — never raise (degrade-to-idle)
        ref = str(c.get("ref", "")).strip()
        if not ref:
            continue
        m = registry_match(reg, ref)
        fam = m.get("family") if m else policy.derive_family(ref)         # authoritative tag, else derived
        ok, reason = policy.permits(ref, in_registry=bool(m), family=fam, pol=pol)
        present = _ollama_has(ref)
        adoptable = bool(ok and present and not m)
        if not ok:
            pass                                              # keep the policy/safety reason
        elif m:
            reason = "already-registered"
        elif not present:
            reason = "not-present"                            # permitted but not pulled (phase-1: pull first)
        else:
            reason = "ok"
        out.append({**c, "family": fam, "in_registry": bool(m), "present": present,
                    "denied": policy.is_denied_ref(ref), "permitted": ok, "adoptable": adoptable,
                    "reason": reason, "overridable": reason in policy.OVERRIDABLE_REASONS})
    return out


# ── adopt-into-registry (the first reversible mutation) ──────────────────────────────────────────
def _slug(s: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return base or "model"


def _new_model_entry(ref: str, *, name: str | None = None, modality: str = "text",
                     why: str | None = None) -> dict:
    fam = policy.derive_family(ref)
    short = ref.split("/")[-1].split(":")[0]
    # the id carries a short hash of the FULL ref so two distinct refs that share a trailing name
    # (gemma4:latest vs huihui_ai/gemma4:q8) never collide into one entry (a false "adopted").
    h = hashlib.sha1(ref.encode("utf-8", "replace")).hexdigest()[:6]
    rating = "mature" if policy.is_mature_marker(ref) else "sfw"
    return {"id": "adopted-" + _slug(name or short) + "-" + h, "family": fam,
            "role": ((name or ref)[:120]) + " (adopted)",
            "purpose": (why or f"Adopted via the ADR-0049 research loop. Family: {fam}.")[:280],
            "model": ref, "runtime": "ollama", "size_gb": 0,
            "source": f"ollama (local): {ref}", "local": True, "safety_critical": False,
            "used_by": [], "modality": modality, "rating": rating, "tier": "hero",
            "notes": "Adopted from model-currency research (ADR-0049). Edit/remove here, or Revert in the wizard.",
            "fetch": [{"via": "ollama", "ref": ref}], "adopted": True}


@contextlib.contextmanager
def _file_lock(path: Path):
    """Serialize a read-modify-write on a JSON file across the wizard's threads (ThreadingHTTPServer) via
    an flock on a sidecar `.lock` — so two concurrent adopts/reverts can't lose a write or drop an inverse."""
    lock = Path(str(path) + ".lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_json(path: Path, data: dict, mode: int = 0o644) -> bool:
    """Durable atomic write: a UNIQUE temp (never a shared *.tmp two writers would clobber), fsync, rename."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            try:                              # fsync the dir so the rename is durable — the inverse (manifest)
                dfd = os.open(str(path.parent), os.O_DIRECTORY)   # must land before the config change it guards
                os.fsync(dfd)
                os.close(dfd)
            except OSError:
                pass
            return True
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        return False


def add_registry_model(registry_path: Path, entry: dict) -> bool:
    """Append a model entry (idempotent on id; the whole read-modify-write is under the registry lock)."""
    registry_path = Path(registry_path)
    with _file_lock(registry_path):
        try:
            reg = json.loads(registry_path.read_text())
        except Exception:
            return False
        if any(m.get("id") == entry.get("id") for m in reg.get("models", [])):
            return True                                       # idempotent
        reg.setdefault("models", []).append(entry)
        return _atomic_write_json(registry_path, reg)


def remove_registry_model(registry_path: Path, model_id: str) -> bool:
    """Drop a model entry by id (idempotent — removing an absent entry is a success; under the lock)."""
    registry_path = Path(registry_path)
    with _file_lock(registry_path):
        try:
            reg = json.loads(registry_path.read_text())
        except Exception:
            return False
        kept = [m for m in reg.get("models", []) if m.get("id") != model_id]
        if len(kept) == len(reg.get("models", [])):
            return True                                       # already gone
        reg["models"] = kept
        return _atomic_write_json(registry_path, reg)


# ── reversibility ledger (the inverse records, alongside record_fetch's `fetched`) ───────────────
def _write_manifest(data: dict) -> bool:
    return _atomic_write_json(manifest_path(), data, mode=0o600)


def record_action(action: dict) -> bool:
    """Append a reversible action (the inverse of an adopt) to the manifest. Locked + atomic; returns ok."""
    with _file_lock(manifest_path()):
        data = read_manifest()
        data.setdefault("actions", []).append(action)
        return _write_manifest(data)


def remove_action(action_id: str, kind: str = "adopt-model") -> bool:
    """Drop a recorded action by id (locked + atomic; idempotent)."""
    with _file_lock(manifest_path()):
        data = read_manifest()
        data["actions"] = [a for a in data.get("actions", [])
                           if not (a.get("id") == action_id and a.get("kind") == kind)]
        return _write_manifest(data)


def manifest_actions() -> list[dict]:
    return read_manifest().get("actions", [])


def adopt_candidate(reg: dict, ref: str, *, name: str | None = None, modality: str = "text",
                    why: str | None = None, registry_path: Path | None = None,
                    pol: dict | None = None, affirmed: bool = False) -> dict:
    """Adopt a PRESENT, PERMITTED ollama ref into the registry, reversibly (ADR-0049 Phase 1). Re-validates
    server-side — denylist FIRST, then policy, then present-check — so a stale client can't slip a bad ref
    past the gate. NEVER pulls (phase-1). The inverse is recorded BEFORE the registry write (and rolled
    back if the write fails) so we never leave a registry entry with no way to Revert it."""
    pol = pol if pol is not None else policy.load_policy()
    registry_path = Path(registry_path) if registry_path else REGISTRY
    ref = (ref or "").strip()
    if not ref:
        return {"ok": False, "reason": "no-ref"}
    if policy.is_denied_ref(ref):                              # 1 — the red line, first + unconditional
        return {"ok": False, "reason": "safety-denied"}
    existing = registry_match(reg, ref)
    if existing:
        return {"ok": True, "skipped": "already-registered", "id": existing.get("id")}
    ok, reason = policy.permits(ref, in_registry=False, family=policy.derive_family(ref), pol=pol)
    if not ok:                                                # 2 — the family/curation policy
        return {"ok": False, "reason": reason}
    if policy.requires_mature_affirm(ref, pol) and not (affirmed or policy.is_affirmed(pol)):
        return {"ok": False, "reason": "needs-affirm"}
    if not _ollama_has(ref):                                  # 3 — present-check (no arbitrary pull, phase-1)
        return {"ok": False, "reason": "not-present"}
    entry = _new_model_entry(ref, name=name, modality=modality, why=why)
    # inverse FIRST: an orphan inverse (action with no entry) is harmless — Revert is idempotent — but an
    # orphan entry (no inverse) would be un-revertable. So record, then write, and roll back on write failure.
    action = {"kind": "adopt-model", "id": entry["id"], "ref": ref,
              "registry": str(registry_path), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not record_action(action):
        return {"ok": False, "reason": "write-failed"}
    if not add_registry_model(registry_path, entry):
        remove_action(entry["id"])                            # roll back the inverse — no orphan ledger row
        return {"ok": False, "reason": "write-failed"}
    return {"ok": True, "id": entry["id"], "family": entry["family"]}


def revert_action(action_id: str) -> dict:
    """Revert an adopt by id: remove the registry entry, THEN drop the manifest action. Idempotent — a
    leftover action with no entry (if the second step failed) is harmlessly re-revertable."""
    act = next((a for a in manifest_actions()
                if a.get("id") == action_id and a.get("kind") == "adopt-model"), None)
    if not act:
        return {"ok": False, "reason": "no-such-action"}
    if not remove_registry_model(Path(act.get("registry") or REGISTRY), action_id):
        return {"ok": False, "reason": "write-failed"}
    remove_action(action_id)
    return {"ok": True, "id": action_id}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
_GLYPH = {"have": "✓", "partial": "◐", "fetch": "↓", "unknown": "?", "skipped-mature": "·"}


def _cmd_detect(reg: dict) -> int:
    print("AgentOS model detection — brownfield report (nothing is changed)\n")
    hw = detect_hardware()
    print("RUNTIME")
    print(f"  {'✓ installed' if comfyui_present() else '✗ missing  '} ComfyUI"
          f"  ({comfy_root()}) — needed for image + video")
    if hw["vram_gb"]:
        print(f"  GPU: {hw['vram_gb']} GB VRAM · {hw['ram_gb']} GB system RAM")
    print()
    by_mod: dict[str, list] = {}
    for m in models(reg):
        if m.get("modality") == "selector" or m.get("runtime") == "selector":
            continue                                          # not a downloadable artifact
        by_mod.setdefault(m.get("modality", "other"), []).append(m)
    for mod, ms in by_mod.items():
        print(f"{mod.upper()}")
        for m in ms:
            st = model_status(m)
            g = _GLYPH.get(st["state"], "?")
            extra = f"  ({st['have']}/{st['need']} files)" if st["need"] > 1 else ""
            rate = " [mature]" if m.get("rating") == "mature" else ""
            print(f"  {g} {st['state']:8} {m['id']:18}{rate}{extra}")
        print()
    return 0


def _cmd_bundles(reg: dict, full: bool = False) -> int:
    hw = None if full else detect_hardware()
    print("Curated bundles (a bundle is just a query over the registry):\n")
    for b in bundles(reg):
        plan = plan_bundle(reg, b, include_mature=(b.get("rating") == "mature"), hw=hw)
        have = sum(1 for r in plan["rows"] if r["state"] == "have")
        defer = (f" · defers {', '.join(d['id'] for d in plan['deferred'])} (won't fit this GPU)"
                 if plan.get("deferred") else "")
        print(f"  {b['id']:18} {b.get('modality',''):6} {b.get('rating','sfw'):7} "
              f"{have}/{len(plan['rows'])} present · ~{plan['approx_gb']}GB to fetch{defer}")
    return 0


def _cmd_plan(reg: dict, bid: str, mature: bool, full: bool = False) -> int:
    b = find_bundle(reg, bid)
    if not b:
        print(f"unknown bundle: {bid} (see `bundles`)", file=sys.stderr)
        return 2
    hw = None if full else detect_hardware()
    plan = plan_bundle(reg, b, include_mature=mature or b.get("rating") == "mature", hw=hw)
    print(f"Plan for bundle '{bid}':\n")
    for r in plan["rows"]:
        print(f"  {_GLYPH.get(r['state'],'?')} {r['state']:13} {r['id']}")
    print(f"\n  gap: {len(plan['gap'])} file(s), ~{plan['approx_gb']} GB")
    if plan.get("deferred"):
        print(f"  ↑ deferred — won't fit {hw.get('vram_gb')} GB (re-run with --full to fetch anyway): "
              f"{', '.join(d['id'] for d in plan['deferred'])}")
    if plan["needs_auth"]:
        print(f"  needs account/token: {', '.join(plan['needs_auth'])} "
              f"(store with: setup.py creds set <svc>)")
    if plan["denied"]:
        print(f"  ⚠ {len(plan['denied'])} artifact(s) on the safety denylist — refused")
    return 0


def _cmd_fetch(reg: dict, bid: str, mature: bool, yes: bool, full: bool = False) -> int:
    b = find_bundle(reg, bid)
    if not b:
        print(f"unknown bundle: {bid}", file=sys.stderr)
        return 2
    is_mature = mature or b.get("rating") == "mature"
    if is_mature and not yes:
        print("This bundle includes MATURE (18+) models. Re-run with --yes to affirm you are an "
              "adult fetching adult models under your own accounts. No minors / no real-person "
              "likenesses (enforced denylist).", file=sys.stderr)
        return 3
    hw = None if full else detect_hardware()
    plan = plan_bundle(reg, b, include_mature=is_mature, hw=hw)
    if plan.get("deferred"):
        print(f"  ↑ deferring (won't fit {hw.get('vram_gb')} GB VRAM): "
              f"{', '.join(d['id'] for d in plan['deferred'])} — re-run with --full to fetch anyway.")
    if not plan["gap"]:
        print(f"✓ '{bid}' is already fully present — nothing to fetch.")
        return 0
    tokens = {a: keyring_get(a) for a in plan["needs_auth"]}
    missing_tok = [a for a, t in tokens.items() if not t]
    if missing_tok:
        print(f"! missing token(s): {', '.join(missing_tok)} — those models will be skipped. "
              f"Store with: setup.py creds set <svc>")
    print(f"→ fetching {len(plan['gap'])} file(s) for '{bid}' (~{plan['approx_gb']} GB)…\n")
    results = fetch_plan(plan, tokens=tokens)
    ok = sum(1 for r in results if r.get("ok"))
    skipped = [r for r in results if r.get("skipped") in ("needs-token", "manual", "denied")]
    for r in results:
        if not r.get("ok"):
            print(f"  ! {r.get('artifact')}: {r.get('skipped','failed')} — {r.get('reason','')}")
    print(f"\n{'✓' if not skipped else '◐'} {ok}/{len(results)} fetched"
          + (f", {len(skipped)} skipped (need token / manual)" if skipped else ""))
    return 0 if ok or not plan["gap"] else 1


def _cmd_creds(svc: str, action: str) -> int:
    if svc not in ("hf", "huggingface", "civitai"):
        print("service must be: hf | civitai", file=sys.stderr)
        return 2
    svc = "huggingface" if svc == "hf" else svc
    if action == "clear":
        ok = keyring_clear(svc)
        print(f"{'✓ cleared' if ok else '! could not clear'} {svc} token")
        return 0 if ok else 1
    # set — read the token from stdin (never argv/echo)
    print(f"Paste your {svc} token (input hidden if a TTY), then Enter:", file=sys.stderr)
    try:
        import getpass
        tok = getpass.getpass("") if sys.stdin.isatty() else sys.stdin.readline().strip()
    except Exception:
        tok = sys.stdin.readline().strip()
    if not tok:
        print("no token given", file=sys.stderr)
        return 2
    ok = keyring_set(svc, tok)
    print(f"{'✓ stored in the OS keyring' if ok else '! keyring unavailable'} ({svc})")
    return 0 if ok else 1


def _cmd_comfyui(yes: bool) -> int:
    if comfyui_present():
        print(f"✓ ComfyUI already installed at {comfy_root()} — nothing to do.")
        return 0
    print(f"ComfyUI isn't installed. This clones it to {comfy_root()}, builds a uv venv, and installs "
          "PyTorch (cu124) + the core node packs — several GB, a few minutes.")
    if not yes:
        print("Re-run with --yes to install it.")
        return 3
    res = comfyui_setup()
    if res.get("ok"):
        print(f"✓ ComfyUI ready at {res.get('root')}")
        return 0
    print(f"! ComfyUI setup failed at: {' '.join(res.get('failed', []))}", file=sys.stderr)
    return 1


def _cmd_express(reg: dict, want_video: bool, mature: bool, yes: bool, full: bool = False) -> int:
    """Ordered for speed + leverage: TEXT first (fast, no account — then it writes your prompts),
    then the ComfyUI runtime, then IMAGE, then VIDEO. Hero models that don't fit this GPU are
    deferred per bundle (pass --full to fetch them anyway)."""
    print("Express setup — text first (it's quick, and the model then helps write your prompts).\n")
    print("① text"); _cmd_fetch(reg, "text", False, True, full)
    if not comfyui_present():
        print("\n② ComfyUI runtime (needed for image + video)")
        if yes:
            _cmd_comfyui(True)
        else:
            print("  re-run `express --yes` to install ComfyUI (~GB); stopping after text for now.")
            print(f"\n✦ Text is ready. Try it: ollama run {_text_model_present() or '<your model>'}")
            return 0
    if comfyui_present():
        print("\n③ image"); _cmd_fetch(reg, "image", False, True, full)
        if want_video:
            vb = "video-wan" if mature else "video-10eros"
            print(f"\n④ video ({vb})"); _cmd_fetch(reg, vb, mature, yes, full)
    print(f"\n✦ Your first prompt (written by your text model): \"{suggest_opening_prompt('image')}\"")
    print("  Open Lucid → http://127.0.0.1:8765 and make it.")
    return 0


def _cmd_research(modality: str) -> int:
    hw = detect_hardware()
    print(f"Researching the latest {modality} models for your {hw.get('vram_gb')} GB GPU "
          f"(this asks an agent to search the web — a moment)…\n")
    res = research_models(modality, hw)
    if not res.get("ok"):
        print(f"! {res.get('error')}", file=sys.stderr)
        return 1
    print(res["suggestions"])
    print("\n(These are suggestions — review them, then edit ../models/registry.json to adopt any.)")
    return 0


def _cmd_onboard(reg: dict) -> int:
    """The guided entry: show what's here, what's available, and how to get to a first result.
    (The rich web wizard is ADR-0044's later surface; this is the honest CLI front door.)"""
    _cmd_detect(reg)
    print("─" * 64)
    print("Curated bundles you can set up (only the gaps download — nothing already here):\n")
    _cmd_bundles(reg)
    print("\nNext (recommended order — text first: it's quick and then writes your prompts):")
    print("  setup.py express [video] [--mature] [--yes]  # text → ComfyUI → image → video, in order")
    print("  setup.py comfyui --yes                 # install the ComfyUI runtime (image/video need it)")
    print("  setup.py fetch <bundle> [--yes]        # one bundle's gap (--mature for the 18+ lane)")
    print("  setup.py creds set civitai             # store a free Civitai token (mature video)")
    print("  setup.py research [video]              # ask an agent for the latest models for your GPU")
    print("\nOr skip all this and run the wizard:  ./install.sh --onboard --web")
    return 0


def _cmd_policy(reg: dict) -> int:
    pol = policy.load_policy()
    print(f"Model policy (ADR-0049) — {policy.policy_path()}")
    print(f"  allow any Ollama model: {'yes' if pol['allow_any_ollama'] else 'no (curated only)'}")
    print(f"  family allowlist: {', '.join(pol['family_allow']) or '(empty = all families)'}")
    print(f"  family blocklist: {', '.join(pol['family_block']) or '(none)'}")
    print(f"  known families:   {', '.join(policy.KNOWN_FAMILIES)}")
    print("  precedence: safety-denylist > family_block > allow_any > family_allow")
    print(f"\n  live Hermes inference default: {hermes_current_default() or 'unknown'}  "
          "(config.yaml is the source of truth)")
    return 0


def _cmd_research_json(reg: dict, modality: str, out_path: str | None = None) -> int:
    res = research_candidates(modality, detect_hardware())
    if res.get("ok"):
        res["candidates"] = validate_candidates(reg, res.get("candidates", []))
    blob = json.dumps(res)
    if out_path:
        try:
            Path(out_path).write_text(blob)
        except Exception as e:
            print(f"! could not write {out_path}: {e}", file=sys.stderr)
            return 1
    else:
        print(blob)
    return 0 if res.get("ok") else 1


def _cmd_adopt(reg: dict, ref: str, yes_mature: bool) -> int:
    res = adopt_candidate(reg, ref, affirmed=yes_mature)
    if res.get("ok"):
        if res.get("skipped"):
            print(f"· {ref} is already registered ({res.get('id')}) — nothing to do.")
        else:
            print(f"✓ adopted {ref} as '{res['id']}' (family: {res['family']}). "
                  f"Revert with: setup.py revert {res['id']}")
        return 0
    reason = res.get("reason", "rejected")
    msg = {"safety-denied": "refused by the safety red line — cannot be overridden by any setting",
           "needs-affirm": "needs an 18+ affirmation (mature/uncensored, or allow-any is on) — re-run with --yes-mature",
           "not-present": "not pulled yet — `ollama pull` it first (phase-1 never pulls arbitrary refs)",
           }.get(reason, policy.REASON_TEXT.get(reason, reason))
    print(f"✗ not adopted: {msg}", file=sys.stderr)
    return 1


def _cmd_revert(action_id: str) -> int:
    res = revert_action(action_id)
    print(f"{'✓ reverted' if res.get('ok') else '! could not revert'} {action_id}"
          + ("" if res.get("ok") else f" ({res.get('reason')})"))
    return 0 if res.get("ok") else 1


def main(argv: list[str]) -> int:
    if not argv:
        argv = ["onboard"]
    cmd, rest = argv[0], argv[1:]
    mature = "--mature" in rest
    yes = "--yes" in rest or "-y" in rest
    full = "--full" in rest                          # opt out of the GPU-aware downselect (fetch every model)
    yes_mature = "--yes-mature" in rest              # ADR-0049: affirm 18+ for a mature/uncensored adopt
    rest2 = list(rest)
    out_path = None
    if "--out" in rest2:                             # ADR-0049: research-json writes its JSON array here
        i = rest2.index("--out")
        out_path = rest2[i + 1] if i + 1 < len(rest2) else None
        del rest2[i:i + 2]
    pos = [a for a in rest2 if not a.startswith("-")]
    reg = load_registry()
    if cmd == "onboard":
        return _cmd_onboard(reg)
    if cmd == "detect":
        return _cmd_detect(reg)
    if cmd == "comfyui":
        return _cmd_comfyui(yes)
    if cmd == "express":
        return _cmd_express(reg, want_video="video" in pos, mature=mature, yes=yes, full=full)
    if cmd == "research":
        return _cmd_research(pos[0] if pos else "video")
    if cmd == "bundles":
        return _cmd_bundles(reg, full)
    if cmd == "plan":
        return _cmd_plan(reg, pos[0] if pos else "", mature, full)
    if cmd == "fetch":
        return _cmd_fetch(reg, pos[0] if pos else "", mature, yes, full)
    if cmd == "creds":
        return _cmd_creds(pos[1] if len(pos) > 1 else (pos[0] if pos else ""),
                          pos[0] if pos and pos[0] in ("set", "clear") else "set")
    if cmd == "policy":
        return _cmd_policy(reg)
    if cmd == "research-json":
        return _cmd_research_json(reg, pos[0] if pos and pos[0] in ("text", "image", "video") else "text", out_path)
    if cmd == "adopt":
        return _cmd_adopt(reg, pos[0] if pos else "", yes_mature)
    if cmd == "revert":
        return _cmd_revert(pos[0] if pos else "")
    if cmd == "hermes-default":
        print(hermes_current_default() or "unknown")
        return 0
    print(__doc__)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
