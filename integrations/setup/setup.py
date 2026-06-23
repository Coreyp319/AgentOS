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
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")     # strip terminal control (ollama leaks cursor moves)

HERE = Path(__file__).resolve().parent
REGISTRY = HERE.parent / "models" / "registry.json"

# Hard exclusions — CSAM / non-consensual-real-likeness repos are categorically off-limits, never
# fetched regardless of bundle or token (the local-video-gen skill's denylist; ADR-0008 red line).
DENYLIST = ("deadman44",)

HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{file}"
CIVITAI_DL = "https://civitai.com/api/download/models/{version_id}"
_OLLAMA_LIST_CACHE: list[str] | None = None

# ComfyUI runtime bootstrap (image/video need it; text does not — that's why text goes first).
COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI"
TORCH_INDEX = "https://download.pytorch.org/whl/cu124"
# The core custom-node packs the shipped dream workflows need; the long tail is handled by
# ComfyUI-Manager. Continue-on-failure — a missing pack degrades one workflow, not the install.
NODE_PACKS = [
    ("ComfyUI-Manager", "https://github.com/ltdrdata/ComfyUI-Manager"),
    ("ComfyUI-GGUF", "https://github.com/city96/ComfyUI-GGUF"),
    ("ComfyUI-KJNodes", "https://github.com/kijai/ComfyUI-KJNodes"),
    ("ComfyUI-LTXVideo", "https://github.com/Lightricks/ComfyUI-LTXVideo"),
]
_DEFAULT_PROMPT = {"image": "a quiet harbor at dawn, soft mist on still water",
                   "video": "a calm forest stream in morning light, gentle drifting motion"}


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


def comfyui_setup(dry: bool = False, run=subprocess.run) -> dict:
    """Bootstrap the ComfyUI runtime if absent: clone, make the uv venv, install torch (cu124) +
    requirements, then the core node packs. Idempotent (skip-if-present); continue-on-failure for
    node packs. Returns a result dict; `steps` when dry."""
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
    steps.append(pip + ["torch", "torchvision", "torchaudio", "--index-url", TORCH_INDEX])
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
def detect_hardware(run=subprocess.run) -> dict:
    vram_total = vram_free = 0
    if shutil.which("nvidia-smi"):
        try:
            r = run(["nvidia-smi", "--query-gpu=memory.total,memory.free",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8, check=False)
            ln = (r.stdout or "").strip().splitlines()
            if ln:
                t, f = ln[0].split(",")
                vram_total, vram_free = int(t.strip()), int(f.strip())   # MiB
        except Exception:
            pass
    ram_gb = 0.0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) / 1024 / 1024              # kB → GiB
                break
    except Exception:
        pass
    return {"vram_mib": vram_total, "vram_free_mib": vram_free,
            "vram_gb": round(vram_total / 1024, 1), "ram_gb": round(ram_gb, 1)}


def bundle_fit(reg: dict, bundle: dict, hw: dict) -> str:
    """Does the bundle's heaviest model fit this GPU? 'fits' | 'tight' | 'too-big' | 'unknown'.
    Heuristic: a model's loaded VRAM footprint ≈ its size_gb (GGUF/fp8 weights dominate)."""
    vram = hw.get("vram_gb", 0)
    if not vram:
        return "unknown"
    biggest = max((float(m.get("size_gb", 0) or 0)
                   for mid in bundle.get("models", []) if (m := find_model(reg, mid))), default=0.0)
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
        f"{modality}-generation model(s) for a single GPU with {hw.get('vram_gb')} GB VRAM and "
        f"{hw.get('ram_gb')} GB system RAM, runnable locally via "
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
    blob = " ".join(str(art.get(k, "")) for k in ("repo", "ref", "version_id", "file", "dest")).lower()
    return any(d in blob for d in DENYLIST)


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
def resolve_bundle(reg: dict, bundle: dict) -> list[dict]:
    return [m for mid in bundle.get("models", []) if (m := find_model(reg, mid))]


def plan_bundle(reg: dict, bundle: dict, include_mature: bool = False) -> dict:
    """Reconcile a bundle against the box: which models are present, which need fetching, what
    auth/size that implies. The mature lane is excluded unless include_mature."""
    rows, gap_arts, need_auth, denied = [], [], set(), []
    total_gb = 0.0
    for m in resolve_bundle(reg, bundle):
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
            "approx_gb": round(total_gb, 1), "denied": denied}


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


def _cmd_bundles(reg: dict) -> int:
    print("Curated bundles (a bundle is just a query over the registry):\n")
    for b in bundles(reg):
        plan = plan_bundle(reg, b, include_mature=(b.get("rating") == "mature"))
        have = sum(1 for r in plan["rows"] if r["state"] == "have")
        print(f"  {b['id']:18} {b.get('modality',''):6} {b.get('rating','sfw'):7} "
              f"{have}/{len(plan['rows'])} present · ~{plan['approx_gb']}GB to fetch")
    return 0


def _cmd_plan(reg: dict, bid: str, mature: bool) -> int:
    b = find_bundle(reg, bid)
    if not b:
        print(f"unknown bundle: {bid} (see `bundles`)", file=sys.stderr)
        return 2
    plan = plan_bundle(reg, b, include_mature=mature or b.get("rating") == "mature")
    print(f"Plan for bundle '{bid}':\n")
    for r in plan["rows"]:
        print(f"  {_GLYPH.get(r['state'],'?')} {r['state']:13} {r['id']}")
    print(f"\n  gap: {len(plan['gap'])} file(s), ~{plan['approx_gb']} GB")
    if plan["needs_auth"]:
        print(f"  needs account/token: {', '.join(plan['needs_auth'])} "
              f"(store with: setup.py creds set <svc>)")
    if plan["denied"]:
        print(f"  ⚠ {len(plan['denied'])} artifact(s) on the safety denylist — refused")
    return 0


def _cmd_fetch(reg: dict, bid: str, mature: bool, yes: bool) -> int:
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
    plan = plan_bundle(reg, b, include_mature=is_mature)
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


def _cmd_express(reg: dict, want_video: bool, mature: bool, yes: bool) -> int:
    """Ordered for speed + leverage: TEXT first (fast, no account — then it writes your prompts),
    then the ComfyUI runtime, then IMAGE, then VIDEO."""
    print("Express setup — text first (it's quick, and the model then helps write your prompts).\n")
    print("① text"); _cmd_fetch(reg, "text", False, True)
    if not comfyui_present():
        print("\n② ComfyUI runtime (needed for image + video)")
        if yes:
            _cmd_comfyui(True)
        else:
            print("  re-run `express --yes` to install ComfyUI (~GB); stopping after text for now.")
            print(f"\n✦ Text is ready. Try it: ollama run {_text_model_present() or '<your model>'}")
            return 0
    if comfyui_present():
        print("\n③ image"); _cmd_fetch(reg, "image", False, True)
        if want_video:
            vb = "video-wan" if mature else "video-10eros"
            print(f"\n④ video ({vb})"); _cmd_fetch(reg, vb, mature, yes)
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


def main(argv: list[str]) -> int:
    if not argv:
        argv = ["onboard"]
    cmd, rest = argv[0], argv[1:]
    mature = "--mature" in rest
    yes = "--yes" in rest or "-y" in rest
    pos = [a for a in rest if not a.startswith("-")]
    reg = load_registry()
    if cmd == "onboard":
        return _cmd_onboard(reg)
    if cmd == "detect":
        return _cmd_detect(reg)
    if cmd == "comfyui":
        return _cmd_comfyui(yes)
    if cmd == "express":
        return _cmd_express(reg, want_video="video" in pos, mature=mature, yes=yes)
    if cmd == "research":
        return _cmd_research(pos[0] if pos else "video")
    if cmd == "bundles":
        return _cmd_bundles(reg)
    if cmd == "plan":
        return _cmd_plan(reg, pos[0] if pos else "", mature)
    if cmd == "fetch":
        return _cmd_fetch(reg, pos[0] if pos else "", mature, yes)
    if cmd == "creds":
        return _cmd_creds(pos[1] if len(pos) > 1 else (pos[0] if pos else ""),
                          pos[0] if pos and pos[0] in ("set", "clear") else "set")
    print(__doc__)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
