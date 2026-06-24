# Create Video from Image — Dolphin ServiceMenu (integration)

Right-click any image in the file manager → **Create** → two image-to-video items:

- **Create Video from Image** — the normal path.
- **Create Video from Image (Private)** — ADR-0016 ephemeral session (tmpfs, not saved, not on
  the status hub, no wallpaper, auto-burned on logout).

Both are thin entries on **one already-written governed launcher**
(`apps/dreaming/lucid/create_from_image.py`). This integration only wires the menu and its
reversible install; it never generates anything itself. Every clip still goes through the same
governed path as Lucid: the **VRAM lease** (Spawn → confirm-evict → Release), the **deterministic
red-line prompt gate**, and the **B2 seed-likeness guard** (ADR-0015/0016/0017).

## Install / remove
```
./apply.sh      # render the template + install to ~/.local/share/kio/servicemenus/ (chmod +x)
./restore.sh    # remove it
```
Dolphin auto-rereads servicemenus; if the entry doesn't appear, restart Dolphin or run
`kbuildsycoca6`. The menu shows only on the still formats the i2v pipeline accepts
(png / jpeg / webp / gif / bmp / tiff). Multi-select spawns one process per image → one video
each (`%f`, single local path).

## How it works
- The `.desktop.in` template (`apps/dreaming/lucid/dist/agentos-create-video.desktop.in`) carries
  one `@LUCID_DIR@` token; `apply.sh` resolves the launcher's absolute dir from the repo layout
  (never hard-coded) and `sed`s it in. On Plasma 6 the install dir is
  `~/.local/share/kio/servicemenus/` — the old `kservices5/ServiceMenus` is dead — and the file
  **must be executable** for the user dir to authorize it.
- Generation is **not instant**: it needs the coordinator (`agentosd lease`) + ComfyUI + Ollama and
  takes minutes. The launcher is **honest and fails open** — if a resource is down it shows a calm
  "skipping" notification and falls back to the ambient shader; it never forces a GPU load or pops an
  error dialog.
- A **real-person seed triggers a consent dialog** (default Cancel) before anything runs; a possible
  **minor is hard-blocked** (non-overridable); "can't verify" refuses. That gate is the launcher's
  (B2) — a right-click only consents to *trying*, not to who is in the photo.
- **(Private)** is ADR-0016: ephemeral, tmpfs-only, nothing persisted.

## Showing creations in the Lucid app (:8765) — backend contract
Right-click creations were fire-and-forget (notifications only); they now also post to a small
**job board** so the Lucid web page can show them as a live queue. The launcher records each
NON-private creation via `lucid_jobs` (`queued → checking → generating → ready / skipped / blocked /
failed`); **private creations are never recorded** (ADR-0016). The board is tmpfs, atomic, self-pruned.

The page side is the frontend's to render — two small, additive hooks in `lucid_web.py`:

```python
import lucid_jobs as J
# 1) carry the board on the existing /api/state poll (no new poll needed):
#    in state():  return {... , "jobs": J.recent()}
# 2) stream a finished clip for a "ready" job (read-only GET /api/clip?id=…):
def _clip(self, job_id):
    p = J.clip_path(job_id)                 # validates: known job, status 'ready', real video file
    if not p:
        return self._send(404, "not found", "text/plain")
    data = open(p, "rb").read()
    self._send(200, data, "video/mp4")      # small clips; switch to chunked if they grow
```
Then render `s.jobs` as a "Creations" card (spinner for `generating`, inline `<video src="/api/clip?id=…">`
for `ready`, the `detail` string for skipped/blocked/failed). Pass the raw `id` straight to
`J.clip_path` — it validates; never join a client string onto a path. See `lucid_jobs.py` for the
full contract; `test_lucid_jobs.py` covers it.

## Still owed
Generation itself isn't guaranteed on a box where ComfyUI is already running outside the lease (the
launcher expects the lease to Spawn/own it, and admission needs headroom over current VRAM use) — so
a creation may legitimately land on `skipped`. Reconciling already-running vs lease-owned ComfyUI is
the next backend fix.
