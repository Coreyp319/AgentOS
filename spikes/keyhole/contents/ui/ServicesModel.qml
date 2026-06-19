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
    property var _prevTones: ({})       // section name -> tone last poll (transition latch)

    // Stable-identity view model. The VIEW binds to THIS, not the `rows` array: a
    // poll updates rows in place (set) so unchanged delegates persist and the board
    // is STILL at rest — only a genuine change moves a pixel. Expand/collapse
    // insert/remove rows so the ListView can fade them. `rows` (the plain array)
    // stays the deterministic source for boardPx and the headless tests.
    ListModel { id: boardModel; dynamicRoles: true }
    property alias rowModel: boardModel
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
        // Latch tone transitions so the earned motion (tint sunrise / attention ember)
        // fires ONCE on a real change and never on the steady 8s poll. The model
        // rebuilds wholesale, so the previous tones must be remembered explicitly.
        var pt = {}
        for (var t = 0; t < meta.length; ++t) {
            meta[t].toneChanged = (_prevTones[meta[t].name] !== undefined)
                                  && (_prevTones[meta[t].name] !== meta[t].tone)
            pt[meta[t].name] = meta[t].tone
        }
        _prevTones = pt
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

    // The colour-blind-safe, count-bearing predicate read aloud by a screen reader and
    // reused so a tinted header is never a colour-only health assertion. Mirrors
    // summaryString()'s wording so the two can't drift.
    function predicateFor(tone, count) {
        switch (tone) {
        case "attention":    return count + " need attention"
        case "transitional": return count + " starting"
        case "healthy":      return count + " healthy"
        default:             return count + " idle"
        }
    }

    // Every row carries the FULL union of roles (group fields + svc fields). A
    // ListModel registers its delegate roles from the FIRST element, so a later svc
    // role would be invisible in the delegate unless it already exists on row 0.
    function _row(o) {
        return {
            key: o.key, kind: o.kind,
            name: o.name || "", collapsible: o.collapsible === true,
            collapsed: o.collapsed === true, tone: o.tone || "idle",
            glyph: o.glyph || "", count: o.count || 0,
            toneChanged: o.toneChanged === true, predicate: o.predicate || "",
            group: o.group || "", svcName: o.svcName || "", svcStatus: o.svcStatus || "",
            svcState: o.svcState || "", svcUrl: o.svcUrl || "", svcTone: o.svcTone || "idle"
        }
    }

    function _rebuildRows() {
        var out = []
        for (var i = 0; i < _meta.length; ++i) {
            var m = _meta[i]
            var collapsed = isCollapsed(m)
            out.push(_row({
                key: "g:" + m.name, kind: "group", name: m.name,
                collapsible: m.collapsible, collapsed: collapsed,
                tone: m.tone, glyph: m.glyph, count: m.count,
                toneChanged: m.toneChanged === true,
                predicate: m.collapsible ? predicateFor(m.tone, m.count) : ""
            }))
            if (!collapsed)
                for (var j = 0; j < m.members.length; ++j) {
                    var s = m.members[j]
                    out.push(_row({
                        key: "s:" + m.name + "/" + (s.id || s.name || j),
                        kind: "svc", group: m.name,
                        svcName: s.name, svcStatus: s.status,
                        svcState: s.state, svcUrl: s.url,
                        svcTone: toneFor(s)
                    }))
                }
        }
        rows = out
        _syncModel(out)
    }

    // Reconcile boardModel to `out` with the FEWEST ops (keyed): drop gone rows,
    // insert/move into order, set the rest in place. A no-change poll therefore only
    // re-sets identical roles (no signal, no churn) → the board is still at rest;
    // an expand inserts svc rows (ListView fades them in) without disturbing the
    // sibling sections. This stable identity is what lets a tint Behavior animate a
    // real tone change instead of being re-created flat every poll.
    function _syncModel(out) {
        var wantKeys = {}
        for (var i = 0; i < out.length; ++i) wantKeys[out[i].key] = true
        for (var r = boardModel.count - 1; r >= 0; --r)
            if (!wantKeys[boardModel.get(r).key]) boardModel.remove(r)
        for (var j = 0; j < out.length; ++j) {
            var want = out[j]
            var cur = -1
            for (var k = j; k < boardModel.count; ++k)
                if (boardModel.get(k).key === want.key) { cur = k; break }
            if (cur === -1) boardModel.insert(j, want)
            else { if (cur !== j) boardModel.move(cur, j, 1); boardModel.set(j, want) }
        }
        while (boardModel.count > out.length) boardModel.remove(boardModel.count - 1)
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
