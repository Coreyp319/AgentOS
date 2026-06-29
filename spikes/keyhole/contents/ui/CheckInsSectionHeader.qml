/*
 * CheckInsSectionHeader.qml — a quiet section divider for the Check-ins list (LABEL · note · rule).
 * SPDX-License-Identifier: MIT
 */
import QtQuick
import QtQuick.Layouts

RowLayout {
    id: hdr
    property var skin
    property string label: ""
    property string note: ""
    spacing: 10
    height: 18

    Text {
        text: hdr.label
        color: hdr.skin ? hdr.skin.label : "#878C9B"
        font.pixelSize: 10; font.letterSpacing: 1.4; font.family: "monospace"; font.bold: true
    }
    Text {
        visible: hdr.note.length > 0
        text: hdr.note
        color: hdr.skin ? hdr.skin.dim : "#8A90A0"
        font.pixelSize: 10
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: hdr.skin ? hdr.skin.hairline : "#262A36" }
}
