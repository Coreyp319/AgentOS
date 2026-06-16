# AgentOS — Create Video from Image (WebExtension)

A tiny cross-browser (Firefox + Chromium) MV3 extension that adds two image right-click items:

- **Create Video from Image**
- **Create Video from Image (Private)** — ADR-0016 ephemeral session

On click it sends the image's `srcUrl` (plus a `private` flag) to the native-messaging host
`org.agentos.create_video`, which hands it to the governed launcher. **The extension fetches
nothing, generates nothing, and makes no safety decision** — the launcher owns download, the
B2 seed-likeness consent gate, the VRAM lease, private mode, and all notifications.

Install the native host first (`../apply.sh`), then load this extension unpacked:

## Load unpacked

- **Firefox** — `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → pick
  `manifest.json` in this folder. (Temporary add-ons clear on browser restart; re-add to test
  again.) The extension ID is pinned to `create-video@agentos`, which the Firefox native-host
  manifest already allow-lists — no extra step.
- **Chrome / Chromium** — `chrome://extensions` → enable **Developer mode** → **Load unpacked**
  → pick this `extension/` folder. Then copy the extension's **ID** (shown under its card) into
  the Chrome native-host manifest's `allowed_origins` — either re-run `../apply.sh` after pasting
  it, or edit the installed `org.agentos.create_video.json` directly and replace
  `@CHROME_EXTENSION_ID@`. Chromium refuses native messaging from an unlisted origin.

## Files
- `manifest.json` — MV3. Carries BOTH `background.service_worker` (Chromium) and
  `background.scripts` (Firefox), plus `browser_specific_settings.gecko.id` so Firefox accepts
  it. `permissions: contextMenus, nativeMessaging, notifications`. No host permissions and no
  `<all_urls>` — we never read page content or fetch the image; we only forward `info.srcUrl`.
- `background.js` — creates the two menu items; on click calls
  `runtime.sendNativeMessage("org.agentos.create_video", {url, private})`. Uses a
  `globalThis.browser ?? globalThis.chrome` shim. If the host is missing it catches `lastError`
  and shows a "native helper not reachable" toast (degrades to a console error).

## Icons
None bundled. The `icons` key is omitted on purpose so there are no missing-file load errors.
