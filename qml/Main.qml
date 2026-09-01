import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import QtGraphs

ApplicationWindow {
    id: root

    // Высота панели кнопок, лог. px
    readonly property real controlBarHeight: 52

    width: 1280
    height: 800
    minimumWidth: 1100
    minimumHeight: 680
    visible: true

    title: ctrl && ctrl.recording ? "Стенд испытаний МФ САОЗ — ЗАПИСЬ" : "Стенд испытаний МФ САОЗ"
    color: "#12161f"

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ----- Левая панель: датчики -----
        Rectangle {
            Layout.preferredWidth: 440
            Layout.fillHeight: true
            color: "#1a2130"

            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: "#33405a"
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                Label {
                    text: "Датчики установки"
                    color: "#8fa3c0"
                    font.pixelSize: 15
                    font.bold: true
                    Layout.bottomMargin: 4
                }

                Repeater {
                    model: sensorModel

                    delegate: RowLayout {
                        id: sensorRow

                        required property int index
                        required property string name
                        required property real value
                        required property string unit
                        required property int decimals
                        required property bool chartVisible
                        required property color seriesColor

                        Layout.fillWidth: true
                        spacing: 8

                        Label {
                            Layout.preferredWidth: 190
                            text: sensorRow.name
                            color: "#dce6f5"
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }

                        Rectangle {
                            Layout.preferredWidth: 86
                            Layout.preferredHeight: 36
                            radius: 6
                            color: "#232d40"
                            border.color: "#33405a"
                            border.width: 1

                            Label {
                                anchors.centerIn: parent
                                text: sensorRow.value.toFixed(sensorRow.decimals)
                                color: "#7fd1ff"
                                font.pixelSize: 16
                                font.bold: true
                                font.family: "Consolas"
                            }
                        }

                        Label {
                            Layout.preferredWidth: 44
                            text: sensorRow.unit
                            color: "#8fa3c0"
                            font.pixelSize: 13
                        }

                        // Кружок с цветом серии этого датчика на графике
                        Rectangle {
                            Layout.preferredWidth: 14
                            Layout.preferredHeight: 14
                            radius: 7
                            color: sensorRow.seriesColor
                            border.color: "#33405a"
                            border.width: 1
                        }

                        // Компактный тумблер вывода параметра на график
                        ChartSwitch {
                            Layout.preferredWidth: 38
                            Layout.preferredHeight: 20
                            active: sensorRow.chartVisible
                            onToggled: (checked) => ctrl.setSensorVisible(sensorRow.index, checked)
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                // ----- Кнопки управления (под датчиками) -----
                // Обычный Item с anchors вместо вложенного RowLayout (обход бага Qt 6.11)
                Item {
                    objectName: "controlBar"
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.controlBarHeight * 2 + 10

                    StateButton {
                        id: recBtn
                        objectName: "btnRec"
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        height: root.controlBarHeight
                        caption: "ЗАПИСЬ"
                        accent: "#ff3b30"
                        blinking: true
                        active: ctrl ? ctrl.recording : false
                        onActivated: ctrl.toggleRecording()
                    }

                    StateButton {
                        id: heatBtn
                        objectName: "btnHeat"
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: root.controlBarHeight
                        caption: "ПОДОГРЕВ"
                        accent: "#ff9500"
                        active: ctrl ? ctrl.heating : false
                        onActivated: ctrl.toggleHeating()
                    }
                }
            }
        }

        // ----- Правая часть: график -----
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Rectangle {
                id: chartBox
                objectName: "chartBox"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: 12
                color: "#161c28"
                radius: 8
                border.color: "#33405a"
                border.width: 1

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    // Область графика
                    GraphsView {
                        id: chart

                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        axisX: ValueAxis {
                            id: axisX
                            min: 0
                            max: 60
                            labelDecimals: 0
                            tickAnchor: 0
                            tickInterval: 10
                            subTickCount: 1
                        }

                        axisY: ValueAxis {
                            id: axisY
                            min: 0
                            max: 100
                            labelDecimals: 0
                            tickAnchor: 0
                            tickInterval: 20
                            subTickCount: 1
                        }

                        theme: GraphsTheme {
                            colorScheme: GraphsTheme.Dark
                            backgroundVisible: false
                            plotAreaBackgroundVisible: true
                            plotAreaBackgroundColor: "#10141d"
                            labelTextColor: "#8fa3c0"
                            labelBackgroundVisible: false
                            seriesColors: ["#e74c3c", "#3498db", "#2ecc71", "#f1c40f"]
                            gridVisible: true
                        }

                        // Серии создаются динамически — по одной на каждый датчик
                        property var seriesRefs: []

                        // Шаблон серии графика
                        Component {
                            id: seriesComp
                            LineSeries {
                                width: 2
                            }
                        }

                        // Создаёт серии графика по описанию из контроллера
                        function buildSeries() {
                            if (!ctrl)
                                return
                            for (var i = 0; i < seriesRefs.length; i++)
                                removeSeries(seriesRefs[i])
                            seriesRefs = []
                            var infos = ctrl.chartSeriesInfo
                            for (var n = 0; n < infos.length; n++) {
                                var s = seriesComp.createObject(chart)
                                s.name = infos[n].name
                                s.color = infos[n].color
                                s.visible = infos[n].visible
                                addSeries(s)
                                seriesRefs.push(s)
                            }
                        }

                        Component.onCompleted: buildSeries()

                        Connections {
                            target: ctrl

                            // Синхронизирует видимость серий с тумблерами датчиков
                            function onChartSeriesChanged() {
                                var infos = ctrl.chartSeriesInfo
                                for (var i = 0; i < chart.seriesRefs.length; i++) {
                                    var s = chart.seriesRefs[i]
                                    if (s === null || s === undefined)
                                        continue
                                    s.visible = infos[i].visible
                                }
                            }

                            function onChartPoint(seriesIdx, x, y) {
                                var s = chart.seriesRefs[seriesIdx]
                                if (s === null || s === undefined)
                                    return
                                s.append(x, y)
                                if (x > axisX.max - 5) {
                                    axisX.min = x - 55
                                    axisX.max = x + 5
                                }
                                if (s.count > 1600)
                                    s.removeMultiple(0, s.count - 1600)
                            }
                        }
                    }
                }
            }
        }
    }
}
