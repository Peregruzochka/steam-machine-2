import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic

Rectangle {
    id: root

    property string caption: ""
    property string hintOn: "ВКЛ"
    property string hintOff: "ВЫКЛ"
    property color accent: "#ff3b30"
    property bool active: false
    property bool blinking: false

    signal activated()

    radius: 10
    border.width: active ? 2 : 1
    border.color: active ? accent : "#33405a"
    color: active ? Qt.darker(accent, 2.2) : "#1c2330"
    RowLayout {
        anchors.centerIn: parent
        spacing: 10

        Rectangle {
            id: indicator
            width: 14
            height: 14
            radius: 7
            color: root.active ? root.accent : "#5a6474"

            SequentialAnimation on opacity {
                running: root.active && root.blinking
                loops: Animation.Infinite
                NumberAnimation { to: 0.15; duration: 480 }
                NumberAnimation { to: 1.0; duration: 480 }
            }
            Connections {
                target: root
                function onActiveChanged() {
                    if (!root.active)
                        indicator.opacity = 1.0
                }
            }
        }

        ColumnLayout {
            spacing: 1

            Label {
                Layout.alignment: Qt.AlignHCenter
                text: root.caption
                color: "#dce6f5"
                font.pixelSize: 15
                font.bold: true
            }
            Label {
                Layout.alignment: Qt.AlignHCenter
                text: root.active ? root.hintOn : root.hintOff
                color: root.active ? root.accent : "#8fa3c0"
                font.pixelSize: 11
                font.bold: true
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.activated()
    }

    Behavior on color { ColorAnimation { duration: 150 } }
}
