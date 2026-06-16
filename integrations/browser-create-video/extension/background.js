// AgentOS — Create Video from Image (browser right-click surface)
//
// This extension does almost nothing on purpose. It does NOT fetch the image, it does NOT
// generate anything, and it makes NO safety decision. It passes one string — the image's
// srcUrl — to a native-messaging host, which hands it to the already-written governed
// launcher (create_from_image.py). The launcher owns the real work: download, the B2
// seed-likeness consent gate (real person → dialog; possible minor → hard block), the VRAM
// lease, ephemeral/private mode, and all notifications. A crafted srcUrl cannot bypass any
// of that — the host re-validates the scheme and the launcher re-validates everything.
//
// Cross-browser: Firefox exposes `browser`, Chromium exposes `chrome`. Same MV3 APIs.
const api = globalThis.browser ?? globalThis.chrome;
// Firefox exposes `browser` (promise-based APIs); Chromium exposes `chrome` (callback-based).
// This one difference matters: a callback passed to `browser.*` is IGNORED, so callback-style
// code silently no-ops on Firefox. Detect once and branch where it counts.
const isFirefox = typeof globalThis.browser !== "undefined" && !!globalThis.browser.runtime;

const HOST = "org.agentos.create_video";
const PARENT = "agentos-create";
const ITEMS = [
  { id: "create-video", title: "Create Video from Image", private: false },
  { id: "create-video-private", title: "Create Video from Image (Private)", private: true },
];

// Recreate the menus idempotently. async/await works on BOTH browsers: on Firefox removeAll()
// returns a promise we await; on Chromium it returns undefined and `await undefined` is a no-op —
// either way the create() calls run AFTER the clear. (The old code nested create() inside a
// removeAll(callback); Firefox never fired that callback, so no items appeared.)
async function buildMenus() {
  try {
    await api.contextMenus.removeAll();
  } catch (_e) {
    /* fresh profile / nothing to remove */
  }
  // A "Create" submenu so the browser mirrors the Dolphin "Create" section.
  api.contextMenus.create({ id: PARENT, title: "Create", contexts: ["image"] });
  for (const it of ITEMS) {
    api.contextMenus.create({ id: it.id, parentId: PARENT, title: it.title, contexts: ["image"] });
  }
}

api.runtime.onInstalled.addListener(buildMenus);
// Firefox event pages / Chromium service workers both re-run this on wake; rebuild defensively.
if (api.runtime.onStartup) {
  api.runtime.onStartup.addListener(buildMenus);
}

// Best-effort toast. notifications permission is declared; if a browser lacks it we degrade to console.
function toast(title, message) {
  try {
    if (api.notifications && api.notifications.create) {
      api.notifications.create({
        type: "basic",
        title,
        message,
        // iconUrl intentionally omitted — Chromium tolerates it; avoids shipping an asset.
      });
      return;
    }
  } catch (_e) {
    /* fall through to console */
  }
  console.error(`[AgentOS Create Video] ${title}: ${message}`);
}

function isPrivateItem(menuItemId) {
  const item = ITEMS.find((i) => i.id === menuItemId);
  return item ? item.private : false;
}

api.contextMenus.onClicked.addListener((info) => {
  const url = info.srcUrl;
  if (typeof url !== "string" || url.length === 0) {
    toast("AgentOS — Create Video", "No image URL was found for that element.");
    return;
  }
  const payload = { url, private: isPrivateItem(info.menuItemId) };

  // One response handler for both browsers. err is non-null only on failure.
  const handle = (response, err) => {
    if (err) {
      // Most common cause: the native host manifest isn't installed (run apply.sh),
      // or (Chrome) the manifest's allowed_origins doesn't list THIS extension's ID.
      toast(
        "AgentOS native helper not reachable",
        "Run integrations/browser-create-video/apply.sh, then reload this extension. " +
          "On Chrome, also paste this extension's ID into the native host manifest.",
      );
      console.error("[AgentOS Create Video] native messaging error:", err.message || err);
      return;
    }
    if (response && response.ok) {
      toast(
        payload.private ? "Creating your private video…" : "Creating your video…",
        "The launcher is handling it — watch for a desktop notification. " +
          "A real-person image will ask for consent first.",
      );
    } else {
      const reason = (response && response.error) || "the helper refused the request";
      toast("AgentOS — Create Video", `Not started: ${reason}.`);
    }
  };

  // Firefox: promise-based (a trailing callback is ignored). Chromium: callback-based.
  try {
    if (isFirefox) {
      api.runtime
        .sendNativeMessage(HOST, payload)
        .then((response) => handle(response, null))
        .catch((e) => handle(null, e));
    } else {
      api.runtime.sendNativeMessage(HOST, payload, (response) =>
        handle(response, api.runtime.lastError),
      );
    }
  } catch (e) {
    toast("AgentOS native helper not reachable", "Is the native host installed? See apply.sh.");
    console.error("[AgentOS Create Video] sendNativeMessage threw:", e);
  }
});
