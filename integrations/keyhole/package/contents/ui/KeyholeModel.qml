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
    // schema 3: the dominant GPU compute process (NVML) — the attribution Ollama-residency and the
    // lease daemon both miss, chiefly ComfyUI (the dreaming backend). name "" == none → row hidden.
    property var workload: ({ name: "", used_mib: -1 })
    property var tokensPerSec: null     // null === UNKNOWN, never synthesized
    // schema 2 (ADR-0019 §6): the local lucid queue, split into two HONEST counts.
    //   held         = "waiting for the graphics card" — CALM weather, count only, NEVER warm.
    //   needs_review = "needs your OK" — the warm-bloom cohort (warmth comes from feed.rs, not here).
    // 0 is a real "nothing waiting" datum (NOT -1/UNKNOWN). A schema-1 producer omits the field,
    // so we default to empty and let the `|| {...}` guard hold it. Two counts → two tray lines.
    property var pendingRequests: ({ held: 0, needs_review: 0 })
    // schema 4 (ADR-0041): the LIVE cross-workflow VRAM-demand queue (arbiter) — how many workflows are
    // blocked on WaitTurn for the lease RIGHT NOW + the tier served next. DISTINCT from pendingRequests
    // (the durable deferral buffer): this is who is actively in line for the GPU this moment. 0 is a real
    // "nothing in line" datum, never UNKNOWN; a pre-schema-4 file omits it and the `|| {...}` guard holds.
    property var queue: ({ depth: 0, next_tier: "" })
    // schema 5 (ADR-0051): the Check-ins cards — per-task Hermes rows read READ-ONLY from kanban.db.
    // [] is the only empty: no active tasks OR an unreadable kanban (the two SPLIT on effectiveState
    // === "unknown" — see checkInsEmptyReason). Cards carry the RAW kanban status; the VIEW derives the
    // creature mood HERE (checkInMood), so the producer ships no UI vocabulary and there is no
    // Rust/QML drift. A pre-schema-5 file omits the field → the `|| []` guard holds the empty default.
    property var checkIns: []
    property int checkInsTotal: -1      // pre-cap total for honest "N of M"; -1 == kanban unreadable
    property int gpuUtil: -1            // schema 5: real NVML GPU %; -1 == UNKNOWN (a real 0 == idle GPU)
    property var recurring: []          // schema 5: Hermes cron jobs (the cadence the tasks table lacks)

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

    // --- EARNED ring intensity + breath (ONE source for every surface) -------
    // Centralised so the tray glyph and the panel header token sample ONE function
    // (like they sample one horizonColor) — no hand-copied derivations that drift.
    // EARNED: the bloom is earned by GPU load, not pinned. needs_you is WARM +
    // present but CALM (a fixed contained floor — warmth asks for you, size does
    // not); unknown is a faint cold ghost (de-energized, distinct from idle's true
    // nothing); idle/snag stay byte-quiet (ring invisible).
    function ringIntensityFor(s, b) {
        // MONOTONIC with load so states stay distinguishable by glow:
        //   idle (quiet resting glow) < working/acting (clear bloom) ≤ needs_you (warm).
        // The working FLOOR sits well above idle so a resident-but-not-generating model
        // still reads as clearly more energised than rest (fixes "idle == busy").
        if (s === "needs_you") return 0.62
        if (s === "working" || s === "acting") return Math.max(0.55, Math.min(1.0, b))
        if (s === "unknown")  return 0.10
        if (s === "idle")     return 0.20   // gentle resting glow — VISIBLE but clearly dimmer + cooler + static vs working (departs from ADR-0012 "invisible-at-rest"; see amendment)
        return 0.0
    }
    function breathingFor(s) { return s === "working" || s === "needs_you" || s === "acting" }

    // --- aurora porthole: palette (tracks the active wallpaper) + energy ------
    // The porthole shares the wallpaper's dawn ramp AND reads the same floats, so the
    // keyhole and the nimbus-aurora wallpaper move together. wallpaperPalette (5 colour
    // stops, sky→base) overrides the default ramp when the host samples a static-image
    // wallpaper; null → the nimbus-aurora ramp (which matches the com.nimbus.aurora shader).
    property var wallpaperPalette: null
    // SECONDARY music-shimmer level for the porthole (level+beat, pre-gated by the
    // wallpaper's MusicReact). main.qml polls audio.json and sets it; 0 when silent/off.
    property real music: 0.0
    readonly property var auroraPalette: (wallpaperPalette && wallpaperPalette.length === 5)
        ? wallpaperPalette
        : [ Qt.rgba(0.05,0.06,0.13,1), Qt.rgba(0.30,0.26,0.46,1), Qt.rgba(0.20,0.31,0.64,1),
            Qt.rgba(0.11,0.17,0.40,1), Qt.rgba(0.04,0.06,0.16,1) ]
    // Overall brightness of the porthole — calm at idle, lifts under load (density-grows-with-load).
    function auroraEnergyFor(s, b) {
        // Wide idle↔working gap so working is "lit from within" and idle is "outline only"
        // — separable at a GLANCE (frozen), not reliant on the breath.
        if (s === "working" || s === "acting") return 1.10 + 0.30 * Math.max(0, Math.min(1, b))
        if (s === "needs_you") return 1.08
        if (s === "snag")      return 0.90
        if (s === "unknown")   return 0.68
        return 0.86   // idle — present but clearly the quietest lit state
    }
    // The reserved dawn-glow crests on needs_you regardless of the warm float (warmth is
    // folded upstream in feed.rs; this guarantees the cue even if the float reads 0).
    function warmFor(s, w) { return s === "needs_you" ? Math.max(0.85, w) : Math.max(0, w) }

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
    // schema 3: the dominant GPU compute process, e.g. "ComfyUI · 21.0 GB". "" when nothing heavy
    // runs (the producer gates on a heavy-VRAM floor) or under UNKNOWN — the FullRepresentation
    // HIDES the row on "" rather than showing a lone em-dash, keeping the panel calm at rest.
    function workloadString() {
        if (effectiveState === "unknown") return ""
        if (!workload || !workload.name || workload.name.length === 0) return ""
        var gb = (workload.used_mib > 0) ? (" · " + (workload.used_mib / 1024).toFixed(1) + " GB") : ""
        return workload.name + gb
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
        // schema 3: dominant GPU workload. A schema-≤2 file omits it → hold the empty default,
        // which reads as "nothing heavy / hide the row", never UNKNOWN.
        workload = d.workload || ({ name: "", used_mib: -1 })
        // tokens_per_sec: respect null explicitly — do NOT coalesce to 0
        tokensPerSec = (d.tokens_per_sec === undefined) ? null : d.tokens_per_sec
        // schema 2: lucid queue mirror. Backward-compatible — a schema-1 file lacks the field and
        // we hold the empty default (0,0), which reads as "nothing waiting", never UNKNOWN.
        pendingRequests = d.pending_requests || ({ held: 0, needs_review: 0 })
        // schema 4: live arbiter wait-queue. A pre-schema-4 file omits it → hold the empty default
        // (nothing in line), which reads calm, never UNKNOWN.
        queue = d.queue || ({ depth: 0, next_tier: "" })
        // schema 5 (ADR-0051): the Check-ins cards + GPU util + cron. A pre-schema-5 file omits them →
        // hold the empty/UNKNOWN defaults (never a fabricated task, never a guessed util).
        checkIns      = d.check_ins || []
        checkInsTotal = (d.check_ins_total === undefined) ? -1 : d.check_ins_total
        gpuUtil       = (d.gpu_util_pct === undefined) ? -1 : d.gpu_util_pct
        recurring     = d.recurring || []
    }

    // schema 2: the two tray lines (held = calm weather; needs_review = your-move). Empty queue →
    // empty strings so the FullRepresentation rows collapse and the panel returns to its idle look.
    // NEVER red, NEVER warm for `held` — the warm hue is reserved for needs_review (and is produced
    // upstream in feed.rs, not asserted here). UNKNOWN suppresses both (a local queue we can't read
    // says nothing — show nothing, never a guess).
    function heldString() {
        if (effectiveState === "unknown") return ""
        var n = (pendingRequests && pendingRequests.held) || 0
        return n > 0 ? (n + (n === 1 ? " held — GPU busy" : " held — GPU busy")) : ""
    }
    function needsReviewString() {
        if (effectiveState === "unknown") return ""
        var n = (pendingRequests && pendingRequests.needs_review) || 0
        return n > 0 ? (n + (n === 1 ? " needs your OK" : " need your OK")) : ""
    }
    // schema 4 (ADR-0041): the live arbiter wait-queue line — "N waiting · <tier> next". CALM weather,
    // like held: a count + an aggregate next-tier, NEVER warm and NEVER a waiter's identity (the
    // arbiter's no-leak contract). "" when nothing is in line or UNKNOWN, so the QUEUE row collapses and
    // the panel stays calm at rest (density grows with load — the ADR-0012 ethos).
    function queueString() {
        if (effectiveState === "unknown") return ""
        var n = (queue && queue.depth) || 0
        if (n <= 0) return ""
        var t = (queue && queue.next_tier) || ""
        return (n + " waiting") + (t.length ? (" · " + t + " next") : "")
    }

    // --- schema 5 (ADR-0051): Check-ins helpers ------------------------------
    // Mood + time derivations live HERE (not in the producer) so the contract stays raw and there is
    // ONE source of truth for the creature vocabulary — every card/creature/column samples these.
    //
    // RAW kanban status (+ failure count) → the creature mood. Precedence:
    //   needsyou (review = explicitly your move) > stalled (blocked / failing) > working > done > calm.
    function checkInMood(status, fails) {
        if (status === "review")  return "needsyou"
        if (status === "blocked" || (fails && fails > 0)) return "stalled"
        if (status === "running") return "working"
        if (status === "done" || status === "archived") return "done"
        return "calm"   // triage/todo/scheduled/ready/unknown → queued-calm
    }
    // Which board column a card sits in (SCHEDULED / NEEDS YOU / RUNNING / DONE).
    function checkInColumn(status, fails) {
        var m = checkInMood(status, fails)
        if (m === "needsyou") return "needs_you"
        if (m === "working" || m === "stalled") return "running"
        if (m === "done") return "done"
        return "scheduled"
    }
    // Hermes stamps are epoch SECONDS (int(time.time())); Date.now() is ms. em-dash for an unset stamp.
    function agoString(ts) {
        if (!ts || ts <= 0) return emdash()
        var s = Math.max(0, Math.floor(Date.now() / 1000 - ts))
        if (s < 60)    return s + "s ago"
        if (s < 3600)  return Math.floor(s / 60) + "m ago"
        if (s < 86400) return Math.floor(s / 3600) + "h ago"
        return Math.floor(s / 86400) + "d ago"
    }
    // "last check-in" = first present of heartbeat → started → created.
    function lastSeenString(card) {
        if (!card) return emdash()
        var ts = (card.last_heartbeat_at > 0) ? card.last_heartbeat_at
               : (card.started_at > 0)        ? card.started_at : card.created_at
        return agoString(ts)
    }
    // The card's status line — a mood-appropriate phrase that always carries the WORD (never
    // color-only): a recent error gist / the current step / running / a "last seen" relative time.
    function checkInStatusLine(card) {
        if (!card) return ""
        var m = checkInMood(card.status, card.consecutive_failures)
        if (m === "stalled" && card.last_error && card.last_error.length) return card.last_error
        if (m === "working") return (card.step && card.step.length) ? (card.step + " · running") : ("running · " + lastSeenString(card))
        if (m === "needsyou") return "needs your OK"
        if (m === "done")     return "done · " + lastSeenString(card)
        return "queued"   // calm
    }
    // A short local "blurt" for the poke bubble — the error gist if any, else a mood line.
    function checkInBlurt(card) {
        if (!card) return ""
        if (card.last_error && card.last_error.length) return card.last_error
        var m = checkInMood(card.status, card.consecutive_failures)
        if (m === "working")  return (card.step && card.step.length) ? (card.step + "…") : "working…"
        if (m === "needsyou") return "your move…"
        if (m === "done")     return "all done!"
        return "waiting…"
    }
    // Honest truncation: the producer caps the list; checkInsTotal is the pre-cap count.
    function checkInsTruncated()   { return checkInsTotal > 0 && checkInsTotal > (checkIns ? checkIns.length : 0) }
    function checkInsHiddenCount() { return checkInsTruncated() ? (checkInsTotal - checkIns.length) : 0 }
    // The two distinct empty states (no new flag — split on the existing UNKNOWN honesty).
    function checkInsEmptyReason() {
        return (effectiveState === "unknown") ? "Can't reach Hermes" : "No active check-ins"
    }
    // Header counts derived from the cards (no extra producer field needed).
    function checkInRunningCount() {
        var n = 0
        if (checkIns) for (var i = 0; i < checkIns.length; i++) {
            var m = checkInMood(checkIns[i].status, checkIns[i].consecutive_failures)
            if (m === "working" || m === "stalled") n++
        }
        return n
    }
    function checkInNeedsYouCount() {
        var n = 0
        if (checkIns) for (var i = 0; i < checkIns.length; i++)
            if (checkInMood(checkIns[i].status, checkIns[i].consecutive_failures) === "needsyou") n++
        return n
    }
    // GPU util for the rail — honest em-dash on UNKNOWN, a real "0%" when the GPU is genuinely idle.
    function gpuUtilString()   { return (effectiveState === "unknown" || gpuUtil < 0) ? emdash() : (gpuUtil + "%") }
    function gpuUtilFraction() { return (gpuUtil < 0) ? 0 : Math.max(0, Math.min(1, gpuUtil / 100)) }

    Timer {
        interval: model.pollIntervalMs
        running: true
        repeat: true
        triggeredOnStart: true   // first read immediately, don't wait one interval
        onTriggered: model.poll()
    }
}
