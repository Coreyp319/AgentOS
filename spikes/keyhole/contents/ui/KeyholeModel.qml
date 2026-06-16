/*
 * KeyholeModel.qml — the single source of truth for the keyhole's reactive state.
 *
 * Dependency-light ON PURPOSE (only QtQuick): the standalone qml6 harness reuses
 * this verbatim, and so does the real plasmoid. It owns:
 *   - the Timer-driven poll of keyhole.json (NEVER sync-XHR — that goes stale, see
 *     spikes/hills-reactive/README.md), re-creating the XMLHttpRequest each tick
 *   - the state -> {glyph SHAPE, text label} honesty map (never color-only)
 *   - the horizon-strip palette sampled from the `floats` (busy/warm/snag), never-red
 *   - first-class UNKNOWN, distinct from idle and from a real 0 (em-dash readouts)
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Item {
    id: model

    // --- Configuration -------------------------------------------------------
    // Absolute path to the feed file. The real producer writes
    //   $XDG_RUNTIME_DIR/nimbus-aurora/keyhole.json
    // The harness/mutate.sh override this to the spike's local sample.
    property string feedPath: ""
    property int pollIntervalMs: 2000   // 2s idle cadence (ADR-0012); back off under load is a producer concern
    property bool reducedMotion: false

    // --- Raw parsed contract (defaults are the SAFE-UNKNOWN posture) ---------
    property int  schema: 0
    property string state: "unknown"
    property string gateway: "unknown"
    property real busy: 0.0
    property real warm: 0.0
    property real snag: 0.0
    property var fleet: ({ running: -1, queued: -1, snagged: -1 })
    property var lease: ({ tier: "", holder: "", preempt: "" })
    property var vram:  ({ used_mib: -1, total_mib: -1 })
    property var residency: []
    property var tokensPerSec: null     // null === UNKNOWN, never synthesized

    // --- Freshness / liveness ------------------------------------------------
    property bool everLoaded: false     // have we ever parsed a good file?
    property bool fileReachable: false  // did the LAST poll succeed?
    property double lastGoodMs: 0       // Date.now() of last good parse
    // mtime-staleness: if the file stops changing AND gateway is not running,
    // we slide toward UNKNOWN. (In v1 the producer also writes gateway=unknown
    // on a dead Hermes, so this is belt-and-suspenders.)
    readonly property bool stale: everLoaded && (Date.now() - lastGoodMs) > (pollIntervalMs * 4)

    // The DISPLAYED state: UNKNOWN wins over a stale-but-cached look, per the
    // honesty contract. A never-reached file is UNKNOWN, not idle.
    readonly property string effectiveState: {
        if (!everLoaded) return "unknown"
        if (!fileReachable && stale) return "unknown"
        if (gateway === "unknown") return "unknown"
        return state
    }

    // --- The honesty map: state -> SHAPE glyph + TEXT label ------------------
    // Glyphs are distinct SHAPES (redundant with color), per a11y canon.
    //   idle ○ · working ◐ · needs_you ● · acting ▸ · snag ▢(dashed) · unknown —
    function glyphFor(s) {
        switch (s) {
        case "idle":      return "○"  // ○ hollow circle
        case "working":   return "◐"  // ◐ half-filled
        case "needs_you": return "●"  // ● solid
        case "acting":    return "▸"  // ▸ chevron
        case "snag":      return "▢"  // ▢ dashed-feel square (rendered dashed via border)
        case "unknown":   return "—"  // — em-dash
        default:          return "—"
        }
    }
    function labelFor(s) {
        switch (s) {
        case "idle":      return "Idle"
        case "working":   return "Working"
        case "needs_you": return "Needs your OK"
        case "acting":    return "Acting"
        case "snag":      return "Paused — waiting"
        case "unknown":   return "Status unavailable — can't reach Hermes"
        default:          return "Status unavailable"
        }
    }

    // --- Honest readout helpers (em-dash for any UNKNOWN scalar) -------------
    function emdash() { return "—" }
    function tokString() {
        // tokens/sec is UNKNOWN in v1 (needs the ADR-0002 proxy). NEVER synthesize.
        return (tokensPerSec === null || tokensPerSec === undefined) ? emdash() : (tokensPerSec.toFixed(0))
    }
    function vramString() {
        if (effectiveState === "unknown" || vram.used_mib < 0 || vram.total_mib < 0) return emdash()
        return (vram.used_mib / 1024).toFixed(1) + " / " + (vram.total_mib / 1024).toFixed(1) + " GB"
    }
    function vramFraction() {
        if (vram.used_mib < 0 || vram.total_mib <= 0) return 0
        return Math.max(0, Math.min(1, vram.used_mib / vram.total_mib))
    }
    function fleetString() {
        if (effectiveState === "unknown" || fleet.running < 0) return emdash() + " tasks"
        var parts = [fleet.running + " active"]
        if (fleet.queued  > 0) parts.push(fleet.queued + " queued")
        if (fleet.snagged > 0) parts.push(fleet.snagged + " snagged")
        return parts.join(" · ")
    }
    function leaseTierString() {
        if (effectiveState === "unknown" || !lease.tier) return emdash()
        return lease.tier + (lease.holder ? " (" + lease.holder + ")" : "")
    }
    function preemptString() {
        if (effectiveState === "unknown") return emdash()
        return lease.preempt && lease.preempt.length ? lease.preempt : "no contention"
    }
    function residencyString() {
        if (effectiveState === "unknown" || !residency || residency.length === 0) return emdash()
        var r = residency[0]
        var mins = Math.round((r.loaded_secs || 0) / 60)
        return r.name + " · loaded " + mins + "m"
    }

    // --- Horizon strip palette (the ONLY color; samples the Aurora dawn) -----
    // busy brightens; warm = a single dawn-glow ~rgb(255,153,87); snag desaturates.
    // NEVER red. UNKNOWN holds a dim memory of the last strip.
    readonly property color horizonColor: {
        // base calm twilight (deep blue-violet), low alpha
        var base = Qt.rgba(0.10, 0.13, 0.22, 1.0)
        if (effectiveState === "unknown") {
            // dim memory: gray-blue, no warmth, clearly de-energized
            return Qt.rgba(0.16, 0.18, 0.22, 1.0)
        }
        // busy lifts luma toward a pale cyan-white dawn
        var dawn = Qt.rgba(0.55 + 0.35 * busy, 0.62 + 0.30 * busy, 0.78 + 0.18 * busy, 1.0)
        // warm pulls toward the reserved dawn-glow rgb(255,153,87) = (1.0, 0.60, 0.34)
        var w = Math.max(0, Math.min(1, warm))
        var r = dawn.r + (1.00 - dawn.r) * w
        var g = dawn.g + (0.60 - dawn.g) * w
        var b = dawn.b + (0.34 - dawn.b) * w
        // snag desaturates toward luma (NEVER toward red): pull channels to gray
        var sg = Math.max(0, Math.min(1, snag))
        var luma = 0.30 * r + 0.59 * g + 0.11 * b
        r = r + (luma - r) * sg
        g = g + (luma - g) * sg
        b = b + (luma - b) * sg
        // snag also dims
        var dim = 1.0 - 0.35 * sg
        return Qt.rgba(r * dim, g * dim, b * dim, 1.0)
    }

    // --- THE POLL (pluggable read backend) ----------------------------------
    // PROVEN-IN-THIS-ENV (see README findings): qml6's XMLHttpRequest on file://
    // is DISABLED by default (Qt 6 security) and returns empty unless
    // QML_XHR_ALLOW_FILE_READ=1 is set — and plasmashell does NOT set it. So the
    // ROBUST production reader is Plasma5Support.DataSource (executable `cat`),
    // which reads the file with NO env flag. The plasmoid injects that via
    // `readBackend`; the standalone harness leaves it null and falls back to XHR
    // (with the flag) so this file stays dependency-light for the harness.
    //
    // CRITICAL either way: re-issue the read EACH tick (fresh request), NEVER a
    // one-shot sync read that caches and goes stale (the hills-reactive failure).

    // A function(path, onText) the host can inject. Plasmoid sets this to a
    // Plasma5Support-backed reader; harness leaves it null (uses XHR).
    property var readBackend: null

    function poll() {
        if (!feedPath || feedPath.length === 0) return
        if (readBackend) {
            readBackend(feedPath, _onText)        // production path (Plasma5Support cat)
        } else {
            _pollXhr()                            // harness fallback
        }
    }

    // Called by either backend with the raw file text (or "" on failure).
    function _onText(text) {
        if (text && text.length) {
            try {
                applyContract(JSON.parse(text))
                fileReachable = true
                everLoaded = true
                lastGoodMs = Date.now()
            } catch (e) {
                // malformed / half-written (.tmp rename race) -> hold last-good
                fileReachable = false
            }
        } else {
            fileReachable = false
        }
    }

    function _pollXhr() {
        var url = feedPath
        if (url.indexOf("file://") !== 0 && url.indexOf("qrc:") !== 0)
            url = "file://" + url
        var xhr = new XMLHttpRequest()   // FRESH each tick — do not hoist this out
        xhr.open("GET", url, true)       // async; no ?query (breaks file:// path resolution)
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            // file:// often reports status 0 on success; gate on responseText.
            var ok = (xhr.status === 0 || xhr.status === 200)
            _onText(ok ? xhr.responseText : "")
        }
        xhr.send()
    }

    function applyContract(d) {
        schema   = d.schema || 0
        state    = d.state || "unknown"
        gateway  = d.gateway || "unknown"
        if (d.floats) {
            busy = d.floats.busy !== undefined ? d.floats.busy : 0.0
            warm = d.floats.warm !== undefined ? d.floats.warm : 0.0
            snag = d.floats.snag !== undefined ? d.floats.snag : 0.0
        }
        if (d.fleet) fleet = d.fleet
        if (d.lease) lease = d.lease
        if (d.vram)  vram  = d.vram
        residency = d.residency || []
        // tokens_per_sec: respect null explicitly — do NOT coalesce to 0
        tokensPerSec = (d.tokens_per_sec === undefined) ? null : d.tokens_per_sec
    }

    Timer {
        interval: model.pollIntervalMs
        running: true
        repeat: true
        triggeredOnStart: true   // first read immediately, don't wait one interval
        onTriggered: model.poll()
    }
}
