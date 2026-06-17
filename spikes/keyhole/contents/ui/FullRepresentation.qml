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
import QtQuick.Controls as QQC2

Item {
    id: full
    property var model            // a KeyholeModel instance
    property var services         // a ServicesModel instance (boot-health board); may be null
    // The instrument skin (light/dark register). Host injects it; default = dark so the
    // standalone harness renders the canonical register without any host.
    property var skin: _defaultSkin
    InstrumentPalette { id: _defaultSkin }
    property bool reducedMotion: model ? model.reducedMotion : false
    implicitWidth: 360
    implicitHeight: col.implicitHeight + 24

    // Authoritative size for the host popup (PlasmaCore.Dialog reads the mainItem's
    // Layout hints): pin min == preferred == implicit so the popup opens at full height
    // and never settles short and clips the board.
    Layout.minimumWidth: implicitWidth
    Layout.preferredWidth: implicitWidth
    Layout.minimumHeight: implicitHeight
    Layout.preferredHeight: implicitHeight

    // Palette aliases bound to the active skin so every reference reacts to the toggle.
    readonly property color panelBg: skin.base
    readonly property color hairline: skin.hairline
    readonly property color secondaryFg: skin.muted
    readonly property color labelFg: skin.label
    readonly property color warmFg: skin.warm        // reserved "needs you" hue — apt for attention
    readonly property color linkColor: skin.blue
    readonly property color hoverTint: skin.tintHover
    // The glyph-ring aurora: the live HorizonStrip colour, darkened in the light
    // register so the ring stays legible on a near-white surface.
    readonly property color glyphAurora: model
        ? (skin.dark ? model.horizonColor : Qt.darker(model.horizonColor, 1.7))
        : panelBg

    // Service-health tone -> colour. Calm by default; colour spent only off-nominal.
    // Glyph SHAPE (ServicesModel.glyphFor) carries the redundant, never-colour-only cue.
    function toneColor(t) {
        switch (t) {
        case "attention":    return skin.warm        // failed / down-daemon / split-brain
        case "transitional": return skin.stAmber     // starting / stopping (amber)
        case "healthy":      return skin.stUp        // up / ran-ok (muted sage)
        default:             return skin.label       // idle / absent / unknown
        }
    }

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
                skin: full.skin
                glyph: full.model ? full.model.glyphFor(full.model.effectiveState) : "—"
                label: full.model ? full.model.labelFor(full.model.effectiveState) : "…"
                state: full.model ? full.model.effectiveState : "unknown"
                warm:  full.model ? full.model.warm : 0
                aurora: full.glyphAurora
                busy:  full.model ? full.model.busy : 0
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
                // VRAM as a nimbus-aurora horizon: the cool dawn ramp (indigo → blue
                // → violet) fills with usage and brightens with GPU busy. Zero-GPU
                // (a gradient + a width tween; ADR-0012 §7) and never warm/red —
                // pressure is cool, the reserved warm stays the glyph's alone.
                // Hidden under UNKNOWN.
                Rectangle {
                    id: vramTrack
                    Layout.preferredWidth: 96
                    height: 5; radius: 2.5; color: full.hairline
                    visible: full.model && full.model.effectiveState !== "unknown" && full.model.vramFraction() > 0
                    readonly property real lift: full.model ? Math.min(1.0, full.model.busy) : 0.0
                    Rectangle {
                        id: vramFill
                        height: parent.height; radius: parent.radius
                        width: parent.width * (full.model ? full.model.vramFraction() : 0)
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: Qt.lighter(full.skin.auroraLo,  1.0 + 0.30 * vramTrack.lift) }
                            GradientStop { position: 0.6; color: Qt.lighter(full.skin.auroraMid, 1.0 + 0.30 * vramTrack.lift) }
                            GradientStop { position: 1.0; color: Qt.lighter(full.skin.auroraHi,  1.0 + 0.34 * vramTrack.lift) }
                        }
                        Behavior on width {
                            enabled: !full.reducedMotion
                            NumberAnimation { duration: 900; easing.type: Easing.OutCubic }
                        }
                        // a brighter dawn crest at the leading edge; intensifies with busy
                        Rectangle {
                            anchors { right: parent.right; top: parent.top; bottom: parent.bottom }
                            width: Math.min(3, parent.width)
                            radius: parent.radius
                            visible: parent.width > parent.height
                            color: Qt.lighter(full.skin.auroraHi, 1.6)
                            opacity: 0.35 + 0.5 * vramTrack.lift
                        }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: full.hairline }

        // --- SYSTEM: the boot-health board (from the status panel, :9123) ---
        // The whole grouped board, scrollable in place. Honest UNAVAILABLE when the
        // panel can't be reached — never a faked "all healthy".
        RowLayout {
            Layout.fillWidth: true
            Text { text: "SYSTEM"; color: full.labelFg; font.pixelSize: 10; font.letterSpacing: 1.5 }
            Item { Layout.fillWidth: true }
            Text {
                readonly property bool attn: full.services && full.services.available && full.services.summary.attention > 0
                text: full.services ? full.services.summaryString() : "unavailable"
                color: attn ? full.warmFg : full.secondaryFg
                font.pixelSize: 11
                font.bold: attn
            }
        }

        // Honest fallback when the panel is down / not yet reached.
        Text {
            visible: !(full.services && full.services.available)
            Layout.fillWidth: true
            text: "Status panel unreachable"
            color: full.labelFg; font.pixelSize: 11; wrapMode: Text.WordWrap
        }

        // The grouped board — fixed-height rows so the viewport caps cleanly. Tall enough
        // to show the whole board without scrolling in the common case; the cap is a
        // safety bound (large catalogs scroll rather than overflow the screen).
        // Height comes from the model's deterministic boardPx (NOT ListView.contentHeight,
        // which collapses to ~0 in a popup), and is pinned via minimumHeight so the
        // ColumnLayout grows the popup to fit instead of squeezing the list to nothing.
        ListView {
            id: board
            visible: full.services && full.services.available
            readonly property real wantHeight: visible ? Math.min(full.services ? full.services.boardPx : 0, 540) : 0
            Layout.fillWidth: true
            Layout.preferredHeight: wantHeight
            Layout.minimumHeight: wantHeight
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            model: full.services ? full.services.rows : []
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: QQC2.ScrollBar.AsNeeded }

            delegate: Item {
                id: rowItem
                width: board.width
                height: (modelData && modelData.kind === "group") ? 22 : 20
                // A service that exposes a web UI (non-empty url) is launchable from here:
                // click opens the web window. Read-only link-out — no state mutation.
                readonly property string svcUrl: (modelData && modelData.kind === "svc"
                    && modelData.svc && modelData.svc.url) ? modelData.svc.url : ""
                // An all-green / all-red section condenses to one collapsible summary
                // row; clicking it expands the members (progressive disclosure).
                readonly property bool isGroup: modelData && modelData.kind === "group"
                readonly property bool groupCollapsible: rowItem.isGroup && modelData.collapsible === true
                readonly property bool groupCollapsed: rowItem.isGroup && modelData.collapsed === true
                readonly property bool clickable: rowItem.svcUrl !== "" || rowItem.groupCollapsible

                // hover highlight for clickable rows (web-window svc OR a section toggle)
                Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: 2; anchors.rightMargin: 2
                    radius: 4
                    color: full.hoverTint
                    visible: rowItem.clickable && rowMA.containsMouse
                }

                // plain section subheader — mixed / transitional / idle sections,
                // which always show their members (nothing to condense).
                Text {
                    visible: rowItem.isGroup && !rowItem.groupCollapsible
                    anchors.left: parent.left
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 3
                    text: (modelData && modelData.name) ? modelData.name : ""
                    color: full.labelFg; font.pixelSize: 10; font.letterSpacing: 1.0
                }

                // condensable section: when collapsed it IS a single row — the
                // section header, painted the shared tone's colour (with a leading
                // shape glyph so it isn't colour-only). Expanded, it reverts to the
                // quiet header and the member rows below carry the colour. The whole
                // row is the toggle target.
                RowLayout {
                    visible: rowItem.groupCollapsible
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 2; anchors.rightMargin: 6
                    spacing: 8
                    Text {
                        visible: rowItem.groupCollapsed
                        text: (modelData && modelData.glyph) ? modelData.glyph : ""
                        color: full.toneColor(modelData ? modelData.tone : "idle")
                        font.pixelSize: 11
                        Layout.preferredWidth: 12
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: (modelData && modelData.name) ? modelData.name : ""
                        color: rowItem.groupCollapsed
                               ? full.toneColor(modelData ? modelData.tone : "idle")
                               : full.labelFg
                        font.pixelSize: 10; font.letterSpacing: 1.0
                        font.bold: rowItem.groupCollapsed && modelData && modelData.tone === "attention"
                        Layout.fillWidth: true; elide: Text.ElideRight
                    }
                    // quiet disclosure affordance (progressive disclosure)
                    Text {
                        text: rowItem.groupCollapsed ? "▸" : "▾"
                        color: full.labelFg; font.pixelSize: 9; opacity: 0.7
                    }
                }

                // service row: SHAPE glyph (tone-coloured) · name · [↗] · honest state
                RowLayout {
                    visible: modelData && modelData.kind === "svc"
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 6; anchors.rightMargin: 4
                    spacing: 8
                    Text {
                        text: (full.services && modelData && modelData.svc) ? full.services.glyphFor(modelData.svc.status) : "—"
                        color: full.toneColor((full.services && modelData && modelData.svc) ? full.services.toneFor(modelData.svc) : "idle")
                        font.pixelSize: 12
                        Layout.preferredWidth: 14
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: (modelData && modelData.svc) ? modelData.svc.name : ""
                        // link-blue when launchable, so a web-window row reads as clickable
                        color: rowItem.svcUrl !== "" ? full.linkColor : full.secondaryFg
                        font.pixelSize: 12
                        Layout.fillWidth: true; elide: Text.ElideRight
                    }
                    Text {
                        text: "↗"
                        visible: rowItem.svcUrl !== ""
                        color: full.linkColor; font.pixelSize: 11
                    }
                    Text {
                        text: (modelData && modelData.svc) ? modelData.svc.state : ""
                        color: full.labelFg; font.pixelSize: 11
                    }
                }

                // whole-row click target: launch a service's web window, or toggle
                // a condensable section open/closed.
                MouseArea {
                    id: rowMA
                    anchors.fill: parent
                    enabled: rowItem.clickable
                    hoverEnabled: enabled
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: {
                        if (rowItem.svcUrl !== "")
                            Qt.openUrlExternally(rowItem.svcUrl)
                        else if (rowItem.groupCollapsible && full.services)
                            full.services.toggle(modelData.name)
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: full.hairline }

        // --- Footer link-outs: Hermes board (gateway-gated) + full status panel
        RowLayout {
            Layout.fillWidth: true
            Text {
                id: boardLink
                text: (full.model && full.model.gateway === "running")
                      ? "Open board ↗"
                      : "Board unavailable (gateway " + (full.model ? full.model.gateway : "unknown") + ")"
                color: (full.model && full.model.gateway === "running") ? full.linkColor : full.labelFg
                font.pixelSize: 12
                font.underline: full.model && full.model.gateway === "running"
                MouseArea {
                    anchors.fill: parent
                    enabled: full.model && full.model.gateway === "running"
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: Qt.openUrlExternally("http://127.0.0.1:9119")
                }
            }
            Item { Layout.fillWidth: true }
            Text {
                id: statusLink
                text: "Full status ↗"
                color: full.linkColor; font.pixelSize: 12; font.underline: true
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Qt.openUrlExternally("http://127.0.0.1:9123")
                }
            }
        }
    }
}
