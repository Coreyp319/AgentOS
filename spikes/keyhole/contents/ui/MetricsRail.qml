/*
 * MetricsRail.qml — the Check-ins metrics rail (ADR-0051), now fronted by the LIVE aurora porthole.
 *
 * The flat GPU ring (a Canvas % arc) is replaced by the keyhole's signature AuroraRing — the same
 * <100px porthole the Instrument tab wears (ADR-0012 §7 amendment), running porthole.frag.qsb and
 * mood-driven by the SHARED model, so the Check-ins tab breathes with the wallpaper too. Beside the
 * honest GPU LOAD % it folds in a use-case from the first (arbitration) tab: WHAT is actually using
 * the GPU right now — the dominant WORKLOAD (e.g. "ComfyUI · 21.0 GB"), or absent that, who HOLDS the
 * lease — present ONLY when something runs (calm at rest, like the Instrument's WORKLOAD/QUEUE rows).
 * VRAM (used/total gauge) · ACTIVE (running task count) are unchanged; honest em-dash under UNKNOWN.
 *
 * The porthole is the VISIBLE tab's one shader (it pauses off-screen / freezes under reduced-motion),
 * so the cost is the same single porthole the Instrument already pays — never two at once.
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

    // The fold-in from the arbitration (Instrument) tab: the GPU's current USE CASE, present-only + calm.
    // WORKLOAD (the heavy process) leads; else a held lease; "" at rest so the line stays quiet (the same
    // "density grows with load" idiom the Instrument's WORKLOAD/QUEUE rows follow).
    readonly property string _useCase: {
        if (!model || model.effectiveState === "unknown") return ""
        var w = model.workloadString()                 // "ComfyUI · 21.0 GB" or ""
        if (w !== "") return w
        var l = model.leaseTierString()                // "batch (holder)" or the em-dash
        return (l && l !== model.emdash()) ? "lease · " + l : ""
    }

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

            // --- GPU: the LIVE aurora porthole + GPU LOAD % + what's using it -----
            RowLayout {
                Layout.fillWidth: true
                spacing: 11
                AuroraRing {
                    id: gpuPorthole
                    diameter: 36
                    glyph:      rail.model ? rail.model.glyphFor(rail.model.effectiveState) : "○"
                    glyphColor: "#ECEFF6"          // always light ink over the dawn (a11y: state = shape + mood)
                    themeStops: rail.skin ? rail.skin.portholeStops : undefined
                    busy:   rail.model ? rail.model.busy : 0
                    warm:   rail.model ? rail.model.warmFor(rail.model.effectiveState, rail.model.warm) : 0
                    snag:   rail.model ? rail.model.snag : 0
                    music:  rail.model ? rail.model.music : 0
                    unknownState: !rail.model || rail.model.effectiveState === "unknown"
                    energy: rail.model ? rail.model.auroraEnergyFor(rail.model.effectiveState, rail.model.busy) : 0.95
                    bloom:  rail.model ? rail.model.ringIntensityFor(rail.model.effectiveState, rail.model.busy) : 0
                    breathing: rail.model ? rail.model.breathingFor(rail.model.effectiveState) : false
                    reducedMotion: rail.reducedMotion
                    Accessible.ignored: true       // the GPU LOAD text cell below carries the spoken value
                    Layout.alignment: Qt.AlignVCenter
                }
                ColumnLayout {
                    spacing: 3
                    Layout.alignment: Qt.AlignVCenter
                    Text {
                        text: "GPU LOAD"
                        color: rail.skin ? rail.skin.label : "#878C9B"
                        font.pixelSize: 9; font.letterSpacing: 1.2; font.family: "monospace"; font.bold: true
                    }
                    Text {
                        text: rail.model ? rail.model.gpuUtilString() : "—"
                        // the design's one alarm affordance kept: >90% goes amber (text still carries
                        // the number — the tint accents, it never IS the signal)
                        color: (rail.model && rail.model.gpuUtil > 90 && rail.model.effectiveState !== "unknown")
                               ? (rail.skin ? rail.skin.stAmber : "#D9B45A")
                               : (rail.skin ? rail.skin.text : "#E6E9F0")
                        font.pixelSize: 13; font.family: "monospace"
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "GPU load " + (rail.model && rail.model.gpuUtil >= 0 && rail.model.effectiveState !== "unknown"
                                          ? (rail.model.gpuUtil + " percent") : "unavailable")
                    }
                    // the arbitration fold-in: what is using the GPU right now (present-only, calm).
                    // fillWidth+max so the line YIELDS at 360px instead of squeezing ACTIVE to the border
                    Text {
                        text: rail._useCase
                        visible: rail._useCase !== ""
                        color: rail.skin ? rail.skin.label : "#878C9B"
                        font.pixelSize: 9; font.family: "monospace"
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                        Layout.maximumWidth: 124
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "GPU in use: " + rail._useCase
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
