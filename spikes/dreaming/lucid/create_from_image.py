#!/usr/bin/env python3
"""Create Video from Image — the governed launcher behind the "Create" right-click surfaces.

Two desktop surfaces share this one entry point (ADR-0015/0016/0017):
  - the Dolphin ServiceMenu  (right-click an image file → Create → Create Video from Image[ (private)])
  - the browser WebExtension (right-click an image on a page → same, via a native-messaging host)

Both hand us ONE image (a local path, a file://, an http(s):// URL, or a data: URL) and an
optional --private flag. We do NOT generate directly — every clip goes through `lucid_linear`,
which means the VRAM lease (Spawn/confirm-evict/Release), the deterministic red-line prompt gate,
and the B2 seed-likeness guard. This launcher's whole job is to drive that path HONESTLY:

  * resource down (coordinator/ComfyUI) -> FAIL OPEN: a calm "skipping" notification, never an error
    dialog, never a forced GPU load.
  * the identity/likeness gate (B2) -> FAIL CLOSED: a real person needs explicit per-seed consent
    (a modal dialog, default Cancel); a possible minor is a hard, non-overridable block; "can't
    verify" (vision model down / unreadable image) refuses. We NEVER set LUCID_ALLOW_UNVETTED_SEED.
  * --private -> ADR-0016: ephemeral tmpfs session, not saved, not on the status hub, no wallpaper,
    auto-burned on logout; we reap crash-orphans on entry and seal the download temp.

A right-click consents to *trying* — it does not assert who is in the photo, so the third-party
likeness call stays B2's. Notifications name the action, never the source path (filenames are PII).

Usage:  create_from_image.py [--private] <image-path-or-url>
        (Real-person consent is captured in-process via a kdialog modal — never an argv flag.)
"""
import argparse
import base64
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lucid_linear as L   # noqa: E402  (lease + red-line gate + B2 — the governed path)
import lucid_b2 as B2      # noqa: E402  (seed-likeness verdict, so we can surface consent honestly)
import lucid_safety as S   # noqa: E402  (the deterministic prompt gate)
import lucid_store as ST   # noqa: E402  (private/ephemeral storage hygiene — ADR-0016)
import lucid_jobs as J     # noqa: E402  (visible queue for NON-private creations — the :8765 board)

APP = "AgentOS · Create"
ICON = "camera-video"
MAX_BYTES = 32 * 1024 * 1024            # refuse absurd downloads (a still frame, not a movie)
MAX_PIXELS = 24_000_000                 # ~6000x4000; matches lucid_web — both surfaces refuse alike
# A neutral, SFW motion prompt: this surface animates the image, it does not author a story (that is
# the interactive Lucid web loop). Still passes through S.gate_prompt like every other beat.
MOTION_PROMPT = ("the scene gently comes alive with subtle natural motion and a slow, calm "
                 "cinematic camera drift; gentle ambient light")


def notify(summary, body="", urgency="normal"):
    """Honest, calm desktop feedback. Never carries the source path/filename (PII)."""
    try:
        subprocess.Popen(["notify-send", "-a", APP, "-i", ICON, "-u", urgency, summary, body])
    except Exception:
        print(f"[create] {summary} — {body}", file=sys.stderr)


def coordinator_up():
    """Fast liveness probe for the lease daemon. Its absence is a FAIL-OPEN (resource) condition."""
    try:
        r = subprocess.run(
            ["busctl", "--user", "call", "org.agentos.Coordinator1", "/org/agentos/Coordinator1",
             "org.agentos.Coordinator1", "Status"], capture_output=True, text=True, timeout=4)
        return r.returncode == 0
    except Exception:
        return False


def _fetch_raw(arg):
    """Pull the source bytes (size-capped) from a path / file:// / data: / http(s):// argument.
    Returns raw bytes — NOT yet trusted; _clean_png does the validation + sanitization."""
    parsed = urllib.parse.urlparse(arg)
    scheme = parsed.scheme.lower()

    if scheme in ("", "file"):
        local = urllib.parse.unquote(parsed.path) if scheme == "file" else arg
        local = os.path.abspath(os.path.expanduser(local))
        if not os.path.isfile(local):
            raise ValueError("that file no longer exists")
        if os.path.getsize(local) > MAX_BYTES:
            raise ValueError("image too large")
        with open(local, "rb") as f:
            return f.read(MAX_BYTES + 1)

    if scheme == "data":                                       # data:[<mime>][;base64],<payload>
        meta, _, payload = arg[5:].partition(",")
        if "base64" not in meta:
            raise ValueError("unsupported inline image")
        raw = base64.b64decode(payload, validate=False)
        if len(raw) > MAX_BYTES:
            raise ValueError("image too large")
        return raw

    if scheme in ("http", "https"):
        # SSRF guard: a browser srcUrl is attacker-influenced. Refuse internal targets so a page
        # can't make us probe loopback/link-local/private services (ComfyUI :8188, Ollama :11434,
        # cloud metadata 169.254.169.254). Validation-gated already, but this closes the oracle.
        if _host_is_internal(parsed.hostname):
            raise ValueError("refusing an internal address")
        req = urllib.request.Request(arg, headers={"User-Agent": "AgentOS-Create/0.1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("image too large")
        return raw

    raise ValueError("unsupported image source")


def _host_is_internal(host):
    """True if `host` (or any address it resolves to) is loopback/link-local/private/reserved.
    Conservative + fail-closed: an unresolvable host counts as blocked."""
    import ipaddress
    import socket
    if not host:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if (ip.is_loopback or ip.is_link_local or ip.is_private
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _clean_png(raw, private):
    """Validate the bytes are a real image and re-encode to a clean PNG we own — strips EXIF/GPS
    (a user photo's metadata, a real leak we never carry into the seed or ComfyUI), guards
    decompression bombs, and caps dimensions. Mirrors lucid_web._decode_seed (ADR-0017). For
    --private the PNG lands in tmpfs at 0700 so a private seed never touches shared disk.

    Returns the temp PNG path; the caller unlinks it in a finally. Fail-closed: if Pillow is
    unavailable we cannot sanitize, so we refuse rather than pass an unsanitized seed through."""
    try:
        from PIL import Image
    except Exception:
        raise RuntimeError("__no_pillow__")   # run() turns this into an honest fail-closed refusal
    import io
    import warnings
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            Image.open(io.BytesIO(raw)).verify()         # raises if not a valid image
            img = Image.open(io.BytesIO(raw))            # re-open (verify leaves it unusable)
            w, h = img.size
            if w > 8192 or h > 8192 or w * h > MAX_PIXELS:
                raise ValueError(f"image dimensions too large ({w}x{h})")
            img = img.convert("RGB")
    except ValueError:
        raise
    except Exception:                                    # decode error / broken file / bomb band
        raise ValueError("that doesn't look like a usable image")
    scratch_dir = None
    if private:
        scratch_dir = os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()),
                                   "agentos", "create-priv")
        os.makedirs(scratch_dir, mode=0o700, exist_ok=True)
    fd, p = tempfile.mkstemp(prefix="seed_", suffix=".png", dir=scratch_dir)
    os.fchmod(fd, 0o600)
    os.close(fd)
    img.save(p, "PNG")                                   # no EXIF carried into the PNG
    return p


def consent_for_real_person(verdict):
    """A real, photographed person needs explicit per-seed consent — surfaced as a modal dialog,
    default Cancel. No kdialog (or user declines) => fail closed. We never infer consent from the
    right-click; the click consents to trying, not to who is depicted."""
    msg = ("This looks like a photo of a real person.\n\n"
           "Only continue if you are this person, or have the right to use this image, to make a "
           "video from it.")
    if not shutil.which("kdialog"):
        notify("Can't create this video", "A real person was detected and consent can't be "
               "confirmed here — not created.", "critical")
        return False
    r = subprocess.run(["kdialog", "--title", "Create Video — consent",
                        "--warningyesno", msg,
                        "--yes-label", "I have the right — continue",
                        "--no-label", "Cancel"])
    return r.returncode == 0


def _job(job_id, **fields):
    """Record progress on the visible :8765 board — NON-private creations only. job_id is None for
    a private creation (ADR-0016: never shown on the board), making every call a no-op. Best-effort."""
    if not job_id:
        return
    try:
        J.update(job_id, **fields)
    except Exception:
        pass


def gate_seed(local_path, pre_consent, job_id=None):
    """Run B2 and DECIDE, surfacing each outcome honestly. Returns True only if cleared to generate.

    Hard block (minor)        -> notify, refuse, no override.
    Real person               -> consent dialog (or --consent); default Cancel.
    Can't verify / model down  -> fail closed, refuse.
    Clear (no face / not real) -> proceed."""
    if os.environ.get("LUCID_ALLOW_UNVETTED_SEED") == "1":
        # The launcher itself never sets this; if the environment forces it (CI/no-vision-model),
        # respect it but make the bypass loud — it is NOT a shipping path.
        print("[create] WARNING: LUCID_ALLOW_UNVETTED_SEED=1 — B2 bypassed (test only)", file=sys.stderr)
        return True
    verdict = B2.check_seed(local_path)
    if verdict.ok:
        return True
    if verdict.requires_consent:
        if pre_consent or consent_for_real_person(verdict):
            return True
        _job(job_id, status="blocked", detail="consent declined")
        notify("Cancelled", "No video was created.")
        return False
    if verdict.flags.get("possibly_minor"):
        _job(job_id, status="blocked", detail="image not allowed (possible minor)")
        notify("Can't create this video",
               "This image may show a minor, so it can't be used. This is a safety limit and "
               "can't be turned off.", "critical")
        return False
    _job(job_id, status="blocked", detail="couldn't verify the image safely")
    notify("Couldn't check this image safely",
           "The on-device image check isn't available right now, so nothing was created. "
           "Try again in a moment.")
    return False


def run(arg, private, pre_consent=False):
    # ADR-0016 hygiene: sweep any private clip/frame orphans from a prior crash/logout BEFORE we start.
    if private:
        try:
            ST.reap_orphans()
        except Exception as e:
            print(f"[create] orphan reap skipped: {e}", file=sys.stderr)

    # One session id, reused as the board job id so a creation is traceable end to end. A PRIVATE
    # creation gets NO board entry (job_id stays None) — it must never appear on the :8765 hub.
    session = "shot_" + secrets.token_hex(4)
    job_id = None
    if not private:
        try:
            job_id = J.create("Create from image", job_id=session)
        except Exception:
            job_id = None

    # Resource gate (FAIL OPEN): no lease daemon => nothing to generate on; skip calmly.
    if not coordinator_up():
        _job(job_id, status="skipped", detail="graphics turn-taking is offline")
        notify("Dreaming is offline — skipping",
               "The GPU coordinator isn't running, so the video wasn't created. It never "
               "interrupts what you're doing.")
        return 0

    tag = " · Private" if private else ""
    local = None
    try:
        try:
            local = _clean_png(_fetch_raw(arg), private)   # validate + strip EXIF + cap (ADR-0017)
        except RuntimeError as e:
            if str(e) == "__no_pillow__":
                _job(job_id, status="failed", detail="image sanitizer unavailable")
                notify("Couldn't prepare this image",
                       "The image sanitizer (Pillow) isn't available, so nothing was created.",
                       "critical")
                return 0
            raise
        except ValueError as e:
            _job(job_id, status="failed", detail=f"unusable image: {e}")
            notify("Couldn't use this image", f"{e} — nothing was created.")
            return 0

        _job(job_id, status="checking")
        # Identity/likeness gate (FAIL CLOSED) — same gate for private and non-private.
        if not gate_seed(local, pre_consent, job_id):
            return 0

        _job(job_id, status="generating")
        notify("Creating your video…" + tag,
               "This takes a few minutes — you'll get a notification when it's ready."
               + (" Private: sealed in RAM, not saved." if private else ""))

        # The governed generation: B2 already cleared the seed, so start() trusts it; the lease,
        # eviction-confirm and red-line prompt gate all live inside lucid_linear from here.
        ST.clear(session)
        L.start(session, local, private=private, _trusted_seed=True)
        prompt = S.gate_prompt(MOTION_PROMPT)
        if prompt is None:                       # defensive: a neutral motion prompt should pass
            _job(job_id, status="failed", detail="motion prompt blocked")
            notify("Couldn't create this video", "The motion prompt was blocked. Nothing created.")
            return 0
        node = L.step(session, prompt, label="animate")
        if node is None:                         # generate_video fell open (GPU busy / preempted)
            _job(job_id, status="skipped", detail="the graphics card was busy")
            notify("The GPU is busy — skipped for now" + tag,
                   "Lucid waits its turn and won't interrupt you. Try again shortly.")
            return 0

        _job(job_id, status="ready", clip=node.get("clip"), frame=node.get("out_frame"))
        if private:
            notify("Your private video is ready",
                   "Ephemeral — not saved, wiped when you log out. Open Lucid to view; "
                   "'Burn now' removes it immediately.")
        else:
            notify("Your video is ready", "Saved to your dreams — open Lucid to view it.")
        return 0
    except SystemExit as e:                      # a gate inside lucid_linear refused (belt-and-braces)
        _job(job_id, status="blocked", detail=str(e) or "refused by a safety gate")
        notify("Can't create this video", str(e) or "Refused by a safety gate.", "critical")
        return 0
    except Exception as e:
        _job(job_id, status="failed", detail="something went wrong")
        print(f"[create] failed: {e}", file=sys.stderr)
        notify("Couldn't create the video" + tag, "Something went wrong — nothing was saved.")
        return 0
    finally:
        if local:                                  # the sanitized PNG is always a temp we own
            try:
                os.remove(local)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser(description="Create a local video from one image (governed path).")
    ap.add_argument("image", help="image path, file://, http(s):// or data: URL")
    ap.add_argument("--private", action="store_true",
                    help="ephemeral/incognito: sealed in RAM, not saved, auto-burned on logout (ADR-0016)")
    # No --consent flag on the argv surface (security review): a file named `--consent`/`-x.png` from
    # the file manager's %f could otherwise be parsed as a flag and pre-affirm the real-person gate.
    # Consent is captured ONLY in-process via the kdialog modal. The .desktop Exec also passes `--`.
    args = ap.parse_args()
    sys.exit(run(args.image, args.private))


if __name__ == "__main__":
    main()
