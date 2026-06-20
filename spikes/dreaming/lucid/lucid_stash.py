#!/usr/bin/env python3
"""Lucid private STASH — a persistent, encrypted home for private dreams (ADR-0028).

ADR-0016 made private mode RAM-only + burned-on-logout: a private dream could never be reopened.
ADR-0028 adds an opt-in, passphrase-locked STASH so a private dream CAN be saved and reopened —
WITHOUT weakening ADR-0016's live posture. The split that keeps that promise:

  * On disk the stash holds ONLY ciphertext: an encrypted index (so even the dream *names* are not
    plaintext) and one encrypted tar per dream (chain + clips + sealed frames). lucid_crypto does
    the authenticated encryption; the passphrase-derived master key lives only in this process's
    memory (set by unlock(), dropped by lock()).
  * A dream is only ever WORKED ON as a live private session — open_into() decrypts a stash entry
    INTO the same tmpfs + sealed-input sinks lucid_store already manages (ADR-0016). So while open
    it is exactly as ephemeral as today; the persistent copy is sealed ciphertext.
  * Re-seal then burn: reseal_opened() re-encrypts an open dream's current state back to its blob
    and burns the tmpfs working copy — the lock/logout path. If the stash is already locked (no key)
    the working copy is just burned (the last explicit save persists). Either way no private
    plaintext outlives the session.

Stash layout (LUCID_STASH, default ~/.local/share/agentos/lucid-stash):
    meta.json          plaintext: {"v":1, "salt": <hex>, "check": <hex blob>}   (salt is public)
    index.luciddream   encrypt(master, json[ {id,name,created,updated,frames,premise} ])
    <id>.luciddream    encrypt(master, tar{ chain.json, clips/*, frames/* })

SECURITY: ids are random hex (never the name); tar members are extracted by BASENAME into a known
dir (no traversal); blobs/meta are written 0600 in a 0700 root; session names are lucid_store-
validated before any path is built. See lucid_crypto for the at-rest construction + its honest
"hand-composed AEAD" caveat.
"""
import io
import json
import os
import secrets
import tarfile
import threading
import time

import lucid_crypto as C
import lucid_store as ST

_CHECK_TOKEN = b"lucid-stash-unlock-ok"
_BLOB_EXT = ".luciddream"
_REKEY_EXT = ".rekey"            # staged-under-the-new-key sidecar during a crash-atomic rotation
_ID_RE_LEN = 12   # hex chars
# defense-in-depth bounds on tar extraction (the blob is authenticated first, so these only guard
# against a self-authored pathological archive — a generous ceiling, not a tight policy).
_MAX_TAR_MEMBERS = 4096
_MAX_MEMBER_BYTES = 256 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

# master key + opened-session map live in-process only; never persisted. The lock guards both so an
# unlock/lock can't race a save/open. _OPENED maps a live (restored) session name -> its stash id, so
# a re-save updates the same blob and reseal/lock knows which working copies to re-encrypt.
_STATE = {"master": None}
_OPENED = {}
_LOCK = threading.RLock()


# ---------------- paths ----------------
def _root():
    base = os.environ.get("LUCID_STASH")
    if not base:
        data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        base = os.path.join(data, "agentos", "lucid-stash")
    return base


def _meta_path():
    return os.path.join(_root(), "meta.json")


def _index_path():
    return os.path.join(_root(), "index" + _BLOB_EXT)


def _blob_path(sid):
    if not _valid_id(sid):
        raise ValueError(f"invalid stash id {sid!r}")
    return os.path.join(_root(), sid + _BLOB_EXT)


def _valid_id(sid):
    return isinstance(sid, str) and len(sid) == _ID_RE_LEN and all(c in "0123456789abcdef" for c in sid)


def _new_id():
    while True:
        sid = secrets.token_hex(_ID_RE_LEN // 2)
        if not os.path.exists(_blob_path(sid)):
            return sid


# ---------------- low-level io (atomic, 0600 in a 0700 root) ----------------
def _ensure_root():
    r = _root()
    os.makedirs(r, exist_ok=True)
    try:
        os.chmod(r, 0o700)
    except OSError:
        pass
    return r


def _write_atomic(path, data, mode=0o600):
    """Atomic write at `mode` FROM CREATION (no world-readable window before chmod): a random-named
    temp in the 0700 root, opened O_EXCL at the target mode, fsync'd, then os.replace. Cleans up the
    temp on any write error."""
    _ensure_root()
    tmp = f"{path}.{secrets.token_hex(6)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read(path):
    with open(path, "rb") as f:
        return f.read()


# ---------------- lock / unlock (the master key) ----------------
def exists():
    """True iff the stash has been initialized (a passphrase was set)."""
    return os.path.isfile(_meta_path())


def is_unlocked():
    with _LOCK:
        return _STATE["master"] is not None


def _require_master():
    m = _STATE["master"]
    if m is None:
        raise RuntimeError("stash is locked")
    return m


def init(passphrase):
    """Create the stash with `passphrase` and leave it unlocked. Refuses if one already exists
    (use change_passphrase to rotate). Raises ValueError on an empty passphrase."""
    with _LOCK:
        if exists():
            raise FileExistsError("stash already initialized")
        if not passphrase:
            raise ValueError("empty passphrase")
        salt = C.new_salt()
        master = C.derive_master(passphrase, salt)
        _ensure_root()
        meta = {"v": 1, "salt": salt.hex(), "check": C.encrypt(master, _CHECK_TOKEN).hex()}
        _write_atomic(_meta_path(), json.dumps(meta).encode())
        _STATE["master"] = master
        _write_index([])   # empty encrypted index
        return True


def unlock(passphrase):
    """Derive the master key and verify it against the stored check token. On success the stash is
    unlocked (key held in memory) and True is returned; on a wrong passphrase nothing is stored and
    False is returned. Raises FileNotFoundError if the stash was never initialized."""
    with _LOCK:
        if not exists():
            raise FileNotFoundError("stash not initialized")
        _resolve_pending_rekey()        # finish/roll-back an interrupted rotation BEFORE any read
        meta = json.loads(_read(_meta_path()))
        salt = bytes.fromhex(meta["salt"])
        master = C.derive_master(passphrase, salt)
        if not C.verify(master, bytes.fromhex(meta["check"])):
            return False
        _STATE["master"] = master
        return True


def lock():
    """Drop the master key + the opened-session map from memory. Does NOT burn working copies —
    call reseal_opened() first if you want unsaved changes sealed. Idempotent."""
    with _LOCK:
        _STATE["master"] = None
        _OPENED.clear()


# ---------------- the encrypted index ----------------
def _read_index():
    p = _index_path()
    if not os.path.isfile(p):
        return []          # ABSENT = legitimately empty (before the first write)
    # PRESENT-but-unauthenticated is NOT empty: let C.BadData propagate rather than return [] —
    # otherwise the next _write_index would overwrite a recoverable index with [], orphaning every
    # blob. Bit-rot or an interrupted rotation surfaces as a hard error, not silent data loss.
    return json.loads(C.decrypt(_require_master(), _read(p)).decode())


def _write_index(entries):
    _write_atomic(_index_path(), C.encrypt(_require_master(), json.dumps(entries).encode()))


def listing():
    """Decrypted stash entries (newest first): {id,name,created,updated,frames,premise}. Requires
    an unlocked stash. NO filesystem paths ever leave this module."""
    with _LOCK:
        _require_master()
        return sorted(_read_index(), key=lambda e: e.get("updated", e.get("created", 0)), reverse=True)


# ---------------- save a live private session INTO the stash ----------------
def _add_file(tar, arcname, path):
    data = _read(path)
    ti = tarfile.TarInfo(arcname)
    ti.size = len(data)
    ti.mtime = 0          # deterministic; no real-clock leak into the archive
    ti.mode = 0o600
    tar.addfile(ti, io.BytesIO(data))


def save_session(session, name=None):
    """Encrypt a live PRIVATE session's full state (chain + clips + sealed frames) into the stash.
    Creates a new entry, or updates the entry this session was opened from (so re-saving an opened
    dream overwrites its blob, not a duplicate). Returns the entry metadata. Raises ValueError if the
    session isn't private (the stash is private-only)."""
    import lucid_engine as E
    with _LOCK:
        master = _require_master()
        if not ST.valid_session(session):
            raise ValueError(f"invalid session {session!r}")
        if not ST.is_private(session):
            raise ValueError("only a private session can be stashed")
        chain = ST.load_chain(session, True)

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            cb = json.dumps(chain).encode()
            ti = tarfile.TarInfo("chain.json"); ti.size = len(cb); ti.mtime = 0; ti.mode = 0o600
            tar.addfile(ti, io.BytesIO(cb))
            tdir = ST.session_dir(session, True)            # tmpfs: clips (+ note frames)
            if os.path.isdir(tdir):
                for fn in sorted(os.listdir(tdir)):
                    p = os.path.join(tdir, fn)
                    if not os.path.isfile(p) or os.path.islink(p):
                        continue
                    if fn == "chain.json" or fn.startswith(".chain."):
                        continue
                    _add_file(tar, "clips/" + fn, p)
            sub = os.path.join(E.INPUT_DIR, f".lucid-priv-{session}")   # sealed anchor/seed/guide frames
            if os.path.isdir(sub) and not os.path.islink(sub):
                for fn in sorted(os.listdir(sub)):
                    p = os.path.join(sub, fn)
                    if os.path.isfile(p) and not os.path.islink(p):
                        _add_file(tar, "frames/" + fn, p)
        blob = C.encrypt(master, buf.getvalue())

        sid = _OPENED.get(session) or _new_id()
        _write_atomic(_blob_path(sid), blob)
        entries = _read_index()
        prior = next((e for e in entries if e.get("id") == sid), None)
        nm = (name or (prior or {}).get("name") or chain.get("name")
              or chain.get("premise") or "Untitled dream")
        entry = {"id": sid, "name": str(nm)[:80],
                 "created": (prior or {}).get("created", time.time()), "updated": time.time(),
                 "frames": len(chain.get("nodes", [])), "premise": chain.get("premise")}
        if prior:
            entries[entries.index(prior)] = entry
        else:
            entries.append(entry)
        _write_index(entries)
        _OPENED[session] = sid
        return entry


# ---------------- open a stash entry INTO a live private session ----------------
def restore_name(sid):
    """The deterministic live-session name a stash id restores into (stable, so reopening reuses the
    same tmpfs working dir rather than piling up restores)."""
    return "stash-" + sid


def open_into(sid):
    """Decrypt stash entry `sid` into a live PRIVATE session (tmpfs chain+clips + sealed input
    frames), rewriting the chain's paths to the restored session so it is independent of whatever
    name it was saved under. Returns (session_name, chain). Requires an unlocked stash."""
    import lucid_engine as E
    with _LOCK:
        master = _require_master()
        raw = C.decrypt(master, _read(_blob_path(sid)))   # raises BadData on a corrupt/foreign blob
        sess = restore_name(sid)
        ST.burn(sess)                                      # clear any prior restore / collision (ephemeral)
        tdir = ST.ensure_session(sess, True)
        chain = None
        seen = total = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for m in tar.getmembers():
                if not m.isfile():
                    continue                              # skip symlinks/dirs/devices
                seen += 1
                total += max(m.size, 0)
                if seen > _MAX_TAR_MEMBERS or m.size > _MAX_MEMBER_BYTES or total > _MAX_TOTAL_BYTES:
                    raise C.BadData("stash entry exceeds extraction bounds")
                base = os.path.basename(m.name)           # extract by BASENAME only — no path traversal
                if m.name == "chain.json":
                    chain = json.loads(tar.extractfile(m).read())
                # clips/frames are allowlisted by valid_name (rejects '..', separators, dotfiles like
                # .chain.*) and clips additionally can't collide with the chain file.
                elif m.name.startswith("clips/") and ST.valid_name(base) and base != "chain.json":
                    with open(os.path.join(tdir, base), "wb") as f:
                        f.write(tar.extractfile(m).read())
                elif m.name.startswith("frames/") and ST.valid_name(base):
                    _ref, abs_path = ST.frame_ref(sess, True, base)   # mkdir-or-validate sealed subdir
                    with open(abs_path, "wb") as f:
                        f.write(tar.extractfile(m).read())
        if chain is None:
            raise C.BadData("stash entry missing chain.json")
        # rewrite paths so the restored chain is self-consistent under `sess` (and the current
        # runtime root / input dir), regardless of the name it was saved under.
        for nd in chain.get("nodes", []):
            if nd.get("clip"):
                nd["clip"] = os.path.join(tdir, os.path.basename(nd["clip"]))
            of = nd.get("out_frame")
            if of:
                nd["out_frame"] = f".lucid-priv-{sess}/{os.path.basename(of)}"
            for note in (nd.get("notes") or []):
                note.pop("_frame", None)   # defensive: notes carry no path, but never trust a stale one
        chain["session"] = sess
        chain["private"] = True
        ST.save_chain(sess, True, chain)
        _OPENED[sess] = sid
        return sess, chain


# ---------------- entry management ----------------
def rename(sid, name):
    with _LOCK:
        _require_master()
        entries = _read_index()
        e = next((x for x in entries if x.get("id") == sid), None)
        if e is None:
            return False
        e["name"] = str(name or "")[:80] or "Untitled dream"
        e["updated"] = time.time()
        _write_index(entries)
        return True


def delete(sid):
    """Remove a stash entry's ciphertext blob and its index row. Also burns the live working copy if
    it happens to be open. Returns True iff something was removed."""
    with _LOCK:
        _require_master()
        entries = _read_index()
        kept = [x for x in entries if x.get("id") != sid]
        removed = len(kept) != len(entries)
        if removed:
            _write_index(kept)
        try:
            os.remove(_blob_path(sid))
            removed = True
        except FileNotFoundError:
            pass
        sess = restore_name(sid)
        if sess in _OPENED:
            _OPENED.pop(sess, None)
        ST.burn(sess)   # if it was open, wipe the working copy too
        return removed


# ---------------- reseal + change passphrase ----------------
def opened_sessions():
    with _LOCK:
        return dict(_OPENED)


def reseal_opened(burn=True):
    """Re-encrypt every OPEN working copy back to its stash blob, then (default) burn the tmpfs
    working copy — the lock/logout path while the key is still held. Best-effort: a session that was
    already burned just clears its mapping. Returns the ids resealed. No-op if locked."""
    with _LOCK:
        if _STATE["master"] is None:
            return []
        resealed = []
        for sess, sid in list(_OPENED.items()):
            try:
                if ST.is_private(sess):
                    save_session(sess)
                    resealed.append(sid)
            except Exception:
                pass
            if burn:
                ST.burn(sess)
                _OPENED.pop(sess, None)
        return resealed


def _promote_rekey():
    """Finish a committed rotation: replace every staged <name>.rekey with its live file, then strip
    the 'rekey' flag from meta. Idempotent and KEY-FREE (pure file moves) so it can run at unlock on a
    process that doesn't yet hold the key."""
    root = _root()
    if os.path.isdir(root):
        for fn in os.listdir(root):
            if fn.endswith(_REKEY_EXT):
                live = os.path.join(root, fn[:-len(_REKEY_EXT)])
                os.replace(os.path.join(root, fn), live)
    try:
        meta = json.loads(_read(_meta_path()))
    except (FileNotFoundError, ValueError):
        return
    if meta.pop("rekey", None) is not None:
        _write_atomic(_meta_path(), json.dumps(meta).encode())


def _resolve_pending_rekey():
    """Make the stash consistent after an interrupted change_passphrase, BEFORE any read/unlock.
    KEY-FREE. meta is the single atomic commit point: if it carries the 'rekey' flag the new key was
    committed -> finish the promote; otherwise any staged *.rekey are PRE-commit orphans (the live,
    old-key blobs are intact) -> discard them. Either way the stash ends fully on one key."""
    if not exists():
        return
    try:
        meta = json.loads(_read(_meta_path()))
    except (FileNotFoundError, ValueError):
        return
    if meta.get("rekey"):
        _promote_rekey()
        return
    root = _root()
    if os.path.isdir(root):
        for fn in os.listdir(root):
            if fn.endswith(_REKEY_EXT):
                try:
                    os.remove(os.path.join(root, fn))
                except OSError:
                    pass


def change_passphrase(old, new):
    """Rotate the passphrase: verify `old`, re-key with a fresh salt, re-encrypt the index + every
    blob under the new master. Leaves the stash unlocked under the new key. Returns False on a wrong
    old passphrase (nothing changed).

    CRASH-ATOMIC (ADR-0005): the new-key blobs + index are STAGED to <name>.rekey sidecars while the
    live (old-key) files stay readable; a SINGLE atomic meta write (with a 'rekey' flag) is the commit
    point; then the sidecars are promoted into place and the flag stripped. A crash before the commit
    rolls back to the old key (orphans discarded); a crash after it is finished on the next unlock
    (_resolve_pending_rekey) — the stash is never split across two keys."""
    with _LOCK:
        if not exists():
            raise FileNotFoundError("stash not initialized")
        if not new:
            raise ValueError("empty new passphrase")
        _resolve_pending_rekey()          # never rotate on top of an interrupted rotation
        meta = json.loads(_read(_meta_path()))
        old_master = C.derive_master(old, bytes.fromhex(meta["salt"]))
        if not C.verify(old_master, bytes.fromhex(meta["check"])):
            return False
        new_salt = C.new_salt()
        new_master = C.derive_master(new, new_salt)
        try:
            entries = json.loads(C.decrypt(old_master, _read(_index_path())).decode())
        except (FileNotFoundError, C.BadData):
            entries = []
        # STAGE: write every blob + the index under the new key to .rekey sidecars (live files untouched)
        for e in entries:
            sid = e.get("id")
            try:
                plain = C.decrypt(old_master, _read(_blob_path(sid)))
            except (FileNotFoundError, C.BadData):
                continue
            _write_atomic(_blob_path(sid) + _REKEY_EXT, C.encrypt(new_master, plain))
        _write_atomic(_index_path() + _REKEY_EXT, C.encrypt(new_master, json.dumps(entries).encode()))
        # COMMIT: one atomic meta write flips the stash to the new key (promote pending)
        committed = {"v": 1, "salt": new_salt.hex(),
                     "check": C.encrypt(new_master, _CHECK_TOKEN).hex(), "rekey": True}
        _write_atomic(_meta_path(), json.dumps(committed).encode())
        _STATE["master"] = new_master
        _promote_rekey()                  # replace staged -> live, strip the flag
        return True
