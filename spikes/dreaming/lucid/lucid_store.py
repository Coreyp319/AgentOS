#!/usr/bin/env python3
"""Lucid storage layer — persistent vs PRIVATE/ephemeral (ADR-0016).

Centralizes every decision about *where a dream's bytes live*, so the privacy posture is one
auditable module instead of scattered path joins. Two modes:

  persistent (default) — dreams live in the dream cache (~/.local/share/agentos/dreams) as today.
  private (incognito)  — RAM-backed, sealed, never persisted, auto-burned:
     * chain.json + clips  -> $XDG_RUNTIME_DIR/agentos/lucid-priv/<session>/   (tmpfs, 0700)
     * seed + anchor frames-> ~/ComfyUI/input/.lucid-priv-<session>/           (0700, must be
       ComfyUI-readable — the one unavoidable real-disk spot; sealed + burned)
     * ComfyUI output       -> output/lucid-priv-<session>/ then MOVED to tmpfs + the
       shared-output copy removed (a private clip never lingers in the shared output dir)
  burn(session) securely removes all three sinks; on a private session there is nothing left.

SECURITY: session names flow into filesystem paths INCLUDING an rmtree target, so every name is
validated against a strict allowlist (fail-closed) before any path is built — no traversal, no
clobber. (security-review S-class: never delegate path safety.)
"""
import json
import os
import re
import secrets
import shutil

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def valid_session(session):
    return isinstance(session, str) and bool(_SESSION_RE.match(session))


def valid_name(name):
    """A frame filename: strict basename, no separators, no '..' (security-review)."""
    return (isinstance(name, str) and bool(_NAME_RE.match(name))
            and os.path.basename(name) == name and ".." not in name)


def _own_real_dir(path):
    """True iff `path` is a real directory (not a symlink) owned by us — the only kind we'll
    write into or rmtree. Closes the planted-symlink redirect on the shared ComfyUI input dir."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None  # absent
    import stat as _stat
    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False  # present but unsafe (symlink / not-a-dir / not-ours)
    return True


def _make_sealed(path):
    """Create `path` as a fresh 0700 dir we own, or accept it only if it already is one.
    Refuses a symlink / foreign / non-dir at that name (no makedirs(exist_ok)+chmod-follows-link)."""
    safe = _own_real_dir(path)
    if safe is None:
        os.mkdir(path, 0o700)
    elif safe is False:
        raise PermissionError(f"refusing sealed dir at unsafe path {path!r} (symlink/foreign/not-a-dir)")
    else:
        os.chmod(path, 0o700)
    return path


def _require(session):
    if not valid_session(session):
        raise ValueError(f"invalid session name {session!r} (allowed: [A-Za-z0-9_-], 1-64 chars)")
    return session


def _runtime_root():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "agentos", "lucid-priv")


def _persistent_root():
    import lucid_engine as E  # the persistent dream cache
    return E.DREAMS_DIR


# ---- where the chain + clips live ----
def session_dir(session, private):
    _require(session)
    root = _runtime_root() if private else _persistent_root()
    return os.path.join(root, session)


def ensure_session(session, private):
    d = session_dir(session, private)
    os.makedirs(d, exist_ok=True)
    if private:
        os.chmod(d, 0o700)
        os.chmod(os.path.dirname(d), 0o700)  # the lucid-priv root, too
    return d


def is_private(session):
    """Fail-closed: private if ANY private-only artifact exists — the tmpfs dir OR the sealed input
    subdir. Never inferred solely from the wipeable tmpfs dir, so an input-subdir orphan that
    outlived a logout is still treated as private (routed to burn), never to the shared cache."""
    if not valid_session(session):
        return False
    import lucid_engine as E
    return (os.path.isdir(os.path.join(_runtime_root(), session))
            or os.path.isdir(os.path.join(E.INPUT_DIR, f".lucid-priv-{session}")))


def chain_path(session, private):
    return os.path.join(session_dir(session, private), "chain.json")


def save_chain(session, private, chain):
    """Atomic write (feed.rs idiom): temp + fsync + os.replace — never a torn chain."""
    d = ensure_session(session, private)
    tmp = os.path.join(d, f".chain.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(chain, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chain_path(session, private))


def load_chain(session, private):
    with open(chain_path(session, private)) as f:
        return json.load(f)


# ---- anchor/seed frames: must be ComfyUI-readable. Private -> a sealed subdir of input/ ----
def frame_ref(session, private, name):
    """Return (loadimage_name, abs_path) for an anchor frame called `name`. Private mode puts it
    in a symlink-safe 0700 subdir of ComfyUI's input/ and returns the subpath LoadImage reads."""
    import lucid_engine as E
    _require(session)
    if not valid_name(name):
        raise ValueError(f"invalid frame name {name!r}")
    if not private:
        return name, os.path.join(E.INPUT_DIR, name)
    sub = f".lucid-priv-{session}"
    d = _make_sealed(os.path.join(E.INPUT_DIR, sub))  # mkdir-or-validate; refuses a planted symlink
    return f"{sub}/{name}", os.path.join(d, name)


def frame_abs(session, private, out_frame):
    """Absolute path of an EXISTING anchor frame from its stored `out_frame` ref — READ-ONLY (no dir
    side effects, unlike frame_ref which mkdir-or-validates). Used by the grounding pass to read the
    current frame off disk. Strict-basename validated (no traversal); private frames resolve into the
    sealed input subdir. Raises ValueError on a bad name (fail-closed, never a guessed path)."""
    _require(session)
    import lucid_engine as E
    base = os.path.basename(out_frame or "")
    if not valid_name(base):
        raise ValueError(f"invalid frame ref {out_frame!r}")
    if not private:
        return os.path.join(E.INPUT_DIR, base)
    return os.path.join(E.INPUT_DIR, f".lucid-priv-{session}", base)


def output_prefix(session, private):
    """ComfyUI VHS filename_prefix. Private clips render into a private subdir we then drain."""
    _require(session)
    return f"lucid-priv-{session}/clip" if private else f"lucid/{session}"


def _priv_output_dir(session):
    import lucid_engine as E
    return os.path.join(E.cc.COMFY_ROOT, "output", f"lucid-priv-{session}")


def place_clip(session, private, src_clip):
    """Persistent: leave the clip where ComfyUI wrote it. Private: drain the ENTIRE private output
    subdir into tmpfs — the clip AND its prompt-bearing metadata sidecar (.png) — then remove the
    shared subdir, so no private byte lingers in shared output (privacy-review BLOCKER). Returns the
    clip's new tmpfs path."""
    if not private or not src_clip:
        return src_clip
    d = ensure_session(session, True)
    dest = os.path.join(d, os.path.basename(src_clip))
    out_dir = _priv_output_dir(session)
    if _own_real_dir(out_dir) is True:
        for fn in os.listdir(out_dir):
            src = os.path.join(out_dir, fn)
            if os.path.isfile(src) and not os.path.islink(src):
                shutil.move(src, os.path.join(d, fn))
        shutil.rmtree(out_dir, ignore_errors=True)
    elif os.path.isfile(src_clip):  # fallback: at least relocate the named clip out of shared disk
        shutil.move(src_clip, dest)
    return dest


# ---- the burn: remove every private sink for a session ----
def burn(session):
    """Securely remove ALL private artifacts for `session`. Refuses an invalid name (never rmtree an
    unvalidated path). Symlink-aware (never deletes THROUGH a planted symlink) and VERIFIES removal
    — a path it could not remove is reported, not counted as burned. Returns (removed, failed)."""
    _require(session)
    import lucid_engine as E
    targets = [
        os.path.join(_runtime_root(), session),                            # tmpfs chain + clips
        os.path.join(E.INPUT_DIR, f".lucid-priv-{session}"),               # sealed anchor frames
        _priv_output_dir(session),                                         # any output leftovers
    ]
    removed, failed = [], []
    for p in targets:
        if not os.path.lexists(p):
            continue
        if os.path.islink(p):                          # never rmtree through a symlink
            try:
                os.unlink(p)                            # drop the link; the (foreign) target is left
            except OSError:
                pass
            failed.append(f"{p} (symlink — target NOT wiped)")
            continue
        shutil.rmtree(p, ignore_errors=True)
        (removed if not os.path.lexists(p) else failed).append(p)  # verify before counting burned
    return removed, failed


def clear(session):
    """Remove a session entirely before a fresh start — burn it if private, else drop the
    persistent dir. Returns the paths removed."""
    _require(session)
    removed, _failed = burn(session)
    pdir = session_dir(session, False)
    if os.path.isdir(pdir) and not os.path.islink(pdir):
        shutil.rmtree(pdir, ignore_errors=True)
        removed.append(pdir)
    return removed


def purge_persistent(session):
    """Delete a PERSISTENT dream's EVERY sink — its chain dir, its clips (output/lucid/) AND its
    anchor/seed frames (flat in ComfyUI input/). `clear()`/the old delete only dropped the chain
    dir, leaving clips + frames on disk (privacy-review: "delete must reach all three sinks").
    Reads the chain for the exact file set, with an allowlist-bounded prefix sweep as a safety net
    for orphans. Symlink-aware, verifies removal. Returns (removed, failed). Private sinks are the
    job of burn(); this only ever touches persistent paths, so it's a no-op on a private session."""
    _require(session)
    import lucid_engine as E
    removed, failed = [], []

    def _rm(p):
        if not p or not os.path.lexists(p):
            return
        if os.path.islink(p):                          # never delete THROUGH a planted symlink
            try:
                os.unlink(p)
            except OSError:
                pass
            failed.append(f"{p} (symlink — target NOT wiped)")
            return
        try:
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass
        (removed if not os.path.lexists(p) else failed).append(p)  # verify before counting deleted

    # 1. the precise, complete set named in the chain (clips + every anchor/seed frame)
    try:
        chain = load_chain(session, False)
    except Exception:
        chain = None
    if chain:
        for nd in chain.get("nodes", []):
            _rm(nd.get("clip"))
            of = nd.get("out_frame")
            if of and valid_name(os.path.basename(of)):
                _rm(os.path.join(E.INPUT_DIR, os.path.basename(of)))
    # 2. safety-net sweep for orphans — `session` is allowlist-validated, so the '{session}_' prefix
    #    can't traverse, and the trailing '_' keeps session "web" from ever matching "web2_*".
    for base in (E.INPUT_DIR, os.path.join(E.cc.COMFY_ROOT, "output", "lucid")):
        if os.path.isdir(base):
            for fn in os.listdir(base):
                if fn.startswith(f"{session}_"):
                    _rm(os.path.join(base, fn))
    # 3. the chain dir itself
    _rm(session_dir(session, False))
    return removed, failed


def new_session_id(name=None):
    """A fresh, collision-free, validated session id for a NEW dream — a slug of the user's `name`
    plus a short random suffix. So named dreams coexist in the library (the old single hardcoded
    'web' session clobbered the prior dream every start). Always passes valid_session()."""
    base = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip()).strip("-")[:40] or "dream"
    for _ in range(50):
        cand = f"{base}-{secrets.token_hex(3)}"
        if valid_session(cand) and not os.path.exists(session_dir(cand, False)) \
                and not os.path.exists(session_dir(cand, True)):
            return cand
    return "dream-" + secrets.token_hex(6)


def list_persistent():
    """The saved (non-private) dream LIBRARY: one metadata row per persistent session that has a
    readable chain.json, newest-edited first. Path-free by design (the web layer maps `session` ->
    routes); never lists a private dream (a private chain that somehow landed here is skipped, not
    leaked). Robust: a torn/foreign/symlinked dir is skipped, never raised on."""
    root = _persistent_root()
    if not os.path.isdir(root):
        return []
    out = []
    for s in os.listdir(root):
        d = os.path.join(root, s)
        cp = os.path.join(d, "chain.json")
        if not valid_session(s) or os.path.islink(d) or not os.path.isfile(cp):
            continue
        try:
            with open(cp) as f:
                chain = json.load(f)
        except Exception:
            continue
        if chain.get("private"):
            continue
        nodes = chain.get("nodes") or []
        tip = nodes[-1] if nodes else None
        out.append({
            "session": s,
            "name": chain.get("name") or s,
            "premise": chain.get("premise"),
            "created": chain.get("created"),
            "updated": os.path.getmtime(cp),
            "frames": len(nodes),
            "tip": (tip.get("id") if tip else None),
        })
    out.sort(key=lambda e: e.get("updated") or 0, reverse=True)
    return out


def list_private():
    """Live private sessions: those with a tmpfs session dir."""
    root = _runtime_root()
    if not os.path.isdir(root):
        return []
    return [s for s in os.listdir(root) if valid_session(s) and os.path.isdir(os.path.join(root, s))]


def _priv_queue_root():
    """tmpfs root for the ephemeral in-session private request queue (ADR-0019 §5). Sibling of the
    lucid-priv dream root; does not exist until the private queue ships."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "agentos", "lucid-priv-queue")


def list_priv_queue():
    """Live sessions holding an ephemeral private request in the tmpfs queue (ADR-0019 §5). Returns []
    until the private queue ships (the dir is absent), so the on-logout burn already covers the queue
    the day it lands — no second edit to the burn hook required (ADR-0019 Condition 1)."""
    root = _priv_queue_root()
    if not os.path.isdir(root):
        return []
    return [s for s in os.listdir(root) if valid_session(s)
            and os.path.isdir(os.path.join(root, s))]


def clear_priv_queue_dir():
    """Remove the whole tmpfs private-queue dir — the final sweep after the per-session burn on logout
    (ADR-0019 Condition 1). Only ever touches the tmpfs lucid-priv-queue root, and only if it is a real
    dir we own (a planted symlink / foreign dir is refused, never followed). True iff a dir was cleared."""
    root = _priv_queue_root()
    if _own_real_dir(root) is True:
        shutil.rmtree(root, ignore_errors=True)
        return not os.path.exists(root)
    return False


# ---- download scratch: where a "download the whole dream as one MP4" stitch is assembled ----
def _download_scratch_root():
    """tmpfs root for transient download-stitch workdirs. Sibling of the lucid-priv dream root."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(base, "agentos", "lucid-dl")


def make_download_workdir(private):
    """A fresh 0700 scratch dir for one stitched download; the caller removes it after streaming.

    PRIVATE dream -> a subdir of the tmpfs lucid-dl root (so the stitched — possibly private — MP4
    is built in RAM, never on shared disk, AND a crash leftover is swept by clear_download_scratch).
    The root is created through the symlink-refusing sealed-dir path (never makedirs+chmod, which
    follows a planted link). Persistent dream -> the OS temp dir (non-private bytes; OS-cleaned)."""
    import tempfile
    if private:
        root = _download_scratch_root()
        os.makedirs(os.path.dirname(root), exist_ok=True)   # the per-user 'agentos' parent
        _make_sealed(root)                                   # mkdir-or-validate; refuses a symlink
        return tempfile.mkdtemp(dir=root)
    return tempfile.mkdtemp(prefix="lucid-dl-")


def clear_download_scratch():
    """Reap every transient download-stitch workdir, in BOTH sinks:
      * the tmpfs lucid-dl root (PRIVATE downloads — the cardinal case: a stitched private MP4 must not
        linger in RAM with no reaper), and
      * orphaned `lucid-dl-*` dirs in the OS temp dir (NON-private downloads — make_download_workdir
        puts those in tempfile.gettempdir(), which the tmpfs sweep never touches; a SIGKILL mid-stitch
        would otherwise leave a full-dream MP4 on disk with no other cleaner).
    Run at startup and on stop, closing the same crash-orphan gap the per-session reap closes for clips.
    Every target is symlink-refusing + owned-by-us (so a foreign path sharing the prefix is never
    rmtree'd). True iff anything was actually cleared."""
    cleared = False
    root = _download_scratch_root()
    if _own_real_dir(root) is True:
        shutil.rmtree(root, ignore_errors=True)
        cleared = not os.path.exists(root)
    import glob as _glob
    import tempfile as _tempfile
    for d in _glob.glob(os.path.join(_tempfile.gettempdir(), "lucid-dl-*")):
        if _own_real_dir(d) is True:
            shutil.rmtree(d, ignore_errors=True)
            cleared = cleared or not os.path.exists(d)
    return cleared


def reap_orphans():
    """Burn any private sink (output/lucid-priv-*, input/.lucid-priv-*) whose tmpfs session is gone
    — a crash or logout can leave a clip + prompt-PNG on shared disk with no tmpfs index to find it
    (privacy-review HIGH). Run at start. Returns the sessions reaped."""
    import lucid_engine as E
    live = set(list_private())
    seen = set()
    for base, pat in [(os.path.join(E.cc.COMFY_ROOT, "output"), "lucid-priv-"),
                      (E.INPUT_DIR, ".lucid-priv-")]:
        if not os.path.isdir(base):
            continue
        for e in os.listdir(base):
            if e.startswith(pat):
                s = e[len(pat):]
                if valid_session(s):
                    seen.add(s)
    orphans = sorted(seen - live)
    for s in orphans:
        burn(s)
    return orphans
