/*
 * SegmentedToggle.qml — a calm 2–N segment switch in the instrument's own language.
 *
 * Used for BOTH the tab switcher (Instrument | Check-ins) and the Board | List toggle
 * (ADR-0050). Text segments with a per-segment underline (no Kirigami chrome); keyboard
 * operable; reduced-motion stills the underline fade. Dependency-light (QtQuick + Layouts).
 *
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

Item {
    id: seg
    property var skin
    property bool reducedMotion: false
    property var segments: []        // array of label strings
    property int currentIndex: 0
    signal activated(int index)

    implicitHeight: 24
    implicitWidth: row.implicitWidth

    Row {
        id: row
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        spacing: 18

        Repeater {
            id: rep
            model: seg.segments
            delegate: Item {
                id: segItem
                required property int index
                required property string modelData
                readonly property bool selected: index === seg.currentIndex
                width: Math.max(lbl.implicitWidth, 12)
                height: seg.height
                // keyboard: in the Tab chain + Left/Right arrow-navigable (the PageTab semantics), so
                // the Return/Space activators can fire and focus walks the segments like a real tab bar
                activeFocusOnTab: true
                Keys.onLeftPressed:  if (segItem.index > 0) rep.itemAt(segItem.index - 1).forceActiveFocus()
                Keys.onRightPressed: if (segItem.index < seg.segments.length - 1) rep.itemAt(segItem.index + 1).forceActiveFocus()

                // visible focus ring (the underline encodes SELECTED; this encodes FOCUSED) — WCAG 2.4.7
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: -3
                    radius: 4; color: "transparent"
                    border.width: 1
                    border.color: seg.skin ? seg.skin.blue : "#9B82E0"
                    visible: segItem.activeFocus
                }

                Column {
                    anchors.centerIn: parent
                    spacing: 3
                    Text {
                        id: lbl
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: segItem.modelData
                        color: segItem.selected ? (seg.skin ? seg.skin.text : "#E6E9F0")
                                                : (seg.skin ? seg.skin.label : "#878C9B")
                        font.pixelSize: 12
                        font.bold: segItem.selected
                    }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: lbl.implicitWidth
                        height: 2; radius: 1
                        color: seg.skin ? seg.skin.blue : "#9B82E0"
                        opacity: segItem.selected ? 1 : 0
                        Behavior on opacity {
                            enabled: !seg.reducedMotion
                            NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
                        }
                    }
                }

                Accessible.role: Accessible.PageTab
                Accessible.name: segItem.modelData + (segItem.selected ? ", selected" : "")
                Accessible.focusable: true
                Keys.onReturnPressed: seg.activated(segItem.index)
                Keys.onSpacePressed:  seg.activated(segItem.index)

                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -6
                    cursorShape: Qt.PointingHandCursor
                    onClicked: seg.activated(segItem.index)
                }
            }
        }
    }
}
