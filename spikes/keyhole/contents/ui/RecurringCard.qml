/*
 * RecurringCard.qml — one Check-ins RECURRING (cron) card (ADR-0051/0052/0053).
 *
 * [ pixel sidekick | name + cadence chip | "next/last run" | read-only run-now/pause ]. Sourced
 * from ~/.hermes/cron/jobs.json; cadence + next/last are real (the view relative-formats the ISO
 * stamps). Mood derives from enabled/last_status. Read-only in Phase A.
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    property var skin
    property var job                // a recurring entry
    property int tick: 0
    property bool reducedMotion: false
    property bool animate: true
    property int phase: 0

    readonly property string mood: {
        if (!job) return "calm"
        if (!job.enabled || job.state === "paused") return "calm"
        if (job.last_status === "error") return "stalled"
        return "done"               // enabled + last ok → a calm "done/ready" pal
    }

    radius: 13
    color: skin ? Qt.rgba(skin.text.r, skin.text.g, skin.text.b, 0.022) : "#15171f"
    border.width: 1
    border.color: (mood === "stalled") ? (skin ? Qt.rgba(skin.stAmber.r, skin.stAmber.g, skin.stAmber.b, 0.32) : "#5a4a2a")
                                       : (skin ? skin.hairline : "#262A36")
    implicitHeight: row.implicitHeight + 22

    Accessible.role: Accessible.StaticText
    Accessible.name: (job ? job.name : "") + ", schedule " + (job ? job.schedule : "")
        + ", " + (job && job.enabled ? "enabled" : "paused")
        + ", last run " + (job && job.last_status ? job.last_status : "never")

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.margins: 11
        spacing: 12

        Item {
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 46
            Layout.preferredHeight: 40
            SidekickCreature {
                id: creature
                anchors.centerIn: parent
                skin: card.skin
                mood: card.mood
                tick: card.tick
                animate: card.animate
                reducedMotion: card.reducedMotion
                phase: card.phase
                variant: "pixel"
                blurt: card.job ? (card.job.enabled ? "on schedule" : "paused") : ""
            }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: creature.poke() }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 5
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: card.job ? card.job.name : ""
                    color: card.skin ? card.skin.text : "#E6E9F0"
                    font.pixelSize: 13
                    font.bold: true
                    elide: Text.ElideRight
                    Accessible.ignored: true
                }
                Rectangle {
                    radius: 5
                    color: card.skin ? Qt.rgba(card.skin.text.r, card.skin.text.g, card.skin.text.b, 0.06) : "#1c1f2a"
                    Layout.preferredWidth: cadence.implicitWidth + 12
                    Layout.preferredHeight: cadence.implicitHeight + 5
                    Text {
                        id: cadence
                        anchors.centerIn: parent
                        text: card.job ? card.job.schedule : ""
                        color: card.skin ? card.skin.muted : "#B4BAC8"
                        font.pixelSize: 10
                        font.family: "monospace"
                        Accessible.ignored: true
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: {
                    if (!card.job) return ""
                    if (!card.job.enabled || card.job.state === "paused") return "paused"
                    return card.job.last_status === "error" ? "last run · error" : "last run · ok"
                }
                color: card.mood === "stalled" ? (card.skin ? card.skin.stAmber : "#D9B45A")
                                               : (card.skin ? card.skin.muted : "#B4BAC8")
                font.pixelSize: 12
                font.family: "monospace"
                elide: Text.ElideRight
                Accessible.ignored: true
            }
        }

        ColumnLayout {
            Layout.alignment: Qt.AlignVCenter
            spacing: 6
            ActionButton { skin: card.skin; glyph: "↻"; title: "Run now" }
            ActionButton { skin: card.skin; glyph: card.job && !card.job.enabled ? "▶" : "⏸"
                           title: card.job && !card.job.enabled ? "Resume" : "Pause" }
        }
    }
}
