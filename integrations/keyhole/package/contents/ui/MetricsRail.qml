/*
 * MetricsRail.qml — the Check-ins metrics rail, REPURPOSED to real metrics (ADR-0051).
 *
 * The design's "TOTAL TOK/S + sparkline" and "API $-budget" tiles have NO source (tok/s needs the
 * unbuilt ADR-0002 proxy; there is no budget meter) — so we show only what is genuinely measured:
 * GPU LOAD (real NVML util ring) · VRAM (used/total gauge) · ACTIVE (running task count). Honest
 * em-dash under UNKNOWN; zero-shader (a Canvas ring + a gradient bar).
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

Item {
    id: rail
    property var model
    property var skin
    property bool reducedMotion: false
    implicitHeight: 66

    Rectangle {
        anchors.fill: parent
        radius: 14
        color: skin ? Qt.rgba(skin.deep.r, skin.deep.g, skin.deep.b, 0.45) : "#12141c"
        border.width: 1
        border.color: skin ? skin.hairline : "#262A36"

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            spacing: 0

            // --- GPU LOAD (real NVML util) ------------------------------------
            RowLayout {
                Layout.fillWidth: true
                spacing: 11
                RingGauge {
                    id: gpuRing
                    skin: rail.skin
                    value: rail.model ? rail.model.gpuUtilFraction() : 0
                    unknown: !rail.model || rail.model.gpuUtil < 0 || rail.model.effectiveState === "unknown"
                    centerText: rail.model ? (rail.model.gpuUtil < 0 || rail.model.effectiveState === "unknown" ? "—" : rail.model.gpuUtil + "")
                                           : "—"
                    arcColor: skin ? (rail.model && rail.model.gpuUtil > 90 ? skin.stAmber : skin.auroraMid) : "#4A5AD2"
                }
                ColumnLayout {
                    spacing: 4
                    Text {
                        text: "GPU LOAD"
                        color: rail.skin ? rail.skin.label : "#878C9B"
                        font.pixelSize: 9; font.letterSpacing: 1.2; font.family: "monospace"; font.bold: true
                    }
                    Text {
                        text: rail.model ? rail.model.gpuUtilString() : "—"
                        color: rail.skin ? rail.skin.text : "#E6E9F0"
                        font.pixelSize: 13; font.family: "monospace"
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "GPU load " + (rail.model && rail.model.gpuUtil >= 0 && rail.model.effectiveState !== "unknown"
                                          ? (rail.model.gpuUtil + " percent") : "unavailable")
                    }
                }
            }

            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; Layout.topMargin: 14; Layout.bottomMargin: 14
                        color: rail.skin ? rail.skin.hairline : "#262A36" }

            // --- VRAM (used / total + a cool horizon bar) ---------------------
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 16
                spacing: 6
                Text {
                    text: "VRAM"
                    color: rail.skin ? rail.skin.label : "#878C9B"
                    font.pixelSize: 9; font.letterSpacing: 1.2; font.family: "monospace"; font.bold: true
                }
                Text {
                    text: rail.model ? rail.model.vramString() : "—"
                    color: rail.skin ? rail.skin.text : "#E6E9F0"
                    font.pixelSize: 13; font.family: "monospace"
                    Accessible.role: Accessible.StaticText
                    Accessible.name: "VRAM " + (rail.model && rail.model.effectiveState !== "unknown" ? rail.model.vramString() : "unavailable")
                }
                Rectangle {
                    id: vTrack
                    Layout.preferredWidth: 92; height: 5; radius: 2.5
                    color: rail.skin ? rail.skin.hairline : "#262A36"
                    visible: rail.model && rail.model.effectiveState !== "unknown" && rail.model.vramFraction() > 0
                    Rectangle {
                        height: parent.height; radius: parent.radius
                        width: parent.width * (rail.model ? rail.model.vramFraction() : 0)
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            // low stop lifted to auroraMid so even a small fill is visible against the
                            // hairline track (auroraLo over hairline was ~1.19:1 — invisible at low VRAM)
                            GradientStop { position: 0.0; color: rail.skin ? rail.skin.auroraMid : "#4A5AD2" }
                            GradientStop { position: 1.0; color: rail.skin ? rail.skin.auroraHi : "#8A6BDC" }
                        }
                        Behavior on width {
                            enabled: !rail.reducedMotion
                            NumberAnimation { duration: 700; easing.type: Easing.OutCubic }
                        }
                    }
                }
            }

            Rectangle { Layout.preferredWidth: 1; Layout.fillHeight: true; Layout.topMargin: 14; Layout.bottomMargin: 14
                        color: rail.skin ? rail.skin.hairline : "#262A36" }

            // --- ACTIVE (running task count) ----------------------------------
            ColumnLayout {
                Layout.leftMargin: 16
                Layout.rightMargin: 2
                spacing: 4
                Text {
                    text: "ACTIVE"
                    color: rail.skin ? rail.skin.label : "#878C9B"
                    font.pixelSize: 9; font.letterSpacing: 1.2; font.family: "monospace"; font.bold: true
                }
                Text {
                    text: rail.model ? (rail.model.effectiveState === "unknown" ? "—" : rail.model.checkInRunningCount() + "") : "—"
                    color: rail.skin ? rail.skin.text : "#E6E9F0"
                    font.pixelSize: 22; font.family: "monospace"; font.bold: true
                    Accessible.role: Accessible.StaticText
                    Accessible.name: (rail.model && rail.model.effectiveState !== "unknown")
                                     ? (rail.model.checkInRunningCount() + " active tasks") : "active tasks unavailable"
                }
            }
        }
    }
}
