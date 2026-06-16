/*
 * FullRepresentation.qml — the FULL instrument panel.
 *
 * Layout LEADS WITH ARBITRATION (lease tier/holder + the preempt line — the one
 * datum no competitor can fake), then demotes VRAM / residency / throughput to
 * secondary gauges. Quiet-dark at rest; density grows with load.
 *
 * Dependency-light (QtQuick + Layouts only) so the standalone qml6 harness can
 * load it directly. The plasmoid wraps it; the harness embeds it. The KeyholeModel
 * is passed in as `model` so both hosts share one poll/state source.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

Item {
    id: full
    property var model            // a KeyholeModel instance
    property bool reducedMotion: model ? model.reducedMotion : false
    implicitWidth: 360
    implicitHeight: col.implicitHeight + 24

    readonly property color panelBg: "#12141C"      // quiet-dark; real popup gets KDE blur behind this
    readonly property color hairline: "#262A36"
    readonly property color secondaryFg: "#B4BAC8"
    readonly property color labelFg: "#7A8090"

    // Fallback background ONLY for the harness. In the real plasmoid the native
    // KDE blurred popup provides the translucent background; here we approximate
    // so the standalone window isn't transparent-black.
    Rectangle {
        anchors.fill: parent
        color: full.panelBg
        opacity: 0.92
        radius: 8
    }

    // The horizon strip — pinned to the very top edge.
    HorizonStrip {
        id: strip
        anchors { top: parent.top; left: parent.left; right: parent.right }
        anchors.topMargin: 1
        anchors.leftMargin: 1
        anchors.rightMargin: 1
        tint: full.model ? full.model.horizonColor : "#1A2238"
        reducedMotion: full.reducedMotion
    }

    ColumnLayout {
        id: col
        anchors { top: strip.bottom; left: parent.left; right: parent.right }
        anchors.margins: 12
        anchors.topMargin: 10
        spacing: 10

        // --- Header: state token + fleet -----------------------------------
        RowLayout {
            Layout.fillWidth: true
            StateToken {
                Layout.fillWidth: true
                glyph: full.model ? full.model.glyphFor(full.model.effectiveState) : "—"
                label: full.model ? full.model.labelFor(full.model.effectiveState) : "…"
                state: full.model ? full.model.effectiveState : "unknown"
                warm:  full.model ? full.model.warm : 0
                reducedMotion: full.reducedMotion
            }
            Text {
                text: full.model ? full.model.fleetString() : "—"
                color: full.secondaryFg
                font.pixelSize: 12
                Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: full.hairline }

        // --- ARBITRATION (THE LEAD) -----------------------------------------
        Text {
            text: "ARBITRATION"
            color: full.labelFg
            font.pixelSize: 10
            font.letterSpacing: 1.5
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 10
            rowSpacing: 4
            Text { text: "LEASE";   color: full.labelFg; font.pixelSize: 11 }
            Text {
                Layout.fillWidth: true
                text: full.model ? full.model.leaseTierString() : "—"
                color: full.secondaryFg; font.pixelSize: 13; font.bold: true
                elide: Text.ElideRight
            }
            Text { text: "PREEMPT"; color: full.labelFg; font.pixelSize: 11; Layout.alignment: Qt.AlignTop }
            Text {
                Layout.fillWidth: true
                text: full.model ? full.model.preemptString() : "—"
                color: full.secondaryFg; font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: full.hairline }

        // --- Secondary gauges: THROUGHPUT · RESIDENCY · VRAM ----------------
        GridLayout {
            Layout.fillWidth: true
            columns: 3
            columnSpacing: 12
            rowSpacing: 2

            Text { text: "THROUGHPUT"; color: full.labelFg; font.pixelSize: 10 }
            Text { text: "RESIDENCY";  color: full.labelFg; font.pixelSize: 10 }
            Text { text: "VRAM";       color: full.labelFg; font.pixelSize: 10 }

            // throughput — UNKNOWN (em-dash) until the proxy lands (ADR-0002)
            Text {
                text: full.model ? (full.model.tokString() === "—" ? "—" : full.model.tokString() + " tok/s") : "—"
                color: full.secondaryFg; font.pixelSize: 14
                Layout.alignment: Qt.AlignTop
            }
            Text {
                text: full.model ? full.model.residencyString() : "—"
                color: full.secondaryFg; font.pixelSize: 12
                wrapMode: Text.WordWrap; Layout.alignment: Qt.AlignTop; Layout.fillWidth: true
            }
            ColumnLayout {
                Layout.alignment: Qt.AlignTop
                spacing: 3
                Text {
                    text: full.model ? full.model.vramString() : "—"
                    color: full.secondaryFg; font.pixelSize: 14
                }
                // tiny VRAM bar (zero-GPU rectangle), hidden under UNKNOWN
                Rectangle {
                    Layout.preferredWidth: 90
                    height: 4; radius: 2; color: full.hairline
                    visible: full.model && full.model.effectiveState !== "unknown" && full.model.vramFraction() > 0
                    Rectangle {
                        height: parent.height; radius: 2
                        width: parent.width * (full.model ? full.model.vramFraction() : 0)
                        color: full.secondaryFg
                        Behavior on width {
                            enabled: !full.reducedMotion
                            NumberAnimation { duration: 900; easing.type: Easing.OutCubic }
                        }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: full.hairline }

        // --- Board link-out (gateway-gated) ---------------------------------
        Text {
            id: boardLink
            text: (full.model && full.model.gateway === "running")
                  ? "Open board ↗"
                  : "Board unavailable (gateway " + (full.model ? full.model.gateway : "unknown") + ")"
            color: (full.model && full.model.gateway === "running") ? "#7AA2FF" : full.labelFg
            font.pixelSize: 12
            font.underline: full.model && full.model.gateway === "running"
            MouseArea {
                anchors.fill: parent
                enabled: full.model && full.model.gateway === "running"
                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: Qt.openUrlExternally("http://127.0.0.1:9119")
            }
        }
    }
}
