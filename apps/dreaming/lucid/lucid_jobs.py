#!/usr/bin/env python3
"""Lucid job board — a small, visible record of "Create Video from Image" right-click creations so
the :8765 page can show them as a live queue (queued → checking → generating → ready / skipped /
blocked / failed) instead of the launcher being silent fire-and-forget.

NON-PRIVATE ONLY. A private creation (ADR-0016) is ephemeral and "never shown elsewhere", so it is
NEVER recorded here — the board is a hub-ish surface. The launcher simply doesn't call us for a
private job, so a private right-click leaves no trace on the board.

Storage: one small JSON file per job under $XDG_RUNTIME_DIR/agentos/lucid-jobs/ (tmpfs, 0700). The
board is transient by nature (a session's worth of creations) — atomically written, self-pruned by
age and count, and wiped on logout with the rest of tmpfs.

── Frontend contract (for the page owner in lucid_web.py) ───────────────────────────────────────
    import lucid_jobs as J
    J.recent()            → [ {id,title,status,created,updated,clip?,frame?,detail?}, … ] newest-first
    J.clip_path(job_id)   → an absolute mp4 path SAFE to stream, or None. Validates: known job,
                            status == 'ready', recorded path exists and is a video. Use it to back a
                            read-only `GET /api/clip?id=…` route (stream video/mp4); never join a
                            client string onto a filesystem path yourself.
  A job's `status` is one of STATUSES; `detail` is a short human reason for the non-ready states.
  Surfacing is optional and additive — if the page ignores us, nothing changes.

── Launcher contract (create_from_image.py) ─────────────────────────────────────────────────────
    jid = J.create("Create from image", job_id=session)   # status 'queued'
    J.update(jid, status="generating")
    J.update(jid, status="ready", clip=path, frame=ref)
"""
import glob
import json
import os
import tempfile
import time

JOBS_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()),
                        "agentos", "lucid-jobs")
MAX_AGE = 24 * 3600     # a board entry older than a day is stale — drop it
MAX_JOBS = 24           # keep the most recent N; the board is a glance, not an archive
STATUSES = ("queued", "checking", "generating", "ready", "held", "skipped", "blocked", "failed")


def _dir():
    os.makedirs(JOBS_DIR, mode=0o700, exist_ok=True)
    return JOBS_DIR


def _valid_id(job_id):
    return (isinstance(job_id, str) and 0 < len(job_id) <= 72
            and all(c.isalnum() or c in "_-" for c in job_id))


def _path(job_id):
    if not _valid_id(job_id):                 # never let an id escape the board dir (path traversal)
        raise ValueError("bad job id")
    return os.path.join(_dir(), job_id + ".json")


def _write(job):
    d = _dir()
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(job, f)
        os.replace(tmp, _path(job["id"]))      # atomic: a reader never sees a half-written record
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def create(title, job_id=None):
    """Open a job (status 'queued'). Returns its id. NON-private callers only."""
    job_id = job_id if _valid_id(job_id) else ("job_" + os.urandom(4).hex())
    now = time.time()
    _write({"id": job_id, "title": str(title)[:80], "status": "queued",
            "created": now, "updated": now})
    _prune()
    return job_id


def update(job_id, **fields):
    """Merge fields into a job (creating a stub if the record vanished). `status` is validated."""
    try:
        with open(_path(job_id)) as f:
            job = json.load(f)
    except Exception:
        job = {"id": job_id, "created": time.time()}
    if "status" in fields and fields["status"] not in STATUSES:
        fields.pop("status")
    job.update(fields)
    job["id"] = job_id
    job["updated"] = time.time()
    _write(job)
    return job


def recent(limit=MAX_JOBS):
    """All current board entries, newest first. Cheap (≤ MAX_JOBS small files)."""
    out = []
    for p in glob.glob(os.path.join(_dir(), "*.json")):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except Exception:
            continue
    out.sort(key=lambda j: j.get("created", 0), reverse=True)
    return out[:limit]


def clip_path(job_id):
    """The recorded clip path IFF the job is ready and the file is a real video we can stream;
    else None. The only path the page should ever serve — no client string touches the filesystem."""
    try:
        with open(_path(job_id)) as f:
            job = json.load(f)
    except Exception:
        return None
    clip = job.get("clip")
    if (job.get("status") == "ready" and isinstance(clip, str)
            and clip.lower().endswith((".mp4", ".webm", ".mkv")) and os.path.isfile(clip)):
        return clip
    return None


def _prune():
    """Age out stale records and cap the count — the board never grows without bound."""
    entries = []
    for p in glob.glob(os.path.join(_dir(), "*.json")):
        try:
            entries.append((os.path.getmtime(p), p))
        except OSError:
            continue
    now = time.time()
    fresh = []
    for m, p in entries:
        if now - m > MAX_AGE:
            try:
                os.remove(p)
            except OSError:
                pass
        else:
            fresh.append((m, p))
    for _m, p in sorted(fresh, reverse=True)[MAX_JOBS:]:
        try:
            os.remove(p)
        except OSError:
            pass
