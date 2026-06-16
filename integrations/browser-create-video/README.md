# Create Video from Image — browser right-click surface (integration)

Right-click any image **on a web page** → **Create Video from Image** / **(Private)** → a local
image-to-video clip. This is the browser twin of the Dolphin ServiceMenu in
`integrations/create-video/`; both are thin entries on the *same* already-written governed
launcher (`spikes/dreaming/lucid/create_from_image.py`). This kit wires only the
browser→launcher bridge and its reversible install — **it never generates or decides anything.**

Two moving parts:

- `extension/` — a tiny cross-browser MV3 WebExtension (Firefox + Chromium). Adds the two
  context-menu items; on click it forwards the image `srcUrl` (+ a `private` flag) to a native
  host. It does **not** fetch the image or read the page.
- `host/` — a Python stdlib **native-messaging host** (`agentos_create_video_host.py`) that
  validates the message and spawns the launcher detached. Plus per-browser host-manifest
  templates (`*.json.in`).

## What the launcher does (so this kit doesn't have to)
Every clip goes through the same governed path as Lucid (ADR-0015/0016/0017):

- **VRAM lease** — Spawn → confirm-evict → Release (live AI preempts the batch lane).
- **Deterministic red-line prompt gate** + **B2 seed-likeness guard.**
- **Honest, fails open** — coordinator/ComfyUI/Ollama down → a calm "skipping" notification and
  fallback to the ambient shader; never an error dialog, never a forced GPU load.
- **Real person → consent dialog** (default Cancel) before anything runs; a **possible minor is
  hard-blocked** (non-overridable); "can't verify" refuses. The dialog and all notifications are
  the launcher's.
- **(Private)** = ADR-0016 ephemeral: tmpfs-only, not saved, not on the status hub, no wallpaper,
  auto-burned on logout.

It takes **minutes**, not seconds — this is async; the browser call returns immediately and a
desktop notification arrives when the clip is ready (or skipped).

## Honesty note — browser images are higher-risk seeds
A file you right-click in Dolphin is usually yours. **An image off a web page is usually someone
else's photo** — so this surface is a higher-risk seed source, and the **B2 consent gate is doing
real work here**, not ceremony. A right-click only consents to *trying*; it does not assert who is
in the photo. The third-party-likeness decision stays B2's: a real person triggers a consent
dialog you must accept, and a possible minor is blocked outright. Nothing this extension or host
does can turn that off — they only forward a URL.

## Trust boundary
The image URL comes from a web page's `srcUrl`, so it is attacker-influenced and is treated as
untrusted at every hop:

- The **host validates the scheme** — only `http(s)://` and `data:` are accepted; everything else
  (`file://`, `javascript:`, `blob:`, `chrome:`, bare paths, …) is rejected before any spawn.
  `file://` is deliberately refused here so a web page can't reach local files through us — the
  Dolphin ServiceMenu (`integrations/create-video/`) owns the local-file case.
- The host **never uses a shell** — the URL is passed as a single `argv` element
  (`subprocess.Popen([..., url], start_new_session=True, stdin/out/err=DEVNULL)`), so it can't be
  interpolated into a command. The spawn is detached so a minutes-long job can't hang the browser.
- The **launcher re-validates** the scheme and runs the **full B2 gate** again. A page therefore
  **cannot bypass safety by crafting a `srcUrl`** — the only thing that reaches the launcher is one
  validated URL string, and the launcher refuses or asks for consent exactly as it does for a
  local file.
- Chromium pins the host to one extension ID (`allowed_origins`) and Firefox to one extension ID
  (`allowed_extensions`), so a random page or other extension can't reach the host.

## Install / remove
```
./apply.sh      # wire the native host into Firefox/Chromium/Chrome (whichever are present)
./restore.sh    # remove the host manifests + reset the templated path
```
`apply.sh` resolves the launcher path from the repo layout (never hard-coded), bakes it into the
host script, makes it executable, and installs the host manifest to each present browser:

| Browser  | Native-host manifest path |
|---|---|
| Firefox  | `~/.mozilla/native-messaging-hosts/org.agentos.create_video.json` |
| Chromium | `~/.config/chromium/NativeMessagingHosts/org.agentos.create_video.json` |
| Chrome   | `~/.config/google-chrome/NativeMessagingHosts/org.agentos.create_video.json` |

Missing browsers are detected and skipped with a note. Then load the extension unpacked —
see `extension/README.md`.

### Chrome extension-ID step (required for Chrome/Chromium)
Chromium native messaging refuses any origin not listed in the host manifest. After **Load
unpacked**, copy the extension's **ID** from `chrome://extensions` and replace
`@CHROME_EXTENSION_ID@` in the installed `org.agentos.create_video.json` — either re-run
`./apply.sh` after pasting it into the template, or edit the installed file directly. **Firefox
needs no such step** — it matches by the pinned `create-video@agentos` id.

## Still owed
- A first-class install path (folder this kit into `integrations/apply-all.sh` once the extension
  ships signed, not just unpacked/temporary).
- A bundled icon set and a packaged `.xpi` / `.crx` so it survives a Firefox restart and a Chrome
  re-launch without re-loading unpacked.
