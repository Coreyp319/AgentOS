/*
 * TaskCard.qml — one Check-ins task card (ADR-0051/0052/0053).
 *
 * [ sidekick creature | title + agent + status line (+ last check-in) | read-only actions ].
 * The creature + status WORD carry the mood redundantly (never colour-only). `compact` is the
 * board variant (smaller, no action column). No progress bar / tok-s / GPU-CLOUD badge: the kanban
 * carries no progress %, no per-task throughput, and no run-location — we render only what is real
 * (ADR-0051 honesty), never a fabricated gauge.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    property var model              // KeyholeModel (helpers: checkInMood/statusLine/lastSeen/blurt)
    property var skin
    property var task               // a check_ins entry
    property int tick: 0
    property bool reducedMotion: false
    property bool compact: false
    property bool animate: true
    property int phase: 0

    readonly property string mood: (model && task) ? model.checkInMood(task.status, task.consecutive_failures) : "calm"
    readonly property bool isAttention: mood === "needsyou"
    readonly property color moodColor: {
        if (!skin) return "#9B82E0"
        if (mood === "working")  return skin.creatureWorking
        if (mood === "stalled")  return skin.creatureStalled
        if (mood === "needsyou") return skin.creatureNeedsYou
        if (mood === "done")     return skin.creatureDone
        return skin.creatureCalm
    }
    // status TEXT fill — AA-safe needs-you (copper on light); the warm GLOW stays on the border/halo.
    readonly property color statusColor: (mood === "needsyou" && skin) ? skin.creatureNeedsYouText : moodColor

    radius: 13
    color: isAttention ? (skin ? Qt.rgba(skin.warm.r, skin.warm.g, skin.warm.b, 0.06) : "#201410")
                       : (skin ? Qt.rgba(skin.text.r, skin.text.g, skin.text.b, 0.022) : "#15171f")
    border.width: 1
    border.color: isAttention ? (skin ? Qt.rgba(skin.warm.r, skin.warm.g, skin.warm.b, 0.40) : "#5a3a30")
                : (mood === "stalled") ? (skin ? Qt.rgba(skin.stAmber.r, skin.stAmber.g, skin.stAmber.b, 0.32) : "#5a4a2a")
                : (skin ? skin.hairline : "#262A36")
    implicitHeight: row.implicitHeight + (compact ? 18 : 22)

    Accessible.role: Accessible.StaticText
    Accessible.name: (task ? task.title : "")
        + ", " + (task && task.assignee ? task.assignee : "unassigned")
        + ", " + (model && task ? model.checkInStatusLine(task) : "")

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: card.compact ? 9 : 11
        spacing: card.compact ? 9 : 12

        // --- the sidekick (poke target) ----------------------------------------
        Item {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: card.compact ? 40 : 46
            Layout.preferredHeight: card.compact ? 34 : 38
            SidekickCreature {
                id: creature
                anchors.centerIn: parent
                skin: card.skin
                mood: card.mood
                tick: card.tick
                animate: card.animate
                reducedMotion: card.reducedMotion
                phase: card.phase
                variant: "ascii"
                blurt: (card.model && card.task) ? card.model.checkInBlurt(card.task) : ""
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: creature.poke()
            }
        }

        // --- title + agent + status -------------------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            spacing: card.compact ? 4 : 5
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: card.task ? card.task.title : ""
                    color: card.skin ? card.skin.text : "#E6E9F0"
                    font.pixelSize: card.compact ? 13 : 14
                    font.bold: true
                    elide: Text.ElideRight
                    Accessible.ignored: true            // the card's composed name carries this
                }
                Text {
                    visible: card.task && card.task.assignee && card.task.assignee.length > 0
                    text: card.task ? card.task.assignee : ""
                    color: card.skin ? card.skin.muted : "#B4BAC8"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                    Layout.maximumWidth: 90
                    Accessible.ignored: true
                }
            }
            Text {
                Layout.fillWidth: true
                text: (card.model && card.task) ? card.model.checkInStatusLine(card.task) : ""
                color: card.statusColor
                font.pixelSize: 12
                font.family: "monospace"
                elide: Text.ElideRight
                Accessible.ignored: true
            }
            Text {
                visible: !card.compact
                text: (card.model && card.task) ? ("last check-in " + card.model.lastSeenString(card.task)) : ""
                color: card.skin ? card.skin.label : "#878C9B"
                font.pixelSize: 10
                elide: Text.ElideRight
                Accessible.ignored: true
            }
        }

        // --- read-only actions (board/compact hides them to save width) --------
        ColumnLayout {
            visible: !card.compact
            Layout.alignment: Qt.AlignVCenter
            spacing: 6
            ActionButton { skin: card.skin; glyph: "⏸"; title: "Pause" }
            ActionButton { skin: card.skin; glyph: "↗"; title: "Open in board" }
        }
    }
}
