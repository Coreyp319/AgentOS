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

# --- report ---
print(f"{ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
