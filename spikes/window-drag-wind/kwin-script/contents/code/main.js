// AgentOS — window-drag → wind producer (KWin 6 / Plasma 6, Wayland).
// Design 0023-window-drag-wind-producer.md  ·  ADR-0001 (don't reinvent) · ADR-0003 (fail-open)
//
// WHAT THIS IS
//   A tiny, read-only KWin script. It observes the geometry of a window WHILE THE USER
//   DRAGS IT and derives a single drag vector — direction (θ) + speed — from frameGeometry
//   deltas. On each (throttled) step it hands that RAW vector to the agentosd "wind sink"
//   over the SESSION D-Bus. The sink owns the calm low-pass and the atomic JSON write
//   (`wind.json`, sibling of `agent.json`). The shader consumer reads windDir/gust from there.
//
// WHY THE SPLIT (load-bearing, see spec §3 + §"sandbox")
//   A KWin script runs in a CONSTRAINED QML/JS sandbox: NO Qt file I/O (no QFile/fs.write),
//   and plain-JS scripts have NO reliable Timer/setTimeout primitive. The one sanctioned
//   escape hatch is `callDBus(...)` (async, SESSION bus only — confirmed: KWin scripts cannot
//   touch the system bus). So:
//     - this script does the cheap, sandbox-safe part: read frameGeometry, diff it, throttle;
//     - the Rust sink does the deterministic spring + atomic file write (where feed.rs already
//       proves that pattern). Determinism + the low-pass live in CODE, not in the sandbox.
//   Precedent for exactly this shape: maxiberta/kwin-system76-scheduler-integration (a KWin
//   script that callDBus-es a custom session-bus service).
//
// PRIVACY (load-bearing, spec §4 — pre-empts the privacy skeptic)
//   This script reads ONLY frameGeometry (x, y, width, height) deltas. It NEVER reads, stores,
//   or transmits a window's caption/title, resourceClass/resourceName, PID, windowId, or
//   contents. The D-Bus payload below is THREE floats + a flag. Verify by inspection: the only
//   `window.*` properties touched are `.frameGeometry`, `.move`, `.resize`, `.normalWindow`,
//   `.dock`, `.desktopWindow` — and the last three are read ONLY to DECIDE WHETHER to react,
//   never emitted. There is no code path that can serialize an identity.
//
// API FACTS (KWin 6, verified against develop.kde.org/docs/plasma/kwin/api/)
//   workspace.windowAdded(KWin::Window)                         — attach per-window handlers
//   workspace.stackingOrder : QList<KWin::Window*>              — windows present at load time
//   Window.interactiveMoveResizeStarted()                       — drag/resize begins
//   Window.interactiveMoveResizeStepped(QRectF geometry)        — fires per drag step (the live feed)
//   Window.interactiveMoveResizeFinished()                      — drag/resize ends
//   Window.frameGeometry : QRectF {x,y,width,height}            — current placed rect
//   Window.move : bool / Window.resize : bool                   — distinguishes a MOVE from a RESIZE
//   callDBus(service, path, iface, method, ...args, [cb])       — async, SESSION bus only
//   workspace.virtualScreenSize : QSizeF                        — for normalizing speed to screen units
//   (Plasma 6 renamed the old `clientStepUserMovedResized` → per-window interactiveMoveResize*,
//    and `Client` → `Window`. We use the new names; this is a Wayland-only design.)

"use strict";

// ----------------------------------------------------------------------------
// Tunables (all overridable via readConfig — see metadata.json config keys).
// These are PRODUCER-side knobs only; they do NOT set the calm. The calm (the eased
// ramp-back to neutral) lives in the sink/shader spring. These just bound + throttle
// what we EMIT so we don't spam the bus during a fast drag.
// ----------------------------------------------------------------------------
var WIND = {
    // D-Bus sink coordinates (the agentosd "wind" subcommand on the session bus).
    service: "org.agentos.Coordinator1",        // same well-known name the lease daemon owns
    path:    "/org/agentos/Wind",                // dedicated object path for the wind signal
    iface:   "org.agentos.Wind1",
    method:  "Gust",                              // Gust(d dirX, d dirY, d speed, b active)

    // Throttle: emit at most one Gust per this many ms during a drag. A drag step can fire
    // at the compositor's frame rate; the sink + shader spring don't need >~20 Hz, and the
    // bus shouldn't carry more. 50 ms ≈ 20 Hz.
    minIntervalMs: 50,

    // Speed normalization: pixels/second mapped to [0,1] gust. SPEED_AT_FULL px/s ⇒ gust 1.0.
    // Default ~ half the virtual-screen width per second feels like a firm flick. Overridable.
    speedAtFull: 1600.0,

    // Deadband: ignore sub-pixel jitter so a settled window doesn't trickle a tiny vector.
    minDeltaPx: 2.0,

    // React to a RESIZE drag too? Default false: a resize changes width/height, not position,
    // and "wind follows where you SHOVE the window" reads most honestly from a MOVE. Kept as a
    // knob because a resize-grab from an edge is arguably a directional shove too.
    reactToResize: false
};

// readConfig lets the KWin Scripts KCM / kwriteconfig6 override the above without editing JS.
function cfg(key, dflt) {
    try {
        var v = readConfig(key, dflt);
        return (v === undefined || v === null) ? dflt : v;
    } catch (e) { return dflt; }
}
WIND.speedAtFull   = Number(cfg("SpeedAtFull",   WIND.speedAtFull));
WIND.minIntervalMs = Number(cfg("MinIntervalMs", WIND.minIntervalMs));
WIND.minDeltaPx    = Number(cfg("MinDeltaPx",    WIND.minDeltaPx));
WIND.reactToResize = Boolean(cfg("ReactToResize", WIND.reactToResize));

// ----------------------------------------------------------------------------
// Per-drag state. We keep the previous frameGeometry centre + a timestamp so each
// step is a clean delta. No window identity is retained between drags — `dragState`
// holds geometry numbers only, and is reset on Finished.
// ----------------------------------------------------------------------------
function emitGust(dirX, dirY, speed, active) {
    // The ONLY thing that leaves this script. Three floats + a bool. No identity, ever.
    // Async + fire-and-forget (no callback): if the sink is down, the call simply no-ops —
    // that's the fail-open posture (ADR-0003). KWin must never block on us.
    try {
        callDBus(WIND.service, WIND.path, WIND.iface, WIND.method,
                 dirX, dirY, speed, active);
    } catch (e) {
        // Never throw out of a signal handler — a producer fault must not perturb window mgmt.
    }
}

// Decide whether a window's drags should produce wind. Normal app windows yes; the desktop,
// docks/panels, and OSDs no (dragging a panel isn't "weather"). These reads are for the
// DECISION only and are never emitted (privacy §4).
function isWindable(w) {
    if (!w) return false;
    if (w.desktopWindow || w.dock) return false;       // not the wallpaper layer / panels
    // normalWindow is the cleanest "this is an app window" gate exposed to scripts.
    return w.normalWindow === true;
}

function centreOf(geom) {
    return { x: geom.x + geom.width / 2.0, y: geom.y + geom.height / 2.0 };
}

function attach(w) {
    if (!w) return;

    // Per-window closure state. Lives only while handlers are connected; holds numbers, not identity.
    var st = { active: false, lastC: null, lastT: 0, lastEmit: 0 };

    function onStart() {
        if (!isWindable(w)) return;
        // A move is the canonical signal. A resize only counts if explicitly enabled.
        if (!w.move && !(WIND.reactToResize && w.resize)) return;
        st.active = true;
        st.lastC = centreOf(w.frameGeometry);
        st.lastT = Date.now();
        st.lastEmit = 0;
    }

    function onStep(/* QRectF geometry — we re-read frameGeometry for consistency */) {
        if (!st.active || !st.lastC) return;
        var now = Date.now();

        var c = centreOf(w.frameGeometry);
        var dx = c.x - st.lastC.x;
        var dy = c.y - st.lastC.y;
        var dt = (now - st.lastT) / 1000.0;          // seconds
        st.lastC = c;
        st.lastT = now;

        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < WIND.minDeltaPx || dt <= 0) return;   // deadband / no-time-elapsed guard

        // Throttle the bus traffic. We still updated lastC/lastT above so the NEXT emitted
        // step measures from here, not from the last EMITTED step (keeps speed honest).
        if (now - st.lastEmit < WIND.minIntervalMs) return;
        st.lastEmit = now;

        // Direction: normalized unit vector. KWin/Wayland y grows DOWNWARD; we emit raw
        // screen-space (dirX right+, dirY down+) and let the shader map to its own up-axis
        // (that mapping is a deterministic consumer-side constant — spec §3, no model).
        var dirX = dx / dist;
        var dirY = dy / dist;

        // Speed: px/s normalized to [0,1]. Clamp; the gust ceiling is the sink/shader's calm
        // guarantee, but we clamp here too so a teleport-sized delta can't emit a wild number.
        var pxPerSec = dist / dt;
        var speed = Math.min(pxPerSec / WIND.speedAtFull, 1.0);

        emitGust(dirX, dirY, speed, true);
    }

    function onFinish() {
        if (!st.active) return;
        st.active = false;
        st.lastC = null;
        // active=false tells the sink the drag ENDED — it begins easing wind back to neutral.
        // We do NOT zero the direction here; the sink holds last-good and decays gust → 0.
        emitGust(0.0, 0.0, 0.0, false);
    }

    // Connect. In KWin 6 these are per-window signals on KWin::Window.
    w.interactiveMoveResizeStarted.connect(onStart);
    w.interactiveMoveResizeStepped.connect(onStep);
    w.interactiveMoveResizeFinished.connect(onFinish);
}

// Attach to windows that already exist at load time...
var existing = workspace.stackingOrder || [];
for (var i = 0; i < existing.length; i++) attach(existing[i]);

// ...and to every window opened afterward. (windowAdded is the Plasma 6 name; the old
// clientAdded is gone.) We never disconnect explicitly — KWin tears the closures down with
// the window, and unloading the script (spec §6) drops every connection at once.
workspace.windowAdded.connect(attach);

print("[agentos-wind] window-drag → wind producer loaded "
      + "(speedAtFull=" + WIND.speedAtFull + " px/s, throttle=" + WIND.minIntervalMs + " ms, "
      + "resize=" + WIND.reactToResize + ")");
