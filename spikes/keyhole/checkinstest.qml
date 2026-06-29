/*
 * checkinstest.qml — offscreen render proof for the Check-ins tab (ADR-0050/0051/0052).
 *
 * Check-ins has NO ShaderEffect (the rail ring + pixel sprite are Canvas, the rest QtQuick), so
 * unlike the porthole this renders correctly OFFSCREEN. Injects a stub schema-5 state directly into
 * a KeyholeModel (no feed file), renders the integrated FullRepresentation on tab 1 + a board view,
 * grabs PNGs, and quits. Imports the CANONICAL ui dir (the spikes copy is kept in sync separately).
 *
 *   QT_QPA_PLATFORM=offscreen qml6 checkinstest.qml
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Window
import "../../integrations/keyhole/package/contents/ui"

Window {
    id: win
    width: 760
    height: 720
    visible: true
    color: "#08090D"

    function nowS() { return Math.floor(Date.now() / 1000) }

    KeyholeModel {
        id: model
        feedPath: ""    // no polling — we inject state directly
        reducedMotion: false
        Component.onCompleted: {
            everLoaded = true
            fileReachable = true
            lastGoodMs = Date.now()
            gateway = "running"
            state = "working"
            gpuUtil = 47
            vram = { used_mib: 14200, total_mib: 24564 }
            checkInsTotal = 5
            var n = win.nowS()
            checkIns = [
                { id:"a", title:"Index & verify sources", assignee:"atlas", status:"running",
                  priority:5, consecutive_failures:0, created_at:n-900, started_at:n-800,
                  last_heartbeat_at:n-12, step:"crunching PDF 27", last_error:"" },
                { id:"b", title:"Backfill embeddings", assignee:"atlas", status:"blocked",
                  priority:5, consecutive_failures:2, created_at:n-1200, started_at:n-1000,
                  last_heartbeat_at:n-90, step:"shard 14", last_error:"OOM on shard 14" },
                { id:"c", title:"Review draft synthesis", assignee:"scribe", status:"review",
                  priority:3, consecutive_failures:0, created_at:n-600, started_at:-1,
                  last_heartbeat_at:-1, step:"", last_error:"" },
                { id:"d", title:"Train reranker (LoRA)", assignee:"local", status:"todo",
                  priority:1, consecutive_failures:0, created_at:n-300, started_at:-1,
                  last_heartbeat_at:-1, step:"", last_error:"" },
                { id:"e", title:"Nightly vector backup", assignee:"local", status:"done",
                  priority:0, consecutive_failures:0, created_at:n-7200, started_at:n-7100,
                  last_heartbeat_at:n-3600, step:"", last_error:"" }
            ]
            recurring = [
                { id:"j1", name:"Daily UI audit", schedule:"0 4 * * *", enabled:true,
                  state:"scheduled", next_run:"2026-06-30T04:00:00-07:00", last_run:"2026-06-29T05:21:52-07:00", last_status:"ok" },
                { id:"j2", name:"Model reaper", schedule:"15,30,45 9 * * *", enabled:false,
                  state:"paused", next_run:"", last_run:"", last_status:"" }
            ]
        }
    }

    Row {
        x: 20; y: 20
        spacing: 20
        // integrated: the real FullRepresentation on the Check-ins tab (list)
        FullRepresentation {
            id: fullList
            model: model
            Component.onCompleted: currentTab = 1
        }
        // a standalone Check-ins on board view
        CheckInsView {
            id: boardView
            model: model
            width: 360
            viewMode: 1
        }
    }

    // grab sequentially then quit
    property int _done: 0
    function _maybeQuit() { _done++; if (_done >= 2) Qt.callLater(Qt.quit) }
    Timer {
        interval: 400; running: true; repeat: false
        onTriggered: {
            fullList.grabToImage(function(r) { r.saveToFile("/tmp/claude-1000/-home-corey-Documents-AgentOS/b34598c9-84a8-4c37-972d-19933563f7b2/scratchpad/checkins_full_list.png"); win._maybeQuit() })
            boardView.grabToImage(function(r) { r.saveToFile("/tmp/claude-1000/-home-corey-Documents-AgentOS/b34598c9-84a8-4c37-972d-19933563f7b2/scratchpad/checkins_board.png"); win._maybeQuit() })
        }
    }
}
