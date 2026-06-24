#!/usr/bin/env python3
"""Lucid crypto — passphrase authenticated encryption, stdlib ONLY (ADR-0028).

The private STASH (a persistent, encrypted home for private dreams) needs authenticated
encryption at rest. This environment has no `cryptography` / `nacl` / `age` / libsodium — only
the Python stdlib (`hashlib`, `hmac`, `secrets`). So this module composes STANDARD primitives in
STANDARD constructions; it invents no ciphers:

  * KDF (passphrase -> master key): `hashlib.scrypt` — memory-hard, the right tool against a
    brute-forced passphrase. Salt is public (stored plaintext beside the ciphertext).
  * Per-message subkeys: keyed BLAKE2b over (label || nonce) under the master key (a fast KDF;
    scrypt runs ONCE at unlock, never per blob).
  * Confidentiality: keyed BLAKE2b in counter mode as a stream cipher (XOR a per-nonce keystream).
  * Integrity/authenticity: keyed BLAKE2b as a MAC over MAGIC||nonce||ciphertext —
    ENCRYPT-THEN-MAC. A wrong passphrase or any tampering fails the tag, so decrypt() raises
    rather than returning garbage. This is also what verifies an unlock attempt.

SECURITY POSTURE (honest, for the reviewer): scrypt + keyed-BLAKE2b EtM is sound when implemented
exactly as below, but it is hand-composed rather than a vetted AEAD. If/when a vetted library is
available (libsodium `crypto_secretbox`, or `age`), the on-disk format is versioned (MAGIC carries
a version) so the stash can be migrated. The passphrase and derived keys live ONLY in this process's
memory and are never logged, never written, never passed to a subprocess.

Format of one encrypted blob (all binary, concatenated):
    MAGIC(8) || nonce(16) || ciphertext(len=plaintext) || tag(32)
The scrypt salt is NOT in the blob — it is owned per-stash by the caller (lucid_stash) and stored
in cleartext meta, so one scrypt pass yields a master key reused across every blob in the stash.
"""
import hashlib
import hmac
import secrets

MAGIC = b"LCRYPT1\x00"          # 8 bytes: format + version; bump on any construction change
NONCE_LEN = 16
TAG_LEN = 32
KEY_LEN = 32
_SALT_LEN = 16
# scrypt cost: 128 * N * r * p = 32 MiB of memory at N=2**15, r=8, p=1 — interactive-fast on the
# 4090 box, expensive to brute-force. maxmem set above the requirement (OpenSSL refuses otherwise).
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 1 << 15, 8, 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024


class BadData(Exception):
    """Decryption failed: wrong passphrase, truncated/garbled blob, or tampering (the tag did not
    verify). Deliberately one exception for all three — never leak WHICH to an attacker."""


def new_salt():
    """A fresh public scrypt salt for a new stash."""
    return secrets.token_bytes(_SALT_LEN)


def derive_master(passphrase, salt):
    """Passphrase + public salt -> 32-byte master key (the one scrypt pass, done at unlock).
    `passphrase` is str (utf-8) or bytes; `salt` is bytes from new_salt()."""
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) != _SALT_LEN:
        raise ValueError("salt must be %d bytes" % _SALT_LEN)
    if not passphrase:
        raise ValueError("empty passphrase")
    return hashlib.scrypt(passphrase, salt=bytes(salt), n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                          dklen=KEY_LEN, maxmem=_SCRYPT_MAXMEM)


def _subkeys(master, nonce):
    """Per-message (enc, mac) subkeys from the master key + nonce via keyed BLAKE2b (fast KDF)."""
    enc = hashlib.blake2b(b"lucid-enc" + nonce, key=master, digest_size=KEY_LEN).digest()
    mac = hashlib.blake2b(b"lucid-mac" + nonce, key=master, digest_size=KEY_LEN).digest()
    return enc, mac


def _keystream(enc_key, nonce, n):
    """`n` bytes of keystream: keyed BLAKE2b(nonce || counter) in 64-byte blocks (counter mode)."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.blake2b(nonce + counter.to_bytes(8, "big"),
                               key=enc_key, digest_size=64).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data, stream):
    return bytes(a ^ b for a, b in zip(data, stream))


def encrypt(master, plaintext):
    """Authenticated-encrypt `plaintext` (bytes) under `master` (from derive_master). Returns the
    blob MAGIC||nonce||ct||tag. A fresh random nonce per call (never reuse a (key,nonce) pair)."""
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    nonce = secrets.token_bytes(NONCE_LEN)
    enc_key, mac_key = _subkeys(master, nonce)
    ct = _xor(bytes(plaintext), _keystream(enc_key, nonce, len(plaintext)))
    tag = hashlib.blake2b(MAGIC + nonce + ct, key=mac_key, digest_size=TAG_LEN).digest()
    return MAGIC + nonce + ct + tag


def decrypt(master, blob):
    """Verify + decrypt a blob from encrypt(). Raises BadData on a wrong key, truncation, or
    tampering (constant-time tag compare). Returns the plaintext bytes."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < len(MAGIC) + NONCE_LEN + TAG_LEN:
        raise BadData("blob too short")
    blob = bytes(blob)
    if blob[:len(MAGIC)] != MAGIC:
        raise BadData("bad magic / unknown format")
    off = len(MAGIC)
    nonce = blob[off:off + NONCE_LEN]; off += NONCE_LEN
    ct = blob[off:len(blob) - TAG_LEN]
    tag = blob[len(blob) - TAG_LEN:]
    enc_key, mac_key = _subkeys(master, nonce)
    want = hashlib.blake2b(MAGIC + nonce + ct, key=mac_key, digest_size=TAG_LEN).digest()
    if not hmac.compare_digest(tag, want):
        raise BadData("authentication failed (wrong passphrase or corrupted data)")
    return _xor(ct, _keystream(enc_key, nonce, len(ct)))


def verify(master, blob):
    """True iff `blob` authenticates under `master` — i.e. the passphrase is correct. Never raises."""
    try:
        decrypt(master, blob)
        return True
    except Exception:
        return False
