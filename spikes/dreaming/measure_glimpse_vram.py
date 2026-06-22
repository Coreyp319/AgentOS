#!/usr/bin/env python3
"""ADR-0023 §Tier-B / council gap #5 — the make-or-break measurement for the speculative
"glimpse" generator: can a min-length i2v render's VRAM be reclaimed promptly under the
substrate's kill-not-yield preempt, and does it actually fit anywhere near the dwell window?

This is the staged spike. It is DESIGNED to be run when the GPU is quiet (the faithful
SIGKILL test BOUNCES ComfyUI and so interrupts any live Lucid session). It answers four
numbers the council brief (docs/design/lucid-choice-moment-council-brief.md §3 Tier B) asked for:

  (1) PEAK VRAM a single glimpse render holds   (delta over baseline; weights dominate, ~15-17 GB)
  (2) ms to FREE that VRAM after a SIGKILL       (the kill-not-yield reclaim latency)
  (3) ComfyUI RELAUNCH-to-ready time             (the cold-start the lease pays on every preempt)
  (4) wall-time of the glimpse render itself     (does it fit the open-ended dwell? playback is 1-5s)

MODES
  default (measure):  NON-destructive. Submit one min-length glimpse, sample VRAM, report (1) + (4)
                      + a finished/interrupted note. Safe to run whenever the GPU has ~17 GB free.
  --kill-test --go:   DESTRUCTIVE. Mid-render, SIGKILL the ComfyUI process, measure (2), then relaunch
                      it from its captured /proc cmdline and measure (3). Requires BOTH flags; prints a
                      countdown first. If relaunch can't be done, it tells you how to restart by hand.

USAGE
  python3 measure_glimpse_vram.py                 # non-destructive footprint + timing
  python3 measure_glimpse_vram.py --kill-test --go   # + the SIGKILL reclaim + relaunch cycle
  [--length 17] [--steps 8] [--width 720] [--height 1280] [--engine wan]

It shells out to comfy_client.py run-template (no submission logic re-implemented) and watches
`nvidia-smi --query-gpu=memory.used` out of band.
"""
import argparse, os, signal, struct, subprocess, sys, threading, time, zlib
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1:8188")
COMFY_ROOT = os.environ.get("COMFY_ROOT", "/home/corey/ComfyUI")
COMFY_INPUT = os.path.join(COMFY_ROOT, "input")
# the verified non-distilled Wan 2.2 i2v graph lucid_engine uses by default (LUCID_WORKFLOW override honored)
WORKFLOW = os.environ.get("LUCID_WORKFLOW",
                          os.path.join(HERE, "workflows", "lucid-nolight-nsfw-i2v.api.json"))
SEED_IMG = "measure_glimpse_seed.png"
BASELINE_TOL = 250   # MiB: "freed" = back within this of baseline


def vram_used_mib():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, check=True).stdout
    return int(out.strip().splitlines()[0])


def comfy_pid():
    r = subprocess.run(["pgrep", "-f", "ComfyUI/main.py"], capture_output=True, text=True)
    pids = [int(x) for x in r.stdout.split()]
    return pids[0] if pids else None


def comfy_up():
    try:
        urllib.request.urlopen(f"http://{COMFY_HOST}/object_info", timeout=2)
        return True
    except Exception:
        return False


def write_seed_png(path, w=720, h=1280, rgb=(60, 48, 96)):
    def chunk(typ, data):
        c = typ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(png)


class VramSampler(threading.Thread):
    """Background VRAM sampler — records (t, used) at ~interval; exposes peak + a freed() check."""
    def __init__(self, interval=0.1):
        super().__init__(daemon=True)
        self.interval, self.samples, self._stop = interval, [], False

    def run(self):
        while not self._stop:
            try:
                self.samples.append((time.time(), vram_used_mib()))
            except Exception:
                pass
            time.sleep(self.interval)

    def stop(self):
        self._stop = True

    def peak(self):
        return max((u for _, u in self.samples), default=0)


def run_glimpse(args, prefix):
    """Launch comfy_client run-template as a subprocess (it submits + waits). Returns the Popen."""
    cmd = [
        sys.executable, os.path.join(HERE, "comfy_client.py"), "run-template", WORKFLOW,
        "--prompt", "the light folds inward and the hills breathe",
        "--set", f"LoadImage.image={SEED_IMG}",
        "--set", f"WanImageToVideo.length={args.length}",
        "--set", f"WanImageToVideo.width={args.width}",
        "--set", f"WanImageToVideo.height={args.height}",
        "--steps", str(args.steps), "--seed", "1",
        "--out-prefix", prefix, "--timeout", "1800",
    ]
    env = dict(os.environ, COMFY_HOST=COMFY_HOST)
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_until_rendering(sampler, baseline, proc, timeout=180):
    """Block until VRAM rises clearly above baseline (model loaded + sampling started) or the job ends."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False  # finished/failed before we caught it mid-render (a fast/cached run)
        cur = sampler.samples[-1][1] if sampler.samples else baseline
        if cur - baseline > 3000:  # >3 GB over baseline = weights are in
            return True
        time.sleep(0.2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--length", type=int, default=17)   # Wan 4k+1 minimum band
    ap.add_argument("--steps", type=int, default=8)      # reduced-step glimpse (vs 20 for a real beat)
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--kill-test", action="store_true", help="also SIGKILL mid-render + measure reclaim/relaunch (DESTRUCTIVE)")
    ap.add_argument("--go", action="store_true", help="required to actually SIGKILL ComfyUI")
    args = ap.parse_args()

    if not comfy_up():
        sys.exit(f"ComfyUI not reachable at {COMFY_HOST} — start it first.")
    pid = comfy_pid()
    print(f"ComfyUI pid={pid}  host={COMFY_HOST}")
    print(f"workflow={WORKFLOW}")

    # capture the launch cmdline + cwd NOW, before any kill, so we can relaunch faithfully
    cmdline, cwd = None, None
    if pid:
        try:
            cmdline = open(f"/proc/{pid}/cmdline", "rb").read().decode().split("\0")
            cmdline = [a for a in cmdline if a]
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except Exception as e:
            print(f"  (couldn't capture relaunch cmdline: {e})")

    write_seed_png(os.path.join(COMFY_INPUT, SEED_IMG), args.width, args.height)
    print(f"seed image -> {os.path.join(COMFY_INPUT, SEED_IMG)}")

    baseline = vram_used_mib()
    print(f"\nbaseline VRAM = {baseline} MiB  (whatever is resident now — note if ollama/a model is holding VRAM)")
    sampler = VramSampler(0.1); sampler.start()

    print(f"\n[1/2] submitting a {args.length}-frame / {args.steps}-step glimpse render…")
    t_submit = time.time()
    proc = run_glimpse(args, prefix="measure/glimpse")
    rendering = wait_until_rendering(sampler, baseline, proc)

    if not args.kill_test:
        # non-destructive: let it finish, report footprint + wall-time
        out, _ = proc.communicate()
        dt = time.time() - t_submit
        peak = sampler.peak(); sampler.stop()
        print((out or "").strip()[-500:])
        print("\n===== GLIMPSE FOOTPRINT (non-destructive) =====")
        print(f"  peak VRAM        : {peak} MiB   (delta over baseline = {peak - baseline} MiB)")
        print(f"  glimpse wall-time: {dt:.1f} s   (length={args.length}, steps={args.steps})")
        print(f"  baseline         : {baseline} MiB")
        print("\n  -> (1) footprint and (4) wall-time captured. Run with --kill-test --go for (2) reclaim + (3) relaunch.")
        print("  -> Read against the dwell: the dwell is open-ended, so wall-time only needs to beat the user's pause,")
        print("     NOT the 1-5s playback. The binding question is whether this footprint can co-reside (it usually can't).")
        return

    # ---- destructive kill-test ----
    if not args.go:
        sampler.stop()
        sys.exit("\n--kill-test needs --go too (it SIGKILLs ComfyUI and interrupts any live Lucid session).")
    if not rendering:
        sampler.stop()
        sys.exit("\nrender finished/failed before it was caught mid-flight — re-run (maybe raise --length/--steps).")

    print("\n[2/2] render is mid-flight. SIGKILL of ComfyUI in:")
    for s in (3, 2, 1):
        print(f"   {s}…"); time.sleep(1)
    peak = sampler.peak()
    print(f"  peak VRAM before kill = {peak} MiB (delta {peak - baseline})")
    os.kill(pid, signal.SIGKILL)
    try:
        proc.kill()
    except Exception:
        pass
    t_kill = time.time()

    freed_at = None
    while time.time() - t_kill < 30:
        cur = vram_used_mib()
        if cur - baseline <= BASELINE_TOL:
            freed_at = time.time(); break
        time.sleep(0.05)
    sampler.stop()
    free_ms = (freed_at - t_kill) * 1000 if freed_at else None
    print(f"\n  VRAM-free after SIGKILL: {('%.0f ms' % free_ms) if free_ms else 'NOT freed within 30 s (!)'}")

    # relaunch
    relaunch_ms = None
    if cmdline:
        print(f"\n  relaunching ComfyUI: {' '.join(cmdline[:4])} …  (cwd={cwd})")
        subprocess.Popen(cmdline, cwd=cwd or None,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        t_re = time.time()
        while time.time() - t_re < 180:
            if comfy_up():
                relaunch_ms = (time.time() - t_re) * 1000; break
            time.sleep(0.5)
        print(f"  relaunch-to-ready: {('%.0f ms' % relaunch_ms) if relaunch_ms else 'did NOT come back in 180 s — restart by hand'}")
    else:
        print("\n  (no captured cmdline — restart ComfyUI by hand; the live Lucid stack depends on it.)")

    print("\n===== KILL-TEST RESULTS =====")
    print(f"  (1) peak VRAM held   : {peak} MiB  (delta {peak - baseline})")
    print(f"  (2) VRAM-free latency: {('%.0f ms' % free_ms) if free_ms else 'FAILED'}")
    print(f"  (3) relaunch-to-ready: {('%.0f ms' % relaunch_ms) if relaunch_ms else 'FAILED/manual'}")
    print("\n  Verdict gate (council #5): a glimpse is only viable if its VRAM frees PROMPTLY on preempt AND")
    print("  the real beat's admission isn't stalled. A multi-second free or a slow relaunch => the speculative")
    print("  tier is infeasible-as-specified and the still-default IS the whole feature (a clean 9-10).")


if __name__ == "__main__":
    main()
