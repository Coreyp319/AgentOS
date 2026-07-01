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
    // ADR-0050 (amended 2026-07-01 — Check-ins promoted to the primary face): which tab is showing
    // (0 = Check-ins, the PRIMARY face the popup opens on; 1 = Instrument, the secondary
    // arbitration/status view). The HorizonStrip, the state glyph honesty, and the compact tray stay
    // shell-global; only the body below the tab bar swaps.
    property int currentTab: 0
    // Honest UNKNOWN: a stale/unreachable board performs NO motion (no tint sunrise,
    // no ember, no breath) — the rows go dim-still instead.
    readonly property bool servicesAvailable: !!(services && services.available)
    implicitWidth: 360
    // Per-tab height so the popup re-measures to the ACTIVE tab (a StackLayout would size to the
    // tallest child and clip the shorter). Snap on switch — no height tween (the contentHeight->0
    // popup-clip + WCAG 2.3.3 lesson the SYSTEM board already follows).
    implicitHeight: (currentTab === 0 ? (checkins.y + checkins.implicitHeight)
                                      : (col.y + col.implicitHeight)) + 16

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
    readonly property color warmFg: skin.warmText    // reserved needs-you FOREGROUND (dual-register, AA-safe)
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
        case "attention":    return skin.warmText     // failed / down-daemon / split-brain
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

    // ADR-0050: the tab switcher — a calm segmented control in the instrument's own language,
    // pinned shell-global directly under the signature strip. Keyboard-operable, reduced-motion aware.
    SegmentedToggle {
        id: tabBar
        anchors { top: strip.bottom; left: parent.left; right: parent.right }
        anchors.topMargin: 7
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        skin: full.skin
        reducedMotion: full.reducedMotion
        segments: ["Check-ins", "Instrument"]
        currentIndex: full.currentTab
        onActivated: function(i) { full.currentTab = i }
    }

    // Tab 2 (secondary) — the existing arbitration Instrument, byte-for-byte (only the top anchor +
    // this `visible` binding changed vs the single-panel original).
    ColumnLayout {
        id: col
        visible: full.currentTab === 1
        anchors { top: tabBar.bottom; left: parent.left; right: parent.right }
        anchors.margins: 12
        anchors.topMargin: 8
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
                warm:  full.model ? full.model.warmFor(full.model.effectiveState, full.model.warm) : 0
                aurora: full.glyphAurora
                busy:  full.model ? full.model.busy : 0
                snag:  full.model ? full.model.snag : 0
                music: full.model ? full.model.music : 0
                energy: full.model ? full.model.auroraEnergyFor(full.model.effectiveState, full.model.busy) : 0.95
                ringIntensity: full.model ? full.model.ringIntensityFor(full.model.effectiveState, full.model.busy) : 0
                breathing: full.model ? full.model.breathingFor(full.model.effectiveState) : false
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
                Accessible.role: Accessible.StaticText
                Accessible.name: "Lease " + (full.model ? full.model.leaseTierString() : "unknown")
            }
            Text { text: "PREEMPT"; color: full.labelFg; font.pixelSize: 11; Layout.alignment: Qt.AlignTop }
            Text {
                Layout.fillWidth: true
                text: full.model ? full.model.preemptString() : "—"
                color: full.secondaryFg; font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
            // QUEUE (schema 4, ADR-0041): who is waiting their turn at the lease RIGHT NOW + the tier
            // served next — the "who's in line" half of the arbitration picture, beside LEASE's "who
            // holds". CALM weather (not bold, like the deferral/held weather), never warm. PRESENT only
            // when something waits; at rest both cells hide and the grid collapses (density grows with
            // load). Both cells share one visibility binding so the 2-column grid stays aligned.
            Text {
                text: "QUEUE"; color: full.labelFg; font.pixelSize: 11
                visible: full.model && full.model.queueString() !== ""
            }
            Text {
                Layout.fillWidth: true
                visible: full.model && full.model.queueString() !== ""
                text: full.model ? full.model.queueString() : ""
                color: full.secondaryFg; font.pixelSize: 13
                elide: Text.ElideRight
                Accessible.role: Accessible.StaticText
                Accessible.name: "Queue " + (full.model ? full.model.queueString() : "")
            }
            // WORKLOAD (schema 3): names the dominant GPU compute process — the attribution the
            // lease/residency rows miss, chiefly ComfyUI (dreaming). The row is PRESENT only when
            // something heavy runs; at rest both cells hide and the grid collapses (density grows
            // with load). Both cells share one visibility binding so the 2-column grid stays aligned.
            Text {
                text: "WORKLOAD"; color: full.labelFg; font.pixelSize: 11
                visible: full.model && full.model.workloadString() !== ""
            }
            Text {
                Layout.fillWidth: true
                visible: full.model && full.model.workloadString() !== ""
                text: full.model ? full.model.workloadString() : ""
                color: full.secondaryFg; font.pixelSize: 13; font.bold: true
                elide: Text.ElideRight
                Accessible.role: Accessible.StaticText
                Accessible.name: "Workload " + (full.model ? full.model.workloadString() : "")
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
                    Accessible.role: Accessible.StaticText
                    Accessible.name: "VRAM " + (full.model ? full.model.vramString() : "unknown")
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
                    // Lighten-with-busy only in the DARK register: lifting a light fill on a light
                    // track LOSES contrast (drops <3:1 at high busy), so light clamps the body lift
                    // to 0 and lets the leading-edge crest carry the "brightens with load" feel.
                    readonly property real bodyLift: full.skin.dark ? lift : 0.0
                    Rectangle {
                        id: vramFill
                        height: parent.height; radius: parent.radius
                        width: parent.width * (full.model ? full.model.vramFraction() : 0)
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: Qt.lighter(full.skin.auroraLo,  1.0 + 0.30 * vramTrack.bodyLift) }
                            GradientStop { position: 0.6; color: Qt.lighter(full.skin.auroraMid, 1.0 + 0.30 * vramTrack.bodyLift) }
                            GradientStop { position: 1.0; color: Qt.lighter(full.skin.auroraHi,  1.0 + 0.34 * vramTrack.bodyLift) }
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
                            // crest stays saturated in light (lightening an already-light violet washes out)
                            color: full.skin.dark ? Qt.lighter(full.skin.auroraHi, 1.6) : full.skin.auroraHi
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
                readonly property bool attn: !!(full.services && full.services.available && full.services.summary.attention > 0)
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
            visible: !!(full.services && full.services.available)
            readonly property real wantHeight: visible ? Math.min(full.services ? full.services.boardPx : 0, 540) : 0
            Layout.fillWidth: true
            Layout.preferredHeight: wantHeight
            Layout.minimumHeight: wantHeight
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            // Stable-identity model: unchanged rows persist across polls (still at rest);
            // expand/collapse insert/remove rows, which these transitions fade.
            model: full.services ? full.services.rowModel : null
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: QQC2.ScrollBar.AsNeeded }

            // Member rows fade in/out on expand/collapse; siblings glide to their new y.
            // Height itself is NOT tweened (boardPx is authoritative — a height tween
            // courts the contentHeight->0 popup-clip bug, and WCAG 2.3.3 flags sliding
            // panels). Reduced-motion disables every transition.
            add: Transition { enabled: !full.reducedMotion
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 180; easing.type: Easing.OutCubic } }
            remove: Transition { enabled: !full.reducedMotion
                NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 180; easing.type: Easing.OutCubic } }
            displaced: Transition { enabled: !full.reducedMotion
                NumberAnimation { properties: "y"; duration: 220; easing.type: Easing.OutCubic } }
            move: Transition { enabled: !full.reducedMotion
                NumberAnimation { properties: "y"; duration: 220; easing.type: Easing.OutCubic } }

            delegate: Item {
                id: rowItem
                width: board.width
                height: (model.kind === "group") ? 22 : 20

                // --- identity (ListModel roles; guarded for heterogeneous rows) ----
                readonly property bool isGroup: model.kind === "group"
                readonly property bool groupCollapsible: isGroup && model.collapsible === true
                readonly property bool groupCollapsed: isGroup && model.collapsed === true
                // A service that exposes a web UI (non-empty url) is launchable: click
                // opens the web window. Read-only link-out — no state mutation.
                readonly property string svcUrl: (model.kind === "svc" && model.svcUrl) ? model.svcUrl : ""
                readonly property bool clickable: svcUrl !== "" || groupCollapsible
                readonly property string tone: isGroup ? (model.tone || "idle") : (model.svcTone || "idle")
                // fires true for ONE poll when this section's tone really changed
                readonly property bool toneChanged: isGroup && model.toneChanged === true && full.servicesAvailable

                // shared activation for mouse + keyboard
                function activate() {
                    if (svcUrl !== "") Qt.openUrlExternally(svcUrl)
                    else if (groupCollapsible && full.services) full.services.toggle(model.name)
                }

                activeFocusOnTab: clickable
                Accessible.role: clickable ? Accessible.Button : Accessible.StaticText
                Accessible.focusable: clickable
                // never colour-only: the screen reader hears the count-bearing predicate,
                // not a silent green "DREAMING".
                Accessible.name: isGroup
                    ? (model.name + (model.collapsible
                        ? (". " + model.predicate + ". " + (groupCollapsed ? "collapsed" : "expanded"))
                        : " section"))
                    : ((model.svcName || "") + ", " + (model.svcState || ""))
                Accessible.onPressAction: rowItem.activate()
                Keys.onReturnPressed: rowItem.activate()
                Keys.onEnterPressed:  rowItem.activate()
                Keys.onSpacePressed:  rowItem.activate()

                // ===== ATTENTION EMBER (the headline beat) =========================
                // A contained warm wash behind a collapsed all-attention row. It
                // GATHERS FROM BELOW (mirroring the wallpaper dawn that rises from the
                // low edge), blooms in once on arrival, then holds a STEADY glow — no
                // sustained breath, so a SYSTEM attention can never be mistaken for the
                // wallpaper's reserved needs_you breath (ADR-0012 §7).
                // Pinned constants (canonical in instrument-tokens, `sectionWash`):
                readonly property real emberRest:  0.09   // steady hold opacity
                readonly property real emberPeak:  0.14   // bloom-in crest (and reduced-motion still)
                readonly property bool isAttention: groupCollapsed && tone === "attention" && full.servicesAvailable

                onIsAttentionChanged: if (isAttention && toneChanged && !full.reducedMotion) bloomIn.restart()
                // one-shot bloom-in (1.0 -> 0.0 over 1400ms) on the transition INTO attention,
                // settling to the steady hold. Triggered from whichever of toneChanged /
                // isAttention settles last.
                property real _bloom: 0.0
                NumberAnimation { id: bloomIn; target: rowItem; property: "_bloom"
                    from: 1.0; to: 0.0; duration: 1400; easing.type: Easing.OutCubic; running: false }
                onToneChangedChanged: if (toneChanged && isAttention && !full.reducedMotion) bloomIn.restart()

                readonly property real _washOpacity: !isAttention ? 0.0
                    : (full.reducedMotion ? emberPeak
                       : (emberRest + (emberPeak - emberRest) * _bloom))

                Rectangle {
                    id: emberWash
                    anchors.fill: parent
                    anchors.leftMargin: 2; anchors.rightMargin: 2
                    radius: 4
                    visible: rowItem._washOpacity > 0.001
                    opacity: rowItem._washOpacity
                    // Derived from skin.warm (the reserved needs-you GLOW hue) — never a
                    // literal. A contained wash rising from the low edge; clear at the top.
                    readonly property color w: full.skin.warm
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: Qt.rgba(emberWash.w.r, emberWash.w.g, emberWash.w.b, 0.0) }
                        GradientStop { position: 1.0; color: Qt.rgba(emberWash.w.r, emberWash.w.g, emberWash.w.b, 1.0) }
                    }
                }

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
                    text: rowItem.isGroup ? (model.name || "") : ""
                    color: full.labelFg; font.pixelSize: 10; font.letterSpacing: 1.0
                }

                // condensable section: when collapsed it IS a single row — the section
                // header, painted the shared tone's colour. The tint DAWNS in (2500ms
                // sunrise) only on a real tone change; otherwise it is set still.
                RowLayout {
                    visible: rowItem.groupCollapsible
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 2; anchors.rightMargin: 6
                    spacing: 8
                    Text {
                        visible: rowItem.groupCollapsed
                        text: rowItem.isGroup ? (model.glyph || "") : ""
                        color: full.toneColor(rowItem.tone)
                        font.pixelSize: 11
                        Layout.preferredWidth: 12
                        horizontalAlignment: Text.AlignHCenter
                        Behavior on color { enabled: rowItem.toneChanged && !full.reducedMotion
                            ColorAnimation { duration: 2500; easing.type: Easing.OutCubic } }
                    }
                    Text {
                        text: rowItem.isGroup ? (model.name || "") : ""
                        color: rowItem.groupCollapsed ? full.toneColor(rowItem.tone) : full.labelFg
                        font.pixelSize: 10; font.letterSpacing: 1.0
                        font.bold: rowItem.groupCollapsed && rowItem.tone === "attention"
                        Layout.fillWidth: true; elide: Text.ElideRight
                        Behavior on color { enabled: rowItem.toneChanged && !full.reducedMotion
                            ColorAnimation { duration: 2500; easing.type: Easing.OutCubic } }
                    }
                    // disclosure affordance — ONE caret glyph, rotated on toggle (caret leads)
                    Text {
                        text: "▸"
                        rotation: rowItem.groupCollapsed ? 0 : 90
                        transformOrigin: Item.Center
                        color: full.labelFg; font.pixelSize: 9; opacity: 0.7
                        Behavior on rotation { enabled: !full.reducedMotion
                            NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                    }
                }

                // service row: SHAPE glyph (tone-coloured) · name · [↗] · honest state
                RowLayout {
                    visible: model.kind === "svc"
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 6; anchors.rightMargin: 4
                    spacing: 8
                    Text {
                        text: (full.services && model.kind === "svc") ? full.services.glyphFor(model.svcStatus) : "—"
                        color: full.toneColor(rowItem.tone)
                        font.pixelSize: 12
                        Layout.preferredWidth: 14
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: (model.kind === "svc") ? (model.svcName || "") : ""
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
                        text: (model.kind === "svc") ? (model.svcState || "") : ""
                        color: full.labelFg; font.pixelSize: 11
                    }
                }

                // keyboard focus ring — a SHAPE outline (not a colour fill) so it stays
                // legible riding on top of the warm ember wash.
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    radius: 4; color: "transparent"
                    border.width: 1; border.color: full.skin.text
                    visible: rowItem.activeFocus
                }

                // whole-row click target: launch a service's web window, or toggle
                // a condensable section open/closed.
                MouseArea {
                    id: rowMA
                    anchors.fill: parent
                    enabled: rowItem.clickable
                    hoverEnabled: enabled
                    cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: rowItem.activate()
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

    // Tab 1 (the primary face) — the Check-ins page (ADR-0050/0051/0052/0053). Sibling of `col`; shown
    // on tab 0. Owns its own shared creature Timer gated on `active`, so the Instrument tab pays nothing.
    CheckInsView {
        id: checkins
        visible: full.currentTab === 0
        anchors { top: tabBar.bottom; left: parent.left; right: parent.right }
        anchors.margins: 12
        anchors.topMargin: 8
        model: full.model
        skin: full.skin
        reducedMotion: full.reducedMotion
        // gate follows the tab this view actually occupies — a mismatch freezes every creature
        // (and kills poke) on the visible tab while burning the tick behind the hidden one
        active: full.visible && checkins.visible
    }
}
