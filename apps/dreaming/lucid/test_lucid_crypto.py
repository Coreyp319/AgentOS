#!/usr/bin/env python3
"""Unit tests for lucid_crypto (ADR-0028) — passphrase authenticated encryption, stdlib only.
No GPU/daemon/model. Run: python3 test_lucid_crypto.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lucid_crypto as C  # noqa: E402

ok = 0
fail = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
    else:
        fail.append(name)


salt = C.new_salt()
master = C.derive_master("correct horse battery staple", salt)

# --- round-trips across sizes (incl. empty + > one 64-byte keystream block) ---
for pt in (b"", b"x", b"hello world", os.urandom(63), os.urandom(64), os.urandom(65), os.urandom(5000)):
    blob = C.encrypt(master, pt)
    check(f"round-trip len={len(pt)}", C.decrypt(master, blob) == pt)

# --- format / framing ---
blob = C.encrypt(master, b"payload")
check("blob carries the versioned magic", blob.startswith(C.MAGIC))
check("ciphertext length == plaintext length (stream cipher, no padding)",
      len(blob) == len(C.MAGIC) + C.NONCE_LEN + len(b"payload") + C.TAG_LEN)
check("ciphertext != plaintext", blob[len(C.MAGIC) + C.NONCE_LEN:-C.TAG_LEN] != b"payload")

# --- nonce uniqueness: same key+plaintext -> different blobs ---
a, b = C.encrypt(master, b"same"), C.encrypt(master, b"same")
check("two encryptions of the same plaintext differ (fresh nonce)", a != b)
check("both still decrypt", C.decrypt(master, a) == b"same" and C.decrypt(master, b) == b"same")

# --- wrong passphrase fails (authentication, not garbage) ---
wrong = C.derive_master("wrong passphrase", salt)
try:
    C.decrypt(wrong, blob); wp = False
except C.BadData:
    wp = True
check("wrong passphrase raises BadData", wp)
check("verify() is False on the wrong key", C.verify(wrong, blob) is False)
check("verify() is True on the right key", C.verify(master, blob) is True)

# --- wrong salt => wrong master => fails (salt is part of the KDF) ---
other = C.derive_master("correct horse battery staple", C.new_salt())
check("same passphrase, different salt -> cannot decrypt", C.verify(other, blob) is False)

# --- tamper detection: flip one byte in each region -> BadData ---
def _tampered(b, i):
    m = bytearray(b); m[i] ^= 0x01; return bytes(m)


big = C.encrypt(master, b"A" * 100)
for label, idx in (("magic", 0), ("nonce", len(C.MAGIC)),
                   ("ciphertext", len(C.MAGIC) + C.NONCE_LEN + 10),
                   ("tag", len(big) - 1)):
    try:
        C.decrypt(master, _tampered(big, idx)); caught = False
    except C.BadData:
        caught = True
    check(f"tamper in {label} -> BadData", caught)

check("truncated blob -> BadData", not C.verify(master, big[:10]))

# --- KDF determinism + input guards ---
check("derive_master is deterministic for (passphrase,salt)",
      C.derive_master("pw", salt) == C.derive_master("pw", salt))
check("derive_master accepts bytes passphrase too",
      C.derive_master(b"pw", salt) == C.derive_master("pw", salt))
for bad in (lambda: C.derive_master("", salt), lambda: C.derive_master("pw", b"short")):
    try:
        bad(); raised = False
    except ValueError:
        raised = True
    check("derive_master rejects bad input", raised)

print(f"lucid_crypto: {ok} passed, {len(fail)} failed")
for f in fail:
    print("  FAIL:", f)
sys.exit(1 if fail else 0)
