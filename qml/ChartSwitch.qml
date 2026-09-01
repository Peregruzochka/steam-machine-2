import QtQuick

// Компактный тумблер в стиле проекта (аналог StateButton, но для строк датчиков).
// Белый фон в положении ON, тёмный — в положении OFF.
Rectangle {
    id: root

    // Состояние тумблера: true — включён (фон белый)
    property bool active: false

    // Сигнал переключения пользователем; checked — новое состояние
    signal toggled(bool checked)

    // Компактные размеры, чтобы тумблер помещался в строку датчика
    implicitWidth: 38
    implicitHeight: 20
    radius: 10
    border.width: 1
    border.color: root.active ? "#e8eef8" : "#33405a"
    // Белый фон во включённом положении, тёмный — в выключенном
    color: root.active ? "#e8eef8" : "#1c2330"

    // Подвижный кружок-ползунок
    Rectangle {
        id: knob
        width: 14
        height: 14
        radius: 7
        anchors.verticalCenter: parent.verticalCenter
        x: root.active ? parent.width - width - 3 : 3
        // В положении ON кружок тёмный на белом фоне, в OFF — светлый на тёмном
        color: root.active ? "#1a2130" : "#8fa3c0"

        Behavior on x {
            NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
        }
        Behavior on color {
            ColorAnimation { duration: 150 }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled(!root.active)
    }

    Behavior on color {
        ColorAnimation { duration: 150 }
    }
}
