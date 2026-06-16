# Create Video from Image — Dolphin ServiceMenu (integration)

Right-click any image in the file manager → **Create** → two image-to-video items:

- **Create Video from Image** — the normal path.
- **Create Video from Image (Private)** — ADR-0016 ephemeral session (tmpfs, not saved, not on
  the status hub, no wallpaper, auto-burned on logout).

Both are thin entries on **one already-written governed launcher**
(`spikes/dreaming/lucid/create_from_image.py`). This integration only wires the menu and its
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
- The `.desktop.in` template (`spikes/dreaming/lucid/dist/agentos-create-video.desktop.in`) carries
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

## Still owed
The browser WebExtension surface that shares this same launcher (right-click an image on a web
page → Create) — the native-messaging host side. This menu is the file-manager half only.
