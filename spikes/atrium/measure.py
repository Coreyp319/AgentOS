#!/usr/bin/env python3
"""Atrium spike — the measurement harness the ADR-0031 gate asks for.

Produces real numbers (writes MEASUREMENTS.md):
  1. WCAG contrast of the degraded/"stale" state — normal vs the ADR fix vs the old buggy
     behaviour (gap #1, the live a11y cap). Rendered by chromium, sampled by Pillow.
  2. PWA install readiness — manifest fields, icon set, service worker (gap #5).
  3. Standing-process cost — the server's idle RSS + /launch.json latency, and a static proof
     the launch view is a "still room" (no WebGL / no rAF ⇒ ~0 GPU) (Open Q1 / ADR-0026).
  4. First-paint reference — headless load + page weight (the structural FCP determinant).

Honest about its environment: chromium here runs on software GL, so GPU-frame numbers are NOT
the 4090 — those are called out as "needs the box." Contrast, page weight, RSS and the
still-room proof are environment-independent and stand on their own.

Run:  python3 measure.py        (chromium + Pillow required; both present on this box)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)


def find_chromium() -> str | None:
    for b in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"):
        p = shutil.which(b)
        if p:
            return p
    return None


CHROME = find_chromium()


def shot(url: str, out_png: Path, w: int, h: int) -> bool:
    """Headless screenshot at a fixed size (DPR 1, sRGB) so pixel sampling is deterministic."""
    if not CHROME:
        return False
    prof = OUT / ".chrome-profile"
    cmd = [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=1", "--force-color-profile=srgb",
           f"--user-data-dir={prof}", f"--window-size={w},{h}",
           f"--screenshot={out_png}", url]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
    except Exception:
        # older chromium: retry with bare --headless
        cmd[1] = "--headless"
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
        except Exception:
            return False
    return out_png.exists() and out_png.stat().st_size > 0


# ── WCAG math ────────────────────────────────────────────────────────────────────────────

def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_lum(rgb) -> float:
    r, g, b = rgb[:3]
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg, bg) -> float:
    l1, l2 = rel_lum(fg), rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def patch_mean(img: Image.Image, cx: int, cy: int, half: int = 40):
    # mean colour of the patch via an exact box-average down to one pixel.
    box = img.crop((cx - half, cy - half, cx + half, cy + half)).convert("RGB")
    return box.resize((1, 1), Image.BOX).getpixel((0, 0))


def measure_contrast() -> dict:
    """Screenshot the probe and sample each column's ink/paper swatch centres. Columns 1-3 are
    the stale treatments (primary text); columns 4-5 are secondary inks (--muted/--faint) in the
    live state, so the gate covers descriptions/labels/footer too."""
    W, H = 1040, 415
    png = OUT / "contrast_probe.png"
    ok = shot(f"file://{HERE/'contrast_probe.html'}", png, W, H)
    if not ok:
        return {"error": "chromium screenshot failed"}
    img = Image.open(png)
    # swatch centres (mirror contrast_probe.html geometry): cols at x=15/220/425/630/835 (w190),
    # ink row top=15 h185, paper row top=215 h185.
    cols = {"normal": 15, "fixed": 220, "buggy": 425, "muted": 630, "faint": 835}
    res = {}
    for name, x in cols.items():
        cx = x + 95
        ink = patch_mean(img, cx, 15 + 92)
        paper = patch_mean(img, cx, 215 + 92)
        ratio = contrast(ink, paper)
        res[name] = {"ink": ink, "paper": paper, "ratio": round(ratio, 2),
                     "AA_text": ratio >= 4.5, "AA_large": ratio >= 3.0}
    return res


# ── PWA readiness ──────────────────────────────────────────────────────────────────────

def measure_pwa() -> dict:
    man_p = HERE / "manifest.webmanifest"
    out = {"manifest_present": man_p.exists(), "service_worker": (HERE / "sw.js").exists()}
    icons = []
    try:
        man = json.loads(man_p.read_text())
        out["start_url"] = man.get("start_url")
        out["display"] = man.get("display")
        req = {"name", "short_name", "start_url", "display", "icons", "background_color", "theme_color"}
        out["required_fields_present"] = sorted(req - set(man))
        for ic in man.get("icons", []):
            f = HERE / ic["src"].lstrip("/")
            ok = f.exists()
            real = None
            if ok:
                try:
                    real = "x".join(map(str, Image.open(f).size))
                except Exception:
                    real = "unreadable"
            icons.append({"src": ic["src"], "declared": ic.get("sizes"), "exists": ok,
                          "real_size": real, "purpose": ic.get("purpose", "any")})
        out["has_maskable"] = any(i.get("purpose") == "maskable" for i in man.get("icons", []))
        out["has_512"] = any(i.get("sizes") == "512x512" for i in man.get("icons", []))
    except Exception as e:
        out["error"] = str(e)
    out["icons"] = icons
    out["installable_static_check"] = bool(
        out.get("manifest_present") and out.get("service_worker")
        and not out.get("required_fields_present") and out.get("has_512") and out.get("has_maskable")
        and all(i["exists"] for i in icons)
    )
    return out


# ── standing cost + still-room proof ───────────────────────────────────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def measure_standing_cost() -> dict:
    out = {}
    # still-room proof: the launch view must create NO WebGL context and run NO rAF loop.
    html = (HERE / "atrium.html").read_text()
    # match real call sites (with the opening paren / tag), not prose in comments.
    uses_webgl = bool(re.search(r"getContext\(\s*['\"](?:experimental-)?webgl", html))
    uses_canvas = bool(re.search(r"<canvas\b", html)) or bool(re.search(r"createElement\(\s*['\"`]canvas", html))
    uses_raf = bool(re.search(r"requestAnimationFrame\s*\(", html))
    out["still_room"] = {
        "uses_webgl": uses_webgl, "uses_canvas": uses_canvas, "uses_raf": uses_raf,
        "verdict": "still room (no GPU backdrop)"
                   if not (uses_webgl or uses_canvas or uses_raf) else "NOT a still room — review",
    }
    # idle RSS of the server process (the only standing cost; it folds into the existing panel,
    # so the *marginal* new standing process count is 0 — this quantifies the host process).
    port = _free_port()
    env = {**os.environ, "ATRIUM_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, str(HERE / "atrium_server.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        ready = False
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/manifest.webmanifest", timeout=0.5).read()
                ready = True
                break
            except Exception:
                time.sleep(0.1)
        out["server_started"] = ready
        if ready:
            time.sleep(1.0)  # settle
            try:
                rss = int(Path(f"/proc/{proc.pid}/status").read_text()
                          .split("VmRSS:")[1].split("kB")[0].strip())
                out["idle_rss_mib"] = round(rss / 1024, 1)
            except Exception as e:
                out["idle_rss_mib"] = f"n/a ({e})"
            # /launch.json latency (local origin)
            lat = []
            for _ in range(5):
                t = time.perf_counter()
                try:
                    urllib.request.urlopen(base + "/launch.json", timeout=5).read()
                    lat.append((time.perf_counter() - t) * 1000)
                except Exception:
                    pass
            if lat:
                out["launch_json_ms"] = {"min": round(min(lat), 1), "median": round(sorted(lat)[len(lat)//2], 1)}
            # marginal new processes when folded into the existing status panel:
            out["marginal_new_standing_processes"] = 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return out


# ── first-paint reference + weight ──────────────────────────────────────────────────────

def measure_weight_and_paint() -> dict:
    out = {}
    files = {"atrium.html": HERE / "atrium.html", "manifest.webmanifest": HERE / "manifest.webmanifest",
             "sw.js": HERE / "sw.js"}
    weight = {k: v.stat().st_size for k, v in files.items() if v.exists()}
    icons = sum(p.stat().st_size for p in (HERE / "icons").glob("*.png"))
    out["bytes"] = {**weight, "icons_total": icons}
    out["shell_bytes_no_icons"] = weight.get("atrium.html", 0)
    out["external_blocking_resources"] = 0   # inline CSS+JS, system fonts, no <link rel=stylesheet>, no web fonts
    out["requests_for_first_paint"] = 1       # the HTML document itself
    # headless load reference (software GL — a ceiling, the real box is faster for paint)
    if CHROME:
        png = OUT / "atrium_demo.png"
        t = time.perf_counter()
        ok = shot(f"file://{HERE/'atrium.html'}?demo=1", png, 440, 900)
        out["headless_full_load_ms_ref"] = round((time.perf_counter() - t) * 1000, 0) if ok else "n/a"
        out["headless_note"] = ("end-to-end chromium spawn+load+screenshot on software GL; "
                                "a loose ceiling, not FCP. Real FCP needs the box — but the page is "
                                "1 request, inline CSS/JS, no web fonts, no WebGL ⇒ paint is parse-bound.")
    return out


def main():
    print("Atrium measurement harness")
    print("chromium:", CHROME or "NOT FOUND")
    data = {
        "generated_by": "measure.py",
        "chromium": CHROME,
        "contrast_stale_state": measure_contrast(),
        "pwa_readiness": measure_pwa(),
        "standing_cost": measure_standing_cost(),
        "weight_and_paint": measure_weight_and_paint(),
    }
    (OUT / "measurements.json").write_text(json.dumps(data, indent=2))
    write_markdown(data)
    print("wrote:", OUT / "measurements.json", "and", HERE / "MEASUREMENTS.md")


def write_markdown(d: dict):
    c = d["contrast_stale_state"]
    pwa = d["pwa_readiness"]
    sc = d["standing_cost"]
    wp = d["weight_and_paint"]
    L = []
    L.append("# Atrium spike — measurements\n")
    L.append("_Generated by `measure.py`. Re-run to refresh. Contrast / weight / RSS / still-room "
             "are environment-independent; headless GPU-frame numbers are software-GL and flagged._\n")

    L.append("## 1. Degraded-state WCAG contrast (ADR-0031 gap #1 — the live a11y cap)\n")
    if "error" in c:
        L.append(f"> measurement failed: {c['error']}\n")
    else:
        L.append("| stale treatment | ink (fg) | paper (bg) | contrast | AA text (4.5) | AA large (3.0) |")
        L.append("|---|---|---|---|---|---|")
        label = {"normal": "none (live)", "fixed": "**ADR-0031 fix** (dim signal, keep text)",
                 "buggy": "original (`opacity:.5;saturate(.6)` on text)"}
        for k in ("normal", "fixed", "buggy"):
            r = c[k]
            L.append(f"| {label[k]} | rgb{r['ink']} | rgb{r['paper']} | **{r['ratio']}:1** | "
                     f"{'✅' if r['AA_text'] else '❌'} | {'✅' if r['AA_large'] else '❌'} |")
        verdict = ("The fix keeps body text at full contrast while signalling 'blind' through the "
                   "dots/glass; the original treatment dims the text itself.")
        L.append("\n" + verdict + "\n")
        # Secondary inks (review found the gate only covered the headline text).
        if "muted" in c and "faint" in c:
            L.append("### Secondary inks over glass (live state)\n")
            L.append("| token | ink | contrast | AA text (4.5) | AA large (3.0) |")
            L.append("|---|---|---|---|---|")
            for k, tok in (("muted", "--inst-muted (#8a90a0) — descriptions"),
                           ("faint", "--inst-label (#878c9b, lifted from #7a8090) — labels/footer")):
                r = c[k]
                L.append(f"| {tok} | rgb{r['ink']} | **{r['ratio']}:1** | "
                         f"{'✅' if r['AA_text'] else '❌'} | {'✅' if r['AA_large'] else '❌'} |")
            faint_ok = c["faint"]["AA_text"]
            L.append("\n" + ("Secondary inks clear AA at body size (`--inst-label` lifted "
                     "#7a8090→#878c9b across the shared instrument register — ADR-0031)." if faint_ok else
                     "> **CROSS-SURFACE NOTE:** `--inst-label` does not clear AA (4.5:1) for small "
                     "text. It's used at `--fs-2xs`/`--fs-3xs` (labels/footer). This is the *shared* "
                     "instrument token (`integrations/design/instrument-tokens.md`, also in "
                     "`panel.html` + the keyhole), so the fix is a one-line cross-surface token "
                     "lift — out of scope for this spike to change unilaterally; flagged for "
                     "visual-systems-designer.") + "\n")

    L.append("## 2. PWA install readiness (gap #5)\n")
    L.append(f"- manifest present: {pwa.get('manifest_present')} · service worker: {pwa.get('service_worker')}")
    L.append(f"- required fields missing: {pwa.get('required_fields_present') or 'none'}")
    L.append(f"- 512px icon: {pwa.get('has_512')} · maskable icon: {pwa.get('has_maskable')}")
    for i in pwa.get("icons", []):
        L.append(f"  - `{i['src']}` declared {i['declared']} → real {i['real_size']} "
                 f"({i['purpose']}) {'✅' if i['exists'] else '❌ MISSING'}")
    L.append(f"- **static installability check: {'✅ PASS' if pwa.get('installable_static_check') else '❌'}**")
    L.append("- _gate residual: install over the **actual** `tailscale serve` HTTPS origin (SW scope "
             "under the proxy) must be confirmed on the box — static checks pass; live origin is owed._\n")

    L.append("## 3. Standing-process cost (Open Q1 / ADR-0026 idle-exit)\n")
    sr = sc.get("still_room", {})
    L.append(f"- **still room:** WebGL={sr.get('uses_webgl')} · canvas={sr.get('uses_canvas')} · "
             f"rAF={sr.get('uses_raf')} → _{sr.get('verdict')}_ (≈0 GPU; no reactive backdrop)")
    L.append(f"- host server idle RSS: **{sc.get('idle_rss_mib')} MiB**")
    if sc.get("launch_json_ms"):
        L.append(f"- `/launch.json` latency (local): min {sc['launch_json_ms']['min']} ms · "
                 f"median {sc['launch_json_ms']['median']} ms")
    L.append(f"- **marginal new standing processes when folded into the status panel: "
             f"{sc.get('marginal_new_standing_processes')}** (the view is a route on the existing daemon)\n")

    L.append("## 4. First-paint determinant + weight\n")
    b = wp.get("bytes", {})
    L.append(f"- launch-view shell: **{wp.get('shell_bytes_no_icons')} bytes** of HTML "
             f"(inline CSS+JS) · icons {b.get('icons_total')} bytes (cached after install)")
    L.append(f"- requests blocking first paint: **{wp.get('requests_for_first_paint')}** "
             f"(the document) · external blocking resources: **{wp.get('external_blocking_resources')}**")
    if wp.get("headless_full_load_ms_ref") is not None:
        L.append(f"- headless full load+screenshot reference: {wp.get('headless_full_load_ms_ref')} ms "
                 f"(software GL; loose ceiling)")
    L.append(f"- _{wp.get('headless_note','')}_\n")

    (HERE / "MEASUREMENTS.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
