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
    property var model              // KeyholeModel (cronHuman / recurringMood / next+last stamps)
    property var skin
    property var job                // a recurring entry
    property int tick: 0
    property bool reducedMotion: false
    property bool animate: true
    property int phase: 0

    // ONE mood source (KeyholeModel.recurringMood) — the filter chips + this card must agree.
    readonly property string mood: (model && job) ? model.recurringMood(job) : "calm"

    radius: 13
    color: skin ? Qt.rgba(skin.text.r, skin.text.g, skin.text.b, 0.022) : "#15171f"
    border.width: 1
    border.color: (mood === "stalled") ? (skin ? Qt.rgba(skin.stAmber.r, skin.stAmber.g, skin.stAmber.b, 0.32) : "#5a4a2a")
                                       : (skin ? skin.hairline : "#262A36")
    implicitHeight: row.implicitHeight + 22

    Accessible.role: Accessible.StaticText
    // coarse (minute-granular) countdown in the NAME so it doesn't mutate + fire AT events each poll
    Accessible.name: (job ? job.name : "") + ", " + (card.model && job ? card.model.cronHuman(job.schedule) : (job ? job.schedule : ""))
        + ", " + (job && job.enabled ? "enabled" : "paused")
        + (card.model && job && job.enabled && card.model.recurringNextString(job, true).length
           ? (", " + card.model.recurringNextString(job, true)) : "")
        + ", " + (card.model && job ? card.model.recurringLastString(job) : "")

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
            spacing: 4
            Text {
                Layout.fillWidth: true
                text: card.job ? card.job.name : ""
                color: card.skin ? card.skin.text : "#E6E9F0"
                font.pixelSize: 13
                font.bold: true
                elide: Text.ElideRight
                Accessible.ignored: true
            }
            // the HUMAN cadence (↻ "Daily 4:00 AM"), not the raw cron — cron means nothing to read
            Text {
                Layout.fillWidth: true
                text: "↻ " + (card.model && card.job ? card.model.cronHuman(card.job.schedule)
                                                      : (card.job ? card.job.schedule : ""))
                color: card.skin ? card.skin.muted : "#B4BAC8"
                font.pixelSize: 11
                elide: Text.ElideRight
                Accessible.ignored: true
            }
            // status: "next in 2h 05m" countdown from the REAL next_run stamp / "paused"; hidden when
            // the stamp is absent (never an invented countdown)
            Text {
                Layout.fillWidth: true
                visible: text.length > 0
                text: (card.model && card.job) ? card.model.recurringNextString(card.job) : ""
                // creatureStalled, not raw stAmber: the register-split token minted for exactly
                // this small-text-on-card-glass surface (light stAmber sits at a 4.67:1 razor)
                color: card.mood === "stalled" ? (card.skin ? card.skin.creatureStalled : "#D9B45A")
                                               : (card.skin ? card.skin.muted : "#B4BAC8")
                font.pixelSize: 12
                font.family: "monospace"
                elide: Text.ElideRight
                Accessible.ignored: true
            }
            // "last run 48m ago · ok" / "· error" / the honest "not yet run" (last_status "")
            Text {
                Layout.fillWidth: true
                visible: text.length > 0
                text: (card.model && card.job) ? card.model.recurringLastString(card.job) : ""
                color: card.mood === "stalled" ? (card.skin ? card.skin.creatureStalled : "#D9B45A")
                                               : (card.skin ? card.skin.label : "#878C9B")
                font.pixelSize: 10
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
            // Open is a READ-ONLY link-out, not a deferred write — live when the gateway is (the
            // same gate the Instrument footer's "Open board ↗" uses; ADR-0053 defers writes only)
            ActionButton {
                skin: card.skin; glyph: "↗"; title: "Open in board"
                enabledAction: card.model && card.model.gateway === "running"
                whyDisabled: "Board unavailable (gateway " + (card.model ? card.model.gateway : "unknown") + ")"
                onTriggered: Qt.openUrlExternally("http://127.0.0.1:9119")
            }
        }
    }
}
