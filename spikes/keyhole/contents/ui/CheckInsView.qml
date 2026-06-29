/*
 * CheckInsView.qml — the Check-ins tab page (ADR-0050/0051/0052/0053).
 *
 * Header (title · N LIVE · subline · read-only Auto-pilot) · the repurposed MetricsRail · a
 * Board⇄List toggle (+ filter chips in List) · the body. List = TASKS + RECURRING card sections;
 * Board = SCHEDULED / NEEDS YOU / RUNNING / DONE stacked columns (the design's <560px reflow). Owns
 * the ONE shared 110ms creature Timer, gated on `active && !reducedMotion`. Honest two-state empty
 * ("Can't reach Hermes" vs "No active check-ins"). Deterministic, capped, scrollable body.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

Item {
    id: checkins
    property var model
    property var skin
    property bool reducedMotion: false
    property bool active: true            // host sets: tab is current AND the popup is expanded

    property int tick: 0                  // the ONE shared creature clock
    property int viewMode: 0              // 0 = list, 1 = board
    property string filter: "all"         // all | needs | working
    readonly property int creatureCap: 10 // bound the number of simultaneously-animating creatures

    readonly property int bodyCap: 360
    readonly property var tasks: (model && model.checkIns) ? model.checkIns : []
    readonly property var recurring: (model && model.recurring) ? model.recurring : []
    readonly property bool unknown: !model || model.effectiveState === "unknown"
    readonly property bool empty: tasks.length === 0

    implicitWidth: 360
    implicitHeight: colRoot.implicitHeight

    // The shared tick — frozen when the tab is hidden / popup collapsed / reduced-motion, AND when
    // the feed is UNKNOWN (an unreachable Hermes must not animate stale cards — the display goes
    // honestly quiet, not serene-and-alive). Creatures cost nothing at rest (the AuroraRing gate).
    Timer {
        interval: 110
        running: checkins.active && !checkins.reducedMotion && !checkins.unknown
        repeat: true
        onTriggered: checkins.tick = (checkins.tick + 1) % 100000
    }

    function _mood(t) { return model ? model.checkInMood(t.status, t.consecutive_failures) : "calm" }
    function _byCol(k) {
        var out = []
        for (var i = 0; i < tasks.length; ++i)
            if (model && model.checkInColumn(tasks[i].status, tasks[i].consecutive_failures) === k) out.push(tasks[i])
        return out
    }
    readonly property var filteredTasks: {
        if (filter === "needs") return tasks.filter(function(t) { return _mood(t) === "needsyou" })
        if (filter === "working") return tasks.filter(function(t) { return _mood(t) === "working" || _mood(t) === "stalled" })
        return tasks
    }
    readonly property var schedTasks: _byCol("scheduled")
    readonly property var needsColTasks: _byCol("needs_you")
    readonly property var runTasks: _byCol("running")
    readonly property var doneTasks: _byCol("done")

    function subline() {
        if (unknown) return "Can't reach Hermes"
        var n = tasks.length
        if (n === 0) return "No active tasks on your GPU"
        var needs = model ? model.checkInNeedsYouCount() : 0
        return n + (n === 1 ? " task" : " tasks") + (needs > 0 ? " · " + needs + " need you" : " · all healthy")
    }

    ColumnLayout {
        id: colRoot
        anchors { top: parent.top; left: parent.left; right: parent.right }
        spacing: 11

        // --- HEADER ---------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                RowLayout {
                    spacing: 9
                    Text {
                        text: "Check-ins"
                        color: checkins.skin ? checkins.skin.text : "#E6E9F0"
                        font.pixelSize: 16; font.bold: true
                    }
                    Rectangle {
                        visible: !checkins.unknown && checkins.model && checkins.model.checkInRunningCount() > 0
                        radius: 5
                        color: checkins.skin ? Qt.rgba(checkins.skin.blue.r, checkins.skin.blue.g, checkins.skin.blue.b, 0.14) : "#23204a"
                        Layout.preferredWidth: liveText.implicitWidth + 12
                        Layout.preferredHeight: liveText.implicitHeight + 4
                        Text {
                            id: liveText
                            anchors.centerIn: parent
                            text: (checkins.model ? checkins.model.checkInRunningCount() : 0) + " LIVE"
                            color: checkins.skin ? checkins.skin.blue : "#9B82E0"
                            font.pixelSize: 9; font.bold: true; font.letterSpacing: 1.1; font.family: "monospace"
                        }
                    }
                }
                Text {
                    text: checkins.subline()
                    color: checkins.skin ? checkins.skin.label : "#878C9B"
                    font.pixelSize: 11
                }
            }
            // read-only Auto-pilot (the deferred write seam, ADR-0053)
            ActionButton {
                skin: checkins.skin
                glyph: "◇"
                title: "Auto-pilot off"
                whyDisabled: "Auto-pilot arrives with the Hermes write-API"
                implicitWidth: 30; implicitHeight: 30
            }
        }

        // --- METRICS RAIL (repurposed to real metrics) ----------------------
        MetricsRail {
            Layout.fillWidth: true
            Layout.preferredHeight: 66
            model: checkins.model
            skin: checkins.skin
            reducedMotion: checkins.reducedMotion
        }

        // --- TOOLBAR: Board/List + filter chips -----------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            SegmentedToggle {
                skin: checkins.skin
                reducedMotion: checkins.reducedMotion
                segments: ["List", "Board"]
                currentIndex: checkins.viewMode
                onActivated: function(i) { checkins.viewMode = i }
            }
            Item { Layout.fillWidth: true }
            // filter chips (List mode only) — keyboard + screen-reader operable (WCAG 2.1.1 / 4.1.2)
            Row {
                visible: checkins.viewMode === 0 && !checkins.empty && !checkins.unknown
                spacing: 6
                Repeater {
                    model: [ {k: "all", l: "All"}, {k: "needs", l: "Needs you"}, {k: "working", l: "Working"} ]
                    delegate: Rectangle {
                        id: chip
                        required property var modelData
                        readonly property bool on: checkins.filter === modelData.k
                        radius: 8
                        height: 22
                        width: chipText.implicitWidth + 16
                        color: on ? (checkins.skin ? checkins.skin.blue : "#9B82E0")
                                  : (checkins.skin ? Qt.rgba(checkins.skin.text.r, checkins.skin.text.g, checkins.skin.text.b, 0.05) : "#15171f")
                        border.width: 1
                        // border doubles as the focus ring (blue when focused) — WCAG 2.4.7
                        border.color: chip.activeFocus ? (checkins.skin ? checkins.skin.blue : "#9B82E0")
                                    : on ? "transparent" : (checkins.skin ? checkins.skin.hairline : "#262A36")
                        activeFocusOnTab: true
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: chip.modelData.l + (chip.on ? ", selected" : "")
                        Accessible.checkable: true
                        Accessible.checked: chip.on
                        Keys.onReturnPressed: checkins.filter = chip.modelData.k
                        Keys.onSpacePressed:  checkins.filter = chip.modelData.k
                        Text {
                            id: chipText
                            anchors.centerIn: parent
                            text: chip.modelData.l
                            color: chip.on ? (checkins.skin ? checkins.skin.base : "#12141C")
                                           : (checkins.skin ? checkins.skin.muted : "#B4BAC8")
                            font.pixelSize: 11; font.bold: chip.on
                            Accessible.ignored: true
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                    onClicked: checkins.filter = chip.modelData.k }
                    }
                }
            }
        }

        // --- BODY (deterministic, capped, scrollable) -----------------------
        Flickable {
            id: body
            Layout.fillWidth: true
            // when the empty/unknown message shows, the list/board columns collapse to ~0 height — so
            // give the Flickable a fixed height for the message, else it clips to nothing (the
            // board-renders-blank bug: an empty board has 0 task cards AND no recurring of its own).
            readonly property bool showEmpty: checkins.unknown
                                              || (checkins.empty && (checkins.viewMode === 1 || checkins.recurring.length === 0))
            readonly property real activeH: showEmpty ? 84
                                            : (checkins.viewMode === 0 ? listCol.implicitHeight : boardCol.implicitHeight)
            Layout.preferredHeight: Math.min(activeH, checkins.bodyCap)
            contentWidth: width
            contentHeight: activeH
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            // keyboard scroll (WCAG 2.1.1) + a pointer affordance for content past the cap
            activeFocusOnTab: interactive
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: QQC2.ScrollBar.AsNeeded }
            Keys.onDownPressed: body.contentY = Math.min(Math.max(0, body.contentHeight - body.height), body.contentY + 60)
            Keys.onUpPressed:   body.contentY = Math.max(0, body.contentY - 60)

            // ===== EMPTY / UNKNOWN STATE =====
            // Honest states: unreachable Hermes → "Can't reach Hermes" (cards suppressed, nothing
            // stale animates); reachable-but-empty → "No active check-ins". The BOARD shows only task
            // columns (no recurring), so an empty board ALWAYS needs this line even when recurring
            // exist — and it points the user to where the recurring live (the List view).
            Text {
                visible: body.showEmpty
                width: body.width - 24
                x: 12
                horizontalAlignment: Text.AlignHCenter
                topPadding: 28
                wrapMode: Text.WordWrap
                text: checkins.unknown
                      ? (checkins.model ? checkins.model.checkInsEmptyReason() : "—")
                      : (checkins.viewMode === 1 && checkins.recurring.length > 0)
                        ? ("No active tasks — " + checkins.recurring.length
                           + (checkins.recurring.length === 1 ? " recurring check is in List" : " recurring checks are in List"))
                        : (checkins.model ? checkins.model.checkInsEmptyReason() : "—")
                color: checkins.skin ? checkins.skin.label : "#878C9B"
                font.pixelSize: 12
            }

            // ===== LIST =====
            Column {
                id: listCol
                visible: checkins.viewMode === 0 && !checkins.unknown
                width: body.width
                spacing: 10

                // TASKS
                CheckInsSectionHeader {
                    visible: checkins.filteredTasks.length > 0
                    width: parent.width; skin: checkins.skin
                    label: "TASKS"
                    note: checkins.model && checkins.model.checkInsTruncated()
                          ? ("showing " + checkins.filteredTasks.length + " of " + checkins.model.checkInsTotal) : ""
                }
                Repeater {
                    model: checkins.filteredTasks
                    delegate: TaskCard {
                        required property int index
                        required property var modelData
                        width: listCol.width
                        model: checkins.model; skin: checkins.skin; task: modelData
                        tick: checkins.tick; reducedMotion: checkins.reducedMotion
                        phase: index
                        animate: checkins.active && !checkins.reducedMotion && index < checkins.creatureCap
                    }
                }
                // filtered-to-empty (tasks exist but none match the chip)
                Text {
                    visible: !checkins.empty && checkins.filteredTasks.length === 0
                    width: parent.width; horizontalAlignment: Text.AlignHCenter; topPadding: 14
                    text: "No tasks match this filter"
                    color: checkins.skin ? checkins.skin.label : "#878C9B"; font.pixelSize: 12
                }

                // RECURRING
                CheckInsSectionHeader {
                    visible: checkins.recurring.length > 0
                    width: parent.width; skin: checkins.skin
                    label: "RECURRING"; note: "wake on a schedule"
                }
                Repeater {
                    model: checkins.recurring
                    delegate: RecurringCard {
                        required property int index
                        required property var modelData
                        width: listCol.width
                        model: checkins.model; skin: checkins.skin; job: modelData
                        tick: checkins.tick; reducedMotion: checkins.reducedMotion
                        phase: index + 3
                        animate: checkins.active && !checkins.reducedMotion && index < checkins.creatureCap
                    }
                }
            }

            // ===== BOARD (stacked columns) =====
            Column {
                id: boardCol
                visible: checkins.viewMode === 1 && !checkins.unknown
                width: body.width
                spacing: 12

                Repeater {
                    model: [
                        {k: "needs_you", l: "NEEDS YOU", tone: "needsyou", rows: checkins.needsColTasks},
                        {k: "running",   l: "RUNNING",   tone: "working",  rows: checkins.runTasks},
                        {k: "scheduled", l: "SCHEDULED", tone: "calm",     rows: checkins.schedTasks},
                        {k: "done",      l: "DONE",      tone: "done",     rows: checkins.doneTasks}
                    ]
                    delegate: Column {
                        required property var modelData
                        width: boardCol.width
                        spacing: 8
                        visible: modelData.rows.length > 0
                        RowLayout {
                            width: parent.width
                            spacing: 8
                            Rectangle {
                                width: 7; height: 7; radius: 3.5
                                color: _boardTone(modelData.tone)
                            }
                            Text {
                                text: modelData.l
                                color: checkins.skin ? checkins.skin.label : "#878C9B"
                                font.pixelSize: 10; font.letterSpacing: 1.3; font.family: "monospace"; font.bold: true
                            }
                            Item { Layout.fillWidth: true }
                            Text {
                                text: modelData.rows.length + ""
                                color: checkins.skin ? checkins.skin.muted : "#B4BAC8"
                                font.pixelSize: 11; font.family: "monospace"
                            }
                        }
                        Repeater {
                            model: modelData.rows
                            delegate: TaskCard {
                                required property int index
                                required property var modelData
                                width: parent.width
                                compact: true
                                model: checkins.model; skin: checkins.skin; task: modelData
                                tick: checkins.tick; reducedMotion: checkins.reducedMotion
                                phase: index
                                animate: checkins.active && !checkins.reducedMotion && index < checkins.creatureCap
                            }
                        }
                    }
                }
            }
        }
    }

    function _boardTone(t) {
        if (!skin) return "#9B82E0"
        if (t === "needsyou") return skin.creatureNeedsYou
        if (t === "working")  return skin.creatureWorking
        if (t === "done")     return skin.creatureDone
        return skin.creatureCalm
    }
}
