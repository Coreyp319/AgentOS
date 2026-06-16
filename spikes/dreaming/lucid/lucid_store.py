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


def list_private():
    """Live private sessions: those with a tmpfs session dir."""
    root = _runtime_root()
    if not os.path.isdir(root):
        return []
    return [s for s in os.listdir(root) if valid_session(s) and os.path.isdir(os.path.join(root, s))]


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
