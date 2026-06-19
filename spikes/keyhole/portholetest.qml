/*
 * portholetest.qml — Phase-1 de-risk for the ShaderEffect aurora porthole.
 *
 * Renders contents/ui/porthole.frag.qsb at all five moods (row 1) and one mood at
 * three iTimes (row 2, a motion check), each over a MAGENTA backing square so the
 * in-shader circular mask's transparent corners are unmistakable. Grabs ONE PNG.
 *
 * MUST run on the live session (offscreen has no GL context -> blank frames):
 *   qml6 portholetest.qml         # writes portholetest.png, then quits
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Window

Window {
    id: win
    visible: true
    width: 580; height: 300
    color: "#2A2D38"          // neutral instrument-ish grey backdrop
    title: "porthole shader de-risk"

    // iTime is fixed per-cell here (deterministic capture); the real ring advances it.
    readonly property var moods: [
        // busy warm snag gray intensity  t    label
        { b: 0.0,  w: 0.0,  s: 0.0, g: 0.0, e: 0.86, t: 6.0, label: "idle" },
        { b: 0.85, w: 0.0,  s: 0.0, g: 0.0, e: 1.36, t: 6.0, label: "working" },
        { b: 0.2,  w: 0.90, s: 0.0, g: 0.0, e: 1.08, t: 6.0, label: "needs_you" },
        { b: 0.0,  w: 0.0,  s: 0.9, g: 0.0, e: 0.90, t: 6.0, label: "snag" },
        { b: 0.0,  w: 0.0,  s: 0.0, g: 1.0, e: 0.68, t: 6.0, label: "unknown" }
    ]
    readonly property var motion: [
        { b: 0.85, w: 0.0, s: 0.0, g: 0.0, e: 1.36, t: 2.0,  label: "work t=2" },
        { b: 0.85, w: 0.0, s: 0.0, g: 0.0, e: 1.36, t: 10.0, label: "work t=10" },
        { b: 0.85, w: 0.0, s: 0.0, g: 0.0, e: 1.36, t: 20.0, label: "work t=20" }
    ]

    component Cell : Item {
        property var m
        width: 110; height: 130
        Rectangle {                       // magenta backing — corners must show through
            anchors.horizontalCenter: parent.horizontalCenter
            y: 6; width: 96; height: 96; color: "#FF00FF"
        }
        ShaderEffect {
            anchors.horizontalCenter: parent.horizontalCenter
            y: 6; width: 96; height: 96
            blending: true
            fragmentShader: "contents/ui/porthole.frag.qsb"
            property real     iTime: m.t
            property vector2d iResolution: Qt.vector2d(width, height)
            property real     uBusy: m.b
            property real     uWarm: m.w
            property real     uSnag: m.s
            property real     uIntensity: m.e
            property real     uGray: m.g
        }
        Text {
            anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
            text: parent.m.label; color: "#E6E9F0"; font.pixelSize: 11
        }
    }

    Item {
        id: root
        anchors.fill: parent
        Column {
            anchors.centerIn: parent
            spacing: 8
            Row { spacing: 6; Repeater { model: win.moods;  Cell { m: modelData } } }
            Row { spacing: 6; Repeater { model: win.motion; Cell { m: modelData } } }
        }
    }

    Component.onCompleted: console.log("PORTHOLE: window up, scheduling grab")
    Timer {
        interval: 900; running: true; repeat: false
        onTriggered: {
            var ok = root.grabToImage(function (res) {
                res.saveToFile("portholetest.png")
                console.log("PORTHOLE: saved portholetest.png")
                Qt.callLater(Qt.quit)
            })
            if (!ok) { console.log("PORTHOLE: grabToImage FAILED to schedule"); Qt.quit() }
        }
    }
}
