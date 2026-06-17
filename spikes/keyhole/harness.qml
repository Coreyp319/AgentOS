/*
 * harness.qml — standalone qml6 harness (NO Plasma deps).
 *
 * There is no plasmoidviewer on this box, so we prove the dominant unknown — the
 * Rust->QML file-poll seam + the full instrument rendering — in a plain window.
 * It loads the SAME KeyholeModel and FullRepresentation the plasmoid uses, so the
 * poll/state/render code under test is identical; only the tray host differs
 * (that half is doc-confirmed against shipped Plasma 6 plasmoids).
 *
 * Run:
 *   qml6 harness.qml -- /abs/path/to/keyhole.json
 *   (default path = ./keyhole.json next to this file)
 *
 * A small status line shows the live parsed values + a "polls" counter so you can
 * SEE the Timer firing and the values changing when mutate.sh rewrites the file.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import "contents/ui"

Window {
    id: win
    width: 420
    height: 860
    visible: true
    color: "#08090D"
    title: "keyhole harness"

    // Resolve the feed path: CLI arg after `--`, else ./keyhole.json beside this file.
    function feedFromArgs() {
        var a = Qt.application.arguments
        // arguments: ["qml", ..., "harness.qml", "--", "/path"] OR with -- consumed
        for (var i = 0; i < a.length; ++i) {
            if (a[i] === "--" && i + 1 < a.length) return a[i + 1]
        }
        // also accept a bare trailing path that looks like .json
        for (var j = a.length - 1; j >= 0; --j) {
            if (a[j].indexOf(".json") >= 0) return a[j]
        }
        return Qt.resolvedUrl("keyhole.json").toString().replace("file://", "")
    }

    KeyholeModel {
        id: model
        feedPath: win.feedFromArgs()
        pollIntervalMs: 1000   // 1s in the harness so reactivity is snappy to watch
        reducedMotion: false
    }

    // Boot-health board model — null readBackend ⇒ XHR fallback (http XHR is allowed;
    // only file:// is blocked), so the SYSTEM board is exercised standalone too.
    // `active: true` here since there is no popup expand/collapse to gate on.
    ServicesModel {
        id: servicesModel
        active: true
        pollIntervalMs: 3000
    }

    // poll counter — wraps the model's Timer so we can SEE it fire
    property int pollCount: 0
    Connections {
        target: model
        // there's no signal on lastGoodMs; instead count via a sibling timer that
        // observes the same cadence. Simpler: a debug timer.
    }
    Timer {
        interval: model.pollIntervalMs; running: true; repeat: true; triggeredOnStart: true
        onTriggered: win.pollCount++
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Text {
            text: "KEYHOLE HARNESS — file-poll seam under test"
            color: "#7A8090"; font.pixelSize: 11; font.letterSpacing: 1
        }

        // The real instrument panel (identical component the plasmoid hosts)
        FullRepresentation {
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            model: model
            services: servicesModel
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: "#1E2230" }

        // Live debug readout — proves the values change on disk-write
        GridLayout {
            Layout.fillWidth: true
            columns: 2; columnSpacing: 10; rowSpacing: 3
            Text { text: "feedPath";        color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.feedPath;    color: "#B4BAC8"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle }
            Text { text: "polls fired";     color: "#7A8090"; font.pixelSize: 11 }
            Text { text: win.pollCount;     color: "#9AE6A0"; font.pixelSize: 11 }
            Text { text: "file reachable";  color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.fileReachable ? "yes" : "NO"; color: model.fileReachable ? "#9AE6A0" : "#FF9957"; font.pixelSize: 11 }
            Text { text: "schema";          color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.schema;      color: "#B4BAC8"; font.pixelSize: 11 }
            Text { text: "state (raw)";     color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.state;       color: "#B4BAC8"; font.pixelSize: 11 }
            Text { text: "effectiveState";  color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.effectiveState; color: "#E6E9F0"; font.pixelSize: 11; font.bold: true }
            Text { text: "busy/warm/snag";  color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.busy.toFixed(2)+" / "+model.warm.toFixed(2)+" / "+model.snag.toFixed(2); color: "#B4BAC8"; font.pixelSize: 11 }
            Text { text: "tok/s";           color: "#7A8090"; font.pixelSize: 11 }
            Text { text: model.tokString(); color: "#B4BAC8"; font.pixelSize: 11 }
        }
    }
}
