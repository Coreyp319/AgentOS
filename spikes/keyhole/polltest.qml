/*
 * polltest.qml — headless proof of the file-poll seam (no window, text evidence).
 *
 * Loads KeyholeModel pointed at a file, logs effectiveState + busy + a poll index
 * on every Timer tick, and quits after N polls. Run alongside mutate.sh to capture
 * a text transcript showing values change live as the file is rewritten.
 *
 * Run: qml6 polltest.qml -- /abs/path/to/keyhole.json
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import "contents/ui"

QtObject {
    id: app

    function feedFromArgs() {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length; ++i)
            if (a[i] === "--" && i + 1 < a.length) return a[i + 1]
        for (var j = a.length - 1; j >= 0; --j)
            if (a[j].indexOf(".json") >= 0) return a[j]
        return "keyhole.json"
    }

    property int ticks: 0
    property int maxTicks: 40

    property var model: KeyholeModel {
        feedPath: app.feedFromArgs()
        pollIntervalMs: 500
    }

    property var logger: Timer {
        interval: 500; running: true; repeat: true; triggeredOnStart: false
        onTriggered: {
            app.ticks++
            console.log("tick=" + app.ticks
                + " reachable=" + (app.model.fileReachable ? "Y" : "N")
                + " schema=" + app.model.schema
                + " rawState=" + app.model.state
                + " effective=" + app.model.effectiveState
                + " gateway=" + app.model.gateway
                + " busy=" + app.model.busy.toFixed(2)
                + " warm=" + app.model.warm.toFixed(2)
                + " snag=" + app.model.snag.toFixed(2)
                + " tok/s=" + app.model.tokString()
                + " vram=" + app.model.vramString()
                + " lease=" + app.model.leaseTierString())
            if (app.ticks >= app.maxTicks)
                Qt.quit()
        }
    }
}
