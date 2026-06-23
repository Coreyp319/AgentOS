// Aurora control gallery — one window of representative Qt Quick controls, to preview the
// Aurora Union style without hunting through a real app. Plain QtQuick.Controls (no Kirigami
// dependency) so it runs under `qml6`. Launch via preview/gallery.sh (before|after).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    visible: true
    width: 800
    height: 780
    title: "Aurora control gallery"

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 12
            Label {
                text: "Aurora control gallery"
                font.pixelSize: 18
                font.bold: true
                Layout.fillWidth: true
            }
            Label {
                text: "Tab = focus ring · open a menu/combo for the 12px popup"
                opacity: 0.7
            }
            ToolButton {
                text: "Menu ▾"
                onClicked: demoMenu.popup()
                Menu {
                    id: demoMenu
                    MenuItem { text: "Rounded 12px popup" }
                    MenuItem { text: "Soft elevation shadow" }
                    MenuSeparator {}
                    MenuItem { text: "Checkable item"; checkable: true; checked: true }
                    MenuItem { text: "Disabled item"; enabled: false }
                }
            }
        }
    }

    ScrollView {
        id: sv
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: sv.availableWidth
            spacing: 14

            Item { Layout.preferredHeight: 2 }

            GroupBox {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                title: "Buttons — 6px corners, solid lavender focus ring (Tab to see it)"
                RowLayout {
                    anchors.fill: parent
                    spacing: 10
                    Button { text: "Normal" }
                    Button { text: "Accent"; highlighted: true }
                    Button { text: "Flat"; flat: true }
                    Button { text: "Checked"; checkable: true; checked: true }
                    Button { text: "Disabled"; enabled: false }
                    Button {
                        text: "Hover me"
                        ToolTip.visible: hovered
                        ToolTip.text: "12px rounded tooltip, soft shadow"
                    }
                }
            }

            GroupBox {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                title: "Selection controls"
                RowLayout {
                    anchors.fill: parent
                    spacing: 28
                    ColumnLayout {
                        CheckBox { text: "Checked"; checked: true }
                        CheckBox { text: "Unchecked" }
                        CheckBox { text: "Tri-state"; tristate: true; checkState: Qt.PartiallyChecked }
                    }
                    ColumnLayout {
                        RadioButton { text: "Option A"; checked: true }
                        RadioButton { text: "Option B" }
                        RadioButton { text: "Option C" }
                    }
                    ColumnLayout {
                        Switch { text: "On"; checked: true }
                        Switch { text: "Off" }
                        Switch { text: "Disabled"; enabled: false }
                    }
                }
            }

            GroupBox {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                title: "Inputs — 6px fields, solid focus outline"
                RowLayout {
                    anchors.fill: parent
                    spacing: 12
                    TextField { placeholderText: "Text field"; Layout.preferredWidth: 180 }
                    ComboBox { model: ["Combo box", "Rounded popup", "Option three"]; Layout.preferredWidth: 180 }
                    SpinBox { value: 42; from: 0; to: 100 }
                }
            }

            GroupBox {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                title: "Ranges & progress"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 12
                    Slider { value: 0.6; Layout.fillWidth: true }
                    ProgressBar { value: 0.45; Layout.fillWidth: true }
                    RowLayout {
                        Layout.fillWidth: true
                        ProgressBar { indeterminate: true; Layout.fillWidth: true }
                        BusyIndicator { running: true }
                    }
                }
            }

            GroupBox {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                title: "Tabs & a framed pane"
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 10
                    TabBar {
                        Layout.fillWidth: true
                        TabButton { text: "First" }
                        TabButton { text: "Second" }
                        TabButton { text: "Third" }
                    }
                    Frame {
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            Label { text: "A framed pane — select the text below to see the accent:"; font.bold: true }
                            TextField { text: "select me — the highlight is the co-lit accent"; Layout.preferredWidth: 320 }
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: 12 }
        }
    }
}
