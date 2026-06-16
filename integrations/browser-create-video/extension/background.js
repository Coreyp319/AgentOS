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

const HOST = "org.agentos.create_video";
const ITEMS = [
  { id: "create-video", title: "Create Video from Image", private: false },
  { id: "create-video-private", title: "Create Video from Image (Private)", private: true },
];

// Recreate the menu items idempotently on install/update/startup.
function buildMenus() {
  api.contextMenus.removeAll(() => {
    // Reading lastError here just drains it; removeAll on a fresh profile has none.
    void api.runtime.lastError;
    for (const it of ITEMS) {
      api.contextMenus.create({
        id: it.id,
        title: it.title,
        contexts: ["image"],
      });
    }
  });
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

  // sendNativeMessage returns a Promise on Firefox and takes a callback on Chromium.
  // The callback form works on both (Firefox supports it too), so use it and read lastError.
  try {
    api.runtime.sendNativeMessage(HOST, payload, (response) => {
      const err = api.runtime.lastError;
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
    });
  } catch (e) {
    toast("AgentOS native helper not reachable", "Is the native host installed? See apply.sh.");
    console.error("[AgentOS Create Video] sendNativeMessage threw:", e);
  }
});
