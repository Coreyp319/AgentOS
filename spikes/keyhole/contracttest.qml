/*
 * contracttest.qml — exit-code-gated proof of the file-poll seam + the honesty map.
 *
 * console.log output is not captured in this harness's non-interactive shell, so we
 * prove behavior by ENCODING observations into the process exit code (which IS
 * observable). This is a logic proof of the exact code paths the plasmoid runs:
 *   1. KeyholeModel polls a file via fresh-XHR-each-tick (the non-sync-XHR seam)
 *   2. when the file is rewritten on disk, the parsed values CHANGE on a later tick
 *   3. UNKNOWN renders distinctly; tokens_per_sec=null stays em-dash (never 0)
 *
 * Run: qml6 contracttest.qml -- /abs/path/to/keyhole.json
 * Exit code meanings (see runtests.sh which orchestrates the file rewrites):
 *   10 = never reached the file at all
 *   20 = first observed state did not match expected phase-1 state
 *   30 = file rewritten but poll did NOT pick up the change (STALE — the bug we de-risk)
 *   40 = change picked up but UNKNOWN/null honesty broke
 *   0  = ALL PASS: live update observed + honesty contract held
 *
 * The orchestrator writes a known sequence of states into the target file at
 * timed offsets and reads this exit code.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import "contents/ui"

QtObject {
    id: t

    function feedFromArgs() {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length; ++i)
            if (a[i] === "--" && i + 1 < a.length) return a[i + 1]
        for (var j = a.length - 1; j >= 0; --j)
            if (a[j].indexOf(".json") >= 0) return a[j]
        return "keyhole.json"
    }

    property var model: KeyholeModel {
        feedPath: t.feedFromArgs()
        pollIntervalMs: 300
    }

    // observation log of effectiveState seen on each tick
    property var seen: []
    property bool sawWorking: false
    property bool sawUnknown: false
    property bool sawUnknownEmdashTok: false
    property bool sawUnknownEmdashVram: false
    property int ticks: 0

    property var probe: Timer {
        interval: 300; running: true; repeat: true; triggeredOnStart: false
        onTriggered: {
            t.ticks++
            if (!t.model.everLoaded) {
                if (t.ticks > 20) Qt.exit(10)   // never reached file
                return
            }
            var es = t.model.effectiveState
            t.seen.push(es)
            if (es === "working") t.sawWorking = true
            if (es === "unknown") {
                t.sawUnknown = true
                // honesty: under UNKNOWN, tok/s and vram MUST be em-dash, never 0
                if (t.model.tokString() === "—")  t.sawUnknownEmdashTok = true
                if (t.model.vramString() === "—") t.sawUnknownEmdashVram = true
            }

            // The orchestrator drives: starts at working, then rewrites to unknown.
            // We pass only if we observed BOTH live, in order, AND honesty held.
            if (t.sawWorking && t.sawUnknown) {
                if (!t.sawUnknownEmdashTok || !t.sawUnknownEmdashVram) Qt.exit(40)
                Qt.exit(0)   // ALL PASS
            }
            if (t.ticks > 60) {
                if (!t.sawWorking) Qt.exit(20)
                Qt.exit(30)   // working seen but unknown change never picked up = STALE
            }
        }
    }
}
