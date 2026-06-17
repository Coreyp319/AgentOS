/*
 * ServicesModel.qml — the keyhole's view of the AgentOS boot-health board.
 *
 * Polls the status-panel's /status.json (integrations/status-panel) using the SAME
 * pluggable read-backend pattern as KeyholeModel: the plasmoid injects a
 * Plasma5Support executable `curl` reader; the standalone harness leaves it null and
 * falls back to XHR (http XHR is allowed — only file:// is blocked, see KeyholeModel).
 *
 * Read-only and HONEST: an unreachable panel reads UNAVAILABLE, never faked health.
 * Polls ONLY while the popup is open (`active`) at a calm cadence — the panel shells
 * out to `systemctl` per request, so we don't probe it when nobody is looking.
 *
 * Dependency-light (QtQuick only) so the harness can reuse it verbatim.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick

Item {
    id: svc

    // --- Configuration -------------------------------------------------------
    property string url: "http://127.0.0.1:9123/status.json"
    property int pollIntervalMs: 8000      // calm; only runs while `active`
    property bool active: false            // host sets true while the popup is open

    // --- Parsed contract (SAFE-UNAVAILABLE defaults) -------------------------
    property var groups: []
    property var services: []              // flat, already grouped in catalog order
    property var summary: ({ total: -1, healthy: -1, attention: -1 })
    property var rows: []                  // flattened display rows: {kind:"group"|"svc", …}

    // --- Progressive disclosure ---------------------------------------------
    // A section whose members are ALL the same tone (all green, all red, …) folds
    // to a single row: just the section header, painted that tone's colour. The
    // per-row detail is a click away (collapsed by default). Mixed sections always
    // show their members. `expanded` holds the user's per-section overrides (name ->
    // wants-expanded); `_meta` is the parsed-but-not-yet-flattened section list that
    // toggle() rebuilds from.
    property var expanded: ({})
    property var _meta: []
    // Deterministic board height (px) from the row count — the view sizes from THIS,
    // never from ListView.contentHeight (which only counts realized delegates and so
    // collapses to ~0 under any height pressure in a popup). Must match the delegate
    // row heights in FullRepresentation: group header 22, service row 20.
    readonly property int rowGroupPx: 22
    readonly property int rowSvcPx: 20
    readonly property int boardPx: {
        var px = 0
        for (var i = 0; i < rows.length; ++i)
            px += (rows[i].kind === "group" ? rowGroupPx : rowSvcPx)
        return px
    }

    // --- Liveness ------------------------------------------------------------
    property bool everLoaded: false
    property bool reachable: false
    property double lastGoodMs: 0
    readonly property bool stale: everLoaded && (Date.now() - lastGoodMs) > (pollIntervalMs * 3)
    // The panel is "available" to render only with a fresh, reachable read.
    readonly property bool available: everLoaded && reachable && !stale

    // --- Honesty: status -> SHAPE glyph (redundant with colour) --------------
    //   up ● · ok ✓ · starting/stopping ◐ · failed ▲ · down ○ · absent · · unknown —
    function glyphFor(s) {
        switch (s) {
        case "up":       return "●"   // ● running daemon
        case "ok":       return "✓"   // ✓ task ran clean
        case "starting": return "◐"   // ◐ activating
        case "stopping": return "◐"   // ◐ deactivating
        case "failed":   return "▲"   // ▲ alert
        case "down":     return "○"   // ○ not up
        case "absent":   return "·"   // · not installed
        default:         return "—"   // — unknown
        }
    }

    // Replicates the panel's _is_attention (we carry kind + reach per row).
    function isAttention(s) {
        if (!s) return false
        if (s.status === "failed") return true
        if (s.status === "down" && (s.kind || "daemon") === "daemon") return true
        if ((s.status === "up" || s.status === "starting") && s.reach === "unreachable") return true
        return false
    }
    // tone: "attention" | "transitional" | "healthy" | "idle" — view maps to colour.
    function toneFor(s) {
        if (isAttention(s)) return "attention"
        if (!s) return "idle"
        if (s.status === "starting" || s.status === "stopping") return "transitional"
        if (s.status === "up" || s.status === "ok") return "healthy"
        return "idle"
    }

    function summaryString() {
        if (!available) return "unavailable"
        if (summary.attention > 0) return summary.attention + " need attention"
        return summary.healthy + " / " + summary.total + " healthy"
    }

    // --- Poll (pluggable backend; same shape as KeyholeModel.readBackend) ----
    property var readBackend: null         // function(onText). Plasmoid injects curl.
    property bool _inflight: false

    function poll() {
        if (_inflight) return               // never stack probes on a slow panel
        _inflight = true
        if (readBackend) readBackend(_onText)
        else _pollXhr()
    }

    function _onText(text) {
        _inflight = false
        if (text && text.length) {
            try {
                _apply(JSON.parse(text))
                reachable = true; everLoaded = true; lastGoodMs = Date.now()
                return
            } catch (e) { /* half-written / not JSON -> hold last-good, mark unreachable */ }
        }
        reachable = false
    }

    function _apply(d) {
        groups = d.groups || []
        services = d.services || []
        summary = d.summary || ({ total: -1, healthy: -1, attention: -1 })
        // Parse into per-section metadata (tone uniformity + a condensed summary),
        // honouring catalog order and skipping empty sections. The visible row list
        // is built from this + the expand overrides, so a toggle re-flattens without
        // re-fetching, and a poll preserves the user's overrides.
        var meta = []
        var gs = groups.length ? groups : _derivedGroups()
        for (var i = 0; i < gs.length; ++i) {
            var g = gs[i]
            var members = services.filter(function (s) { return (s.group || "Other") === g })
            if (!members.length) continue
            meta.push(_sectionMeta(g, members))
        }
        _meta = meta
        _rebuildRows()
    }

    // Describe a section: its shared tone and whether it condenses. "Condensable"
    // == every member reads the SAME tone (and there's more than one to fold away).
    // The condensed row is just the header in that tone's colour; a leading shape
    // glyph keeps it legible without colour (codebase rule: never colour-only).
    function _sectionMeta(name, members) {
        var tone = toneFor(members[0])
        var uniform = true
        for (var i = 1; i < members.length; ++i)
            if (toneFor(members[i]) !== tone) { uniform = false; break }
        var collapsible = uniform && members.length > 1
        // Shape cue: the shared status glyph if every member reads the same status,
        // else the tone's representative shape.
        var status = members[0].status
        for (var k = 1; k < members.length; ++k)
            if (members[k].status !== status) { status = null; break }
        var toneGlyph = (tone === "attention") ? "▲"
                      : (tone === "transitional") ? "◐"
                      : (tone === "healthy") ? "●" : "·"
        return {
            name: name,
            members: members,
            count: members.length,
            collapsible: collapsible,
            tone: tone,
            glyph: collapsible ? (status ? glyphFor(status) : toneGlyph) : ""
        }
    }

    // Effective collapsed state: condensable sections default collapsed; a user
    // override (expanded[name] = wants-expanded) wins. Non-condensable: never.
    function isCollapsed(m) {
        if (!m.collapsible) return false
        var ov = expanded[m.name]
        return (ov === undefined) ? true : !ov
    }

    function _rebuildRows() {
        var out = []
        for (var i = 0; i < _meta.length; ++i) {
            var m = _meta[i]
            var collapsed = isCollapsed(m)
            out.push({
                kind: "group", name: m.name,
                collapsible: m.collapsible, collapsed: collapsed,
                tone: m.tone, glyph: m.glyph, count: m.count
            })
            if (!collapsed)
                for (var j = 0; j < m.members.length; ++j)
                    out.push({ kind: "svc", svc: m.members[j] })
        }
        rows = out
    }

    // Flip a condensable section open/closed (no-op on non-condensable ones).
    // Rebuilds the visible rows (and thus boardPx) so the popup resizes to fit.
    function toggle(name) {
        for (var i = 0; i < _meta.length; ++i) {
            if (_meta[i].name === name && _meta[i].collapsible) {
                var e = {}
                for (var k in expanded) e[k] = expanded[k]
                e[name] = isCollapsed(_meta[i])   // collapsed -> open; open -> collapse
                expanded = e
                _rebuildRows()
                return
            }
        }
    }

    function _derivedGroups() {
        var seen = {}, out = []
        for (var i = 0; i < services.length; ++i) {
            var g = services[i].group || "Other"
            if (!seen[g]) { seen[g] = true; out.push(g) }
        }
        return out
    }

    function _pollXhr() {
        var xhr = new XMLHttpRequest()      // FRESH each tick
        xhr.open("GET", url, true)
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            var ok = (xhr.status === 0 || xhr.status === 200)
            _onText(ok ? xhr.responseText : "")
        }
        xhr.send()
    }

    Timer {
        interval: svc.pollIntervalMs
        running: svc.active
        repeat: true
        triggeredOnStart: true              // opening the popup fetches at once
        onTriggered: svc.poll()
    }
}
