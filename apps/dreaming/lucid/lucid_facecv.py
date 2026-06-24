#!/usr/bin/env python3
"""Deterministic face detection for B2 (ADR-0017 hardening).

The council wanted the seed-likeness gate to be DETERMINISTIC and external to the generating
model — a CV detector, not just a VLM. opencv (`cv2`) + Haar cascades are already in ComfyUI's
venv, so we shell out to it (the system python has no cv2) and get a reproducible face count.
This is B2's *primary* signal; the VLM is the second opinion (real-vs-drawn + age). Two
independent detectors → a VLM false-negative on a real face is still caught by CV.

Fail-closed by contract: `faces()` returns an int, or **None** when the detector couldn't run
(venv missing, cv2 error, unreadable image) — and the caller (lucid_b2) treats None as
"couldn't verify", never as "no face".
"""
import json
import os
import subprocess

VENV_PY = os.environ.get("LUCID_CV_PYTHON", os.path.expanduser("~/ComfyUI/.venv/bin/python"))

# Frontal + profile cascades (profiles are the classic Haar blind spot; run both). Pure stdlib+cv2
# in the child so it has no import surface beyond opencv.
_CHILD = r"""
import sys, os, json, cv2
img = cv2.imread(sys.argv[1])
if img is None:
    print(json.dumps({"faces": -1})); raise SystemExit
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
n = 0
for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
    cas = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, name))
    if cas.empty():
        continue
    n += len(cas.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(36, 36)))
print(json.dumps({"faces": int(n)}))
"""


def available():
    return os.path.exists(VENV_PY)


def faces(path):
    """Deterministic face count, or None if the detector could not run (caller fails closed)."""
    if not os.path.exists(VENV_PY):
        return None
    try:
        r = subprocess.run([VENV_PY, "-c", _CHILD, path],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        n = json.loads(r.stdout.strip()).get("faces", -1)
        return n if isinstance(n, int) and n >= 0 else None
    except Exception:
        return None
