#!/usr/bin/env python3
"""Unit tests for the Create-from-Image launcher's gates (ADR-0015/0016/0017). No GPU, no vision
model, no lease daemon — B2 and the desktop side-effects are mocked, so the safety-critical routing
(fail-closed identity gate, EXIF strip, scheme allowlist) is provable. Run:
python3 test_create_from_image.py"""
import io
import os
import sys
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("cfi", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "create_from_image.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
import lucid_b2 as B2

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


# Silence desktop side-effects; record consent-dialog calls.
NOTES = []
C.notify = lambda *a, **k: NOTES.append(a)
DIALOG_CALLS = {"n": 0}


def _png(w=16, h=16, exif_note=None, fmt="PNG"):
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (w, h), (10, 80, 160))
    if exif_note:
        ex = img.getexif(); ex[0x010e] = exif_note
        img.save(buf, "JPEG", exif=ex.tobytes())
    else:
        img.save(buf, fmt)
    return buf.getvalue()


# --- _fetch_raw: scheme allowlist (trust boundary for the browser srcUrl) ---
for bad in ["ftp://h/x.png", "javascript:alert(1)", "chrome://x", "about:blank"]:
    try:
        C._fetch_raw(bad)
        check(f"reject scheme {bad}", False)
    except ValueError:
        check(f"reject scheme {bad}", True)

# data: URL accepted
raw = C._fetch_raw("data:image/png;base64," + __import__("base64").b64encode(_png()).decode())
check("data: URL fetched", len(raw) > 0)

# --- SSRF guard: internal targets refused (no network needed — numeric/hosts resolution) ---
for h in ["127.0.0.1", "169.254.169.254", "localhost", "0.0.0.0", "10.0.0.5", "192.168.1.1", ""]:
    check(f"internal host blocked: {h or '<empty>'}", C._host_is_internal(h) is True)
for bad in ["http://127.0.0.1:8188/x.png", "http://169.254.169.254/latest/meta-data",
            "https://localhost/x"]:
    try:
        C._fetch_raw(bad)
        check(f"SSRF refused: {bad}", False)
    except ValueError:
        check(f"SSRF refused: {bad}", True)

# --- _clean_png: EXIF strip + validation + scheme of sanitization ---
clean = C._clean_png(_png(exif_note="GPS:secret-location"), private=False)
body = open(clean, "rb").read()
check("EXIF/GPS stripped from re-encoded seed", b"GPS:secret-location" not in body)
from PIL import Image
check("seed normalized to RGB PNG", Image.open(clean).format == "PNG")
os.remove(clean)

# private seed lands in tmpfs, 0600
clean_p = C._clean_png(_png(), private=True)
runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
check("private seed sealed in tmpfs", runtime in clean_p)
check("private seed mode 0600", oct(os.stat(clean_p).st_mode & 0o777) == "0o600")
os.remove(clean_p)

# non-image rejected as a clean ValueError (not a crash)
try:
    C._clean_png(b"this is not an image", private=False)
    check("non-image -> ValueError", False)
except ValueError:
    check("non-image -> ValueError", True)

# --- gate_seed: the fail-closed identity decision (B2 mocked) ---
def mock_b2(flags):
    return lambda *a, **k: B2.dispose(flags)


# clear image -> proceed
B2.check_seed = mock_b2({"has_face": False, "real_person": False, "possibly_minor": False})
check("clear image -> generate", C.gate_seed("/x", pre_consent=False) is True)

# possible minor -> hard block, never overridable (even with pre_consent)
B2.check_seed = mock_b2({"has_face": True, "real_person": True, "possibly_minor": True})
check("possible minor -> blocked", C.gate_seed("/x", pre_consent=False) is False)
check("possible minor -> blocked even with consent", C.gate_seed("/x", pre_consent=True) is False)

# real person, no consent, no dialog available -> fail closed
C.consent_for_real_person = lambda v: False
B2.check_seed = mock_b2({"has_face": True, "real_person": True, "possibly_minor": False})
check("real person, declined -> blocked", C.gate_seed("/x", pre_consent=False) is False)
# real person, pre-consent -> allowed (caller already captured consent)
check("real person, pre-consent -> generate", C.gate_seed("/x", pre_consent=True) is True)
# real person, dialog says yes -> allowed
C.consent_for_real_person = lambda v: True
check("real person, dialog yes -> generate", C.gate_seed("/x", pre_consent=False) is True)

# can't verify (model down / unparseable) -> fail closed
B2.check_seed = lambda *a, **k: B2.Verdict(False, False, "model down", {"checked": False})
check("can't verify -> blocked (fail closed)", C.gate_seed("/x", pre_consent=False) is False)


# === run(): coordinator-offline HOLD path (ADR-0019 §5) ==============================================
# The whole point: a coordinator-offline request must be HELD (durable spool), not dropped to `skipped`
# — but ONLY a B2-cleared, NON-private request, and a possible-minor must STILL be a terminal block.
import tempfile
import lucid_queue as Q
import lucid_jobs as J
import lucid_store as ST
import lucid_linear as L
import lucid_safety as S
import lucid_toast as _real_toast   # used ONLY for its real _require_durable_record guard in the fake

# A real, sanitizable seed via a data: URL (no network) so run()'s fetch+_clean_png actually succeeds.
SEED_URL = "data:image/png;base64," + __import__("base64").b64encode(_png()).decode()


class _Recorder:
    """Stand in for the side-effecting modules; records every call so the routing is provable."""
    def __init__(self):
        self.jobs = {}          # job_id -> last status dict
        self.enqueued = []      # (job_id, title, snapshot_existed)
        self.generated = False  # did we reach L.start (i.e. actually try to generate)?
        self.held_toasts = []   # G5: records passed to lucid_toast.notify_held (must be durable dicts)
        self.failed_toasts = 0  # G5: count of notify_enqueue_failed (no-action, no-row) toasts
        self.private_toasts = []  # G5: titles passed to the action-less private carve-out (if ever)


def _install_run_harness(rec, *, coordinator, b2_flags=None, b2_verdict=None,
                         enqueue_raises=False, spool=None):
    """Wire run()'s dependencies to the recorder. Returns nothing; mutates module-level C/J/Q/etc."""
    C.coordinator_up = lambda: coordinator

    # Visible board (:8765) — pin a known job_id (run() uses J.create's return as job_id) so the
    # recorder keys are stable; record the LAST status we set per job.
    J.create = lambda title, job_id=None: "shot_test"
    def _update(job_id, **fields):
        rec.jobs[job_id] = fields
    J.update = _update

    # Durable spool — real enqueue against a temp spool (proves the snapshot is copied), or a raiser.
    if enqueue_raises:
        def _enq(job_id, title, snapshot_src=None, **k):
            raise RuntimeError("spool unavailable")
        Q.enqueue = _enq
    else:
        def _enq(job_id, title, snapshot_src=None, **k):
            snap_ok = bool(snapshot_src) and os.path.isfile(snapshot_src)
            rec.enqueued.append((job_id, title, snap_ok))
            return _orig_enqueue(job_id, title, snapshot_src, spool=spool)
        Q.enqueue = _enq

    # Storage hygiene + governed generation — no-ops / trip the "generated" flag.
    ST.reap_orphans = lambda *a, **k: None
    ST.clear = lambda *a, **k: None
    def _start(*a, **k):
        rec.generated = True
    L.start = _start
    L.step = lambda *a, **k: {"clip": "c.mp4", "out_frame": "f.png"}
    S.gate_prompt = lambda p: p

    # G5 recovery-toast (ADR-0019 §5): fake the lucid_toast module ref on C so we can prove the held
    # points render the persist-first a11y toast over the ENQUEUED durable record (never send a real
    # notify-send). notify_held re-runs the REAL durable-record guard so a non-durable/private/missing
    # record would raise here — i.e. the test fails loudly if we ever pass the wrong shape.
    # bind the recorder into the fake (closures over `rec`)
    def _notify_held(record, *a, **k):
        _real_toast._require_durable_record(record)    # raises unless durable, non-private, has id
        rec.held_toasts.append(dict(record))
        return None

    def _notify_enqueue_failed(*a, **k):
        rec.failed_toasts += 1
        return None

    def _notify_private(title, *a, **k):
        rec.private_toasts.append(title)
        return None

    fake = type("ToastNS", (), {})()
    fake.notify_held = _notify_held
    fake.notify_enqueue_failed = _notify_enqueue_failed
    fake.notify_private = _notify_private
    C.T = fake

    # B2 verdict (the gate that must clear before a hold).
    if b2_verdict is not None:
        B2.check_seed = lambda *a, **k: b2_verdict
    else:
        B2.check_seed = mock_b2(b2_flags)


_orig_enqueue = Q.enqueue   # keep the real durable enqueue so a non-private hold genuinely spools


# --- coordinator OFFLINE, non-private, B2-clear -> HELD (durable spool), NOT skipped, NOT generated ---
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=False,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         spool=spool)
    rc = C.run(SEED_URL, private=False)
    check("offline non-private -> rc 0 (calm)", rc == 0)
    check("offline non-private -> enqueued (held, not dropped)", len(rec.enqueued) == 1)
    check("offline non-private -> snapshot PNG copied into spool", rec.enqueued and rec.enqueued[0][2] is True)
    check("offline non-private -> board status 'held'",
          rec.jobs.get("shot_test", {}).get("status") == "held")
    check("offline non-private -> never generated (deferred, not run)", rec.generated is False)
    # G5 (ADR-0019 §5): the held point renders the recovery toast over the ENQUEUED durable record.
    check("offline non-private -> notify_held called exactly once", len(rec.held_toasts) == 1)
    check("offline non-private -> notify_held got the durable record id (matches the board job)",
          rec.held_toasts and rec.held_toasts[0].get("id") == "shot_test")
    check("offline non-private -> notify_held record is in the durable 'held' state",
          rec.held_toasts and rec.held_toasts[0].get("state") == "held")
    check("offline non-private -> notify_held record is NOT private (durable-row, persist-first)",
          rec.held_toasts and rec.held_toasts[0].get("private") is False)
    check("offline non-private -> the held record is the SAME row the spool enqueued (carries a seq)",
          rec.held_toasts and rec.held_toasts[0].get("seq") is not None)
    check("offline non-private -> no enqueue-failed toast on the success path", rec.failed_toasts == 0)
    # the durable spool actually holds a record + its sanitized snapshot
    import glob as _glob
    check("offline non-private -> a *.held.json exists on disk", len(_glob.glob(os.path.join(spool, "*.held.json"))) == 1)
    check("offline non-private -> the snapshot .png exists on disk", len(_glob.glob(os.path.join(spool, "*.png"))) == 1)


# --- coordinator OFFLINE, PRIVATE -> calm SKIP, never enters the durable spool ----------------------
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=False,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         spool=spool)
    rc = C.run(SEED_URL, private=True)
    check("offline private -> rc 0 (calm)", rc == 0)
    check("offline private -> NOT enqueued (no durable spool for private)", len(rec.enqueued) == 0)
    check("offline private -> never generated", rec.generated is False)
    import glob as _glob
    check("offline private -> nothing written to the durable spool", len(_glob.glob(os.path.join(spool, "*.held.json"))) == 0)
    # G5 carve-out (ADR-0019 §5 PRIVATE): a private deferral has NO durable row, so it must NEVER pass
    # a record to a record-requiring toast. The launcher keeps the calm plain skip notify for private.
    check("offline private -> notify_held NEVER called (no durable record exists for a private hold)",
          len(rec.held_toasts) == 0)
    check("offline private -> no enqueue-failed toast either (private never enqueues)", rec.failed_toasts == 0)
    check("offline private -> no durable-record toast was fabricated for a private item",
          len(rec.private_toasts) == 0)


# --- coordinator OFFLINE, possible MINOR -> TERMINAL block; never held, never enqueued --------------
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=False,
                         b2_flags={"has_face": True, "real_person": True, "possibly_minor": True},
                         spool=spool)
    rc = C.run(SEED_URL, private=False)
    check("offline + possible-minor -> rc 0", rc == 0)
    check("offline + possible-minor -> NEVER enqueued (terminal block, not held)", len(rec.enqueued) == 0)
    check("offline + possible-minor -> board status 'blocked' (not 'held')",
          rec.jobs.get("shot_test", {}).get("status") == "blocked")
    check("offline + possible-minor -> never generated", rec.generated is False)
    import glob as _glob
    check("offline + possible-minor -> nothing in the durable spool", len(_glob.glob(os.path.join(spool, "*.held.json"))) == 0)


# --- coordinator OFFLINE, can't-verify (B2 model down) -> fail-closed refuse; never held ------------
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=False,
                         b2_verdict=B2.Verdict(False, False, "model down", {"checked": False}),
                         spool=spool)
    rc = C.run(SEED_URL, private=False)
    check("offline + can't-verify -> NEVER enqueued (fail-closed, not held)", len(rec.enqueued) == 0)
    check("offline + can't-verify -> never generated", rec.generated is False)


# --- coordinator OFFLINE, enqueue FAILS -> fail-OPEN fallback to the calm skip (request not swallowed) ---
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=False,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         enqueue_raises=True, spool=spool)
    rc = C.run(SEED_URL, private=False)
    check("offline + enqueue-fail -> rc 0 (still calm)", rc == 0)
    check("offline + enqueue-fail -> falls back to 'skipped' (fail-open)",
          rec.jobs.get("shot_test", {}).get("status") == "skipped")
    check("offline + enqueue-fail -> never generated", rec.generated is False)
    # G5 fail-open honesty (ADR-0019 §5): the row could NOT be persisted, so show the no-action
    # critical "re-trigger" toast — and NEVER notify_held (there is no durable row to back an action).
    check("offline + enqueue-fail -> notify_enqueue_failed shown (no-action, no-row honesty)",
          rec.failed_toasts == 1)
    check("offline + enqueue-fail -> notify_held NEVER called (no row exists to back an action)",
          len(rec.held_toasts) == 0)


# --- coordinator ONLINE, B2-clear -> proceeds to generation (the reorder didn't break the happy path) ---
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=True,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         spool=spool)
    rc = C.run(SEED_URL, private=False)
    check("online + clear -> reaches generation", rec.generated is True)
    check("online + clear -> NOT enqueued (it ran, not held)", len(rec.enqueued) == 0)
    check("online + clear -> board status 'ready'",
          rec.jobs.get("shot_test", {}).get("status") == "ready")
    check("online + clear -> notify_held NEVER called on the happy path (it ran, wasn't held)",
          len(rec.held_toasts) == 0)


# --- coordinator ONLINE but the GPU is busy (L.step returns None) -> HELD + recovery toast (ADR-0019 §5) ---
# This is the SECOND held point: generate_video fell open (GPU busy / preempted / ComfyUI cold). It must
# enqueue the durable row and render notify_held over THAT enqueued record, exactly like the offline hold.
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=True,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         spool=spool)
    L.step = lambda *a, **k: None        # generate_video fell open -> the GPU-busy deferral branch
    rc = C.run(SEED_URL, private=False)
    check("gpu-busy non-private -> rc 0 (calm)", rc == 0)
    check("gpu-busy non-private -> enqueued (held, not dropped)", len(rec.enqueued) == 1)
    check("gpu-busy non-private -> board status 'held'",
          rec.jobs.get("shot_test", {}).get("status") == "held")
    check("gpu-busy non-private -> notify_held called exactly once", len(rec.held_toasts) == 1)
    check("gpu-busy non-private -> notify_held got the enqueued durable record id",
          rec.held_toasts and rec.held_toasts[0].get("id") == "shot_test")
    check("gpu-busy non-private -> notify_held record is in the durable 'held' state",
          rec.held_toasts and rec.held_toasts[0].get("state") == "held")
    check("gpu-busy non-private -> notify_held record is NOT private",
          rec.held_toasts and rec.held_toasts[0].get("private") is False)
    check("gpu-busy non-private -> no enqueue-failed toast on the success path", rec.failed_toasts == 0)


# --- coordinator ONLINE, GPU busy, but PRIVATE -> calm skip; NO durable-record toast ---------------
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=True,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         spool=spool)
    L.step = lambda *a, **k: None        # GPU busy
    rc = C.run(SEED_URL, private=True)
    check("gpu-busy private -> rc 0 (calm)", rc == 0)
    check("gpu-busy private -> NOT enqueued (private has no durable spool)", len(rec.enqueued) == 0)
    check("gpu-busy private -> notify_held NEVER called (no durable record for a private hold)",
          len(rec.held_toasts) == 0)


# --- coordinator ONLINE, GPU busy, enqueue FAILS -> notify_enqueue_failed, never notify_held -------
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=True,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         enqueue_raises=True, spool=spool)
    L.step = lambda *a, **k: None        # GPU busy
    rc = C.run(SEED_URL, private=False)
    check("gpu-busy + enqueue-fail -> rc 0 (still calm)", rc == 0)
    check("gpu-busy + enqueue-fail -> falls back to 'skipped' (fail-open)",
          rec.jobs.get("shot_test", {}).get("status") == "skipped")
    check("gpu-busy + enqueue-fail -> notify_enqueue_failed shown (no-row honesty)", rec.failed_toasts == 1)
    check("gpu-busy + enqueue-fail -> notify_held NEVER called (no row to back an action)",
          len(rec.held_toasts) == 0)


# --- fail-open: a THROWING held toast must NEVER break the held request (the row is already safe) ---
# notify_held raises, yet the request must still come to rest calmly at rc 0 with the row enqueued+held.
with tempfile.TemporaryDirectory() as spool:
    rec = _Recorder()
    _install_run_harness(rec, coordinator=False,
                         b2_flags={"has_face": False, "real_person": False, "possibly_minor": False},
                         spool=spool)
    def _boom(record, *a, **k):
        raise RuntimeError("swaync is on fire")
    C.T.notify_held = _boom            # the toast layer blows up...
    rc = C.run(SEED_URL, private=False)
    check("toast-throws -> rc 0 (fail-open: toast failure never breaks the request)", rc == 0)
    check("toast-throws -> still enqueued (the durable row is the backstop, not the toast)",
          len(rec.enqueued) == 1)
    check("toast-throws -> board status still 'held' (row safe despite the toast blowing up)",
          rec.jobs.get("shot_test", {}).get("status") == "held")


# --- report ---
print(f"{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
