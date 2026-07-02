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
import QtQuick.Window
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
        var base = tasks
        if (filter === "needs") base = tasks.filter(function(t) { return _mood(t) === "needsyou" })
        if (filter === "working") base = tasks.filter(function(t) { return _mood(t) === "working" || _mood(t) === "stalled" })
        // attention-first (stable partition): needsyou cards lead, matching the Board's column
        // order — the warm tray click must land ON the thing that asked for you, never below the
        // scroll fold. Producer order (the honest cap selection) is preserved within each half,
        // and the creature-cap budget automatically favors the needs-you cards.
        var needs = [], rest = []
        for (var i = 0; i < base.length; ++i)
            (_mood(base[i]) === "needsyou" ? needs : rest).push(base[i])
        return needs.concat(rest)
    }
    // a sticky filter must never outlive the data it filtered: tasks draining to zero while a
    // non-"all" chip is active would render a blank, uncontrollable body (the chips hide on empty)
    onEmptyChanged: if (empty) filter = "all"
    // the chips filter BOTH sections (the design's match() does) — a cron can never be needsyou
    // (no review state exists for cron), and "working" admits the same stalled the task filter does
    readonly property var filteredRecurring: {
        if (filter === "needs") return []
        if (filter === "working") return recurring.filter(function(j) {
            return model && model.recurringMood(j) === "stalled" })
        return recurring
    }
    readonly property var schedTasks: _byCol("scheduled")
    readonly property var needsColTasks: _byCol("needs_you")
    readonly property var runTasks: _byCol("running")
    readonly property var doneTasks: _byCol("done")
    // the ADR-0051 §4 -1 sentinel / pre-schema-5 producer — "" when the tasks read is trustworthy
    readonly property string unavailableReason: (model && !unknown) ? model.checkInsUnavailableReason() : ""

    function subline() {
        if (unknown) return "Can't reach Hermes"
        // headline the PRE-CAP total when the producer truncated — "5 tasks" over a 7-task fleet
        // would under-report; the section note's "showing 5 of 7" then explains the difference
        var n = Math.max(tasks.length, (model && model.checkInsTotal > 0) ? model.checkInsTotal : 0)
        // no run-location claim — ADR-0051 ships none (the same honesty that cut the GPU/CLOUD chip)
        if (n === 0) return unavailableReason.length ? "Check-ins unavailable" : "No active tasks"
        var needs = model ? model.checkInNeedsYouCount() : 0
        return n + (n === 1 ? " task" : " tasks")
               + (needs > 0 ? (" · " + needs + (needs === 1 ? " needs you" : " need you")) : " · all healthy")
    }

    // keep keyboard focus VISIBLE inside the scrolling body (WCAG 2.4.7/2.4.11): when focus lands
    // on a control inside the body's content (the now-live card actions), scroll it into view.
    readonly property Item _afi: Window.activeFocusItem
    on_AfiChanged: {
        var it = _afi
        if (!it || !body.interactive) return
        var p = it.parent, inside = false
        while (p) { if (p === body.contentItem) { inside = true; break } p = p.parent }
        if (!inside) return
        var pos = it.mapToItem(body.contentItem, 0, 0)
        if (pos.y < body.contentY) body.contentY = Math.max(0, pos.y - 8)
        else if (pos.y + it.height > body.contentY + body.height)
            body.contentY = Math.min(Math.max(0, body.contentHeight - body.height),
                                     pos.y + it.height - body.height + 8)
    }

    ColumnLayout {
        id: colRoot
        anchors { top: parent.top; left: parent.left; right: parent.right }
        spacing: 11

        // --- HEADER ---------------------------------------------------------
        // No page title: the selected tab segment already says "Check-ins" — repeating it 20px
        // below made the switcher read as a static caption. The live summary IS the headline.
        RowLayout {
            Layout.fillWidth: true
            spacing: 9
            Text {
                Layout.fillWidth: true
                text: checkins.subline()
                color: checkins.skin ? checkins.skin.text : "#E6E9F0"
                font.pixelSize: 13
                elide: Text.ElideRight
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
            // filter chips (List mode only) — keyboard + screen-reader operable (WCAG 2.1.1 / 4.1.2).
            // Stay visible while a non-"all" filter is active even if it matched nothing, so the
            // control that emptied the body is never invisible (the onEmptyChanged reset also holds).
            Row {
                visible: checkins.viewMode === 0 && !checkins.unknown
                         && (!checkins.empty || checkins.filter !== "all")
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
                        // border doubles as the focus ring — WCAG 2.4.7. On a SELECTED (blue-filled)
                        // chip the ring is the ink color, never blue-on-blue (~1:1, invisible).
                        border.color: chip.activeFocus ? (chip.on ? (checkins.skin ? checkins.skin.text : "#E6E9F0")
                                                                  : (checkins.skin ? checkins.skin.blue : "#9B82E0"))
                                    : on ? "transparent" : (checkins.skin ? checkins.skin.hairline : "#262A36")
                        activeFocusOnTab: true
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: chip.modelData.l + (chip.on ? ", selected" : "")
                        Accessible.checkable: true
                        Accessible.checked: chip.on
                        Accessible.onPressAction: checkins.filter = chip.modelData.k
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
                        // -2 margins grow the hit target to 26px (WCAG 2.5.8 headroom on a 22px chip)
                        MouseArea { anchors.fill: parent; anchors.margins: -2
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: checkins.filter = chip.modelData.k }
                    }
                }
            }
        }

        // --- BODY (deterministic, capped, scrollable) -----------------------
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(body.activeH, checkins.bodyCap)

            Flickable {
            id: body
            anchors.fill: parent
            // when the empty/unknown message shows, the list/board columns collapse to ~0 height — so
            // give the Flickable a fixed height for the message, else it clips to nothing (the
            // board-renders-blank bug: an empty board has 0 task cards AND no recurring of its own).
            // Failure states carry the recovery link below the message, so they run taller.
            readonly property bool showEmpty: checkins.unknown
                                              || (checkins.empty && (checkins.viewMode === 1 || checkins.recurring.length === 0))
            readonly property bool showRecovery: showEmpty
                                                 && (checkins.unknown || checkins.unavailableReason.length > 0)
            readonly property real activeH: showEmpty ? (showRecovery ? 122 : 84)
                                            : (checkins.viewMode === 0 ? listCol.implicitHeight : boardCol.implicitHeight)
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
                id: emptyText
                visible: body.showEmpty
                width: body.width - 24
                x: 12
                horizontalAlignment: Text.AlignHCenter
                topPadding: 28
                wrapMode: Text.WordWrap
                text: {
                    if (checkins.unknown) return checkins.model ? checkins.model.checkInsEmptyReason() : "—"
                    // the -1 sentinel / old-producer reason OUTRANKS the friendly pointers — an
                    // unreadable kanban must never read as a calm "No active tasks"
                    if (checkins.unavailableReason.length) return checkins.unavailableReason
                    if (checkins.viewMode === 1 && checkins.recurring.length > 0)
                        return "No active tasks — " + checkins.recurring.length
                               + (checkins.recurring.length === 1 ? " recurring check is in List" : " recurring checks are in List")
                    return checkins.model ? checkins.model.checkInsEmptyReason() : "—"
                }
                color: checkins.skin ? checkins.skin.label : "#878C9B"
                font.pixelSize: 12
            }
            // failure states must not strand the user (the SYSTEM board + dispatch/adopt actions
            // live behind this link) — read-only, always live, the footer's exact idiom
            Text {
                id: recoveryLink
                visible: body.showRecovery
                anchors.top: emptyText.bottom
                anchors.topMargin: 10
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Full status ↗"
                color: checkins.skin ? checkins.skin.blue : "#9B82E0"
                font.pixelSize: 12
                font.underline: true
                activeFocusOnTab: visible
                Accessible.role: Accessible.Button
                Accessible.name: "Full status — opens the AgentOS status panel"
                Accessible.onPressAction: Qt.openUrlExternally("http://127.0.0.1:9123")
                Keys.onReturnPressed: Qt.openUrlExternally("http://127.0.0.1:9123")
                Keys.onSpacePressed:  Qt.openUrlExternally("http://127.0.0.1:9123")
                Rectangle {   // shape focus ring (the shell idiom)
                    anchors.fill: parent; anchors.margins: -3
                    radius: 5; color: "transparent"
                    border.width: 1
                    border.color: checkins.skin ? checkins.skin.text : "#E6E9F0"
                    visible: recoveryLink.activeFocus
                }
                MouseArea { anchors.fill: parent; anchors.margins: -4
                            cursorShape: Qt.PointingHandCursor
                            onClicked: Qt.openUrlExternally("http://127.0.0.1:9123") }
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
                    // truncation note only under "All" — under a chip it would conflate
                    // filter-hidden with cap-hidden ("showing 1 of 7" when 6 are merely filtered)
                    note: checkins.filter === "all" && checkins.model && checkins.model.checkInsTruncated()
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
                // unreadable-kanban / old-producer notice when recurring keep the list alive (the
                // showEmpty message only covers the fully-empty body — this case still has cron cards)
                Text {
                    visible: checkins.empty && checkins.unavailableReason.length > 0 && checkins.recurring.length > 0
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    topPadding: 6; bottomPadding: 4; wrapMode: Text.WordWrap
                    text: checkins.unavailableReason
                    color: checkins.skin ? checkins.skin.label : "#878C9B"; font.pixelSize: 12
                }

                // RECURRING (chip-filtered like TASKS)
                CheckInsSectionHeader {
                    visible: checkins.filteredRecurring.length > 0
                    width: parent.width; skin: checkins.skin
                    label: "RECURRING"; note: "wake on a schedule"
                }
                Repeater {
                    model: checkins.filteredRecurring
                    delegate: RecurringCard {
                        required property int index
                        required property var modelData
                        width: listCol.width
                        model: checkins.model; skin: checkins.skin; job: modelData
                        tick: checkins.tick; reducedMotion: checkins.reducedMotion
                        phase: index + 3
                        // the ~10-creature cap is a WHOLE-TAB budget (ADR-0052 §4), so recurring
                        // spend what the task section left, not a fresh allowance
                        animate: checkins.active && !checkins.reducedMotion
                                 && (index + checkins.filteredTasks.length) < checkins.creatureCap
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
                        {k: "needs_you", l: "NEEDS YOU", tone: "needsyou", rows: checkins.needsColTasks,
                         animOff: 0},
                        {k: "running",   l: "RUNNING",   tone: "working",  rows: checkins.runTasks,
                         animOff: checkins.needsColTasks.length},
                        {k: "scheduled", l: "SCHEDULED", tone: "calm",     rows: checkins.schedTasks,
                         animOff: checkins.needsColTasks.length + checkins.runTasks.length},
                        {k: "done",      l: "DONE",      tone: "done",     rows: checkins.doneTasks,
                         animOff: checkins.needsColTasks.length + checkins.runTasks.length + checkins.schedTasks.length}
                    ]
                    delegate: Column {
                        id: colDelegate
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
                            model: colDelegate.modelData.rows
                            delegate: TaskCard {
                                required property int index
                                required property var modelData
                                width: parent.width
                                compact: true
                                model: checkins.model; skin: checkins.skin; task: modelData
                                tick: checkins.tick; reducedMotion: checkins.reducedMotion
                                phase: index + colDelegate.modelData.animOff
                                // whole-BOARD budget, not per-column (ADR-0052 §4's aggregate cap)
                                animate: checkins.active && !checkins.reducedMotion
                                         && (index + colDelegate.modelData.animOff) < checkins.creatureCap
                            }
                        }
                    }
                }
            }
            }

            // shape focus ring for the scroll viewport — without it the body is an
            // invisible keyboard tab stop (WCAG 2.4.7)
            Rectangle {
                anchors.fill: parent
                radius: 6; color: "transparent"
                border.width: 1
                border.color: checkins.skin ? checkins.skin.text : "#E6E9F0"
                visible: body.activeFocus
                z: 3
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
