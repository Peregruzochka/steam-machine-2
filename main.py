# -*- coding: utf-8 -*-
"""Точка входа панели оператора: запуск QML-интерфейса и контроллера."""

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from backend import Controller


def main() -> None:
    """Создаёт приложение, контроллер с реальным опросом устройств и грузит QML."""
    app = QGuiApplication(sys.argv)
    app.setOrganizationName("MegaUstanovka")
    app.setApplicationName("Панель оператора")

    controller = Controller()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("ctrl", controller)
    engine.rootContext().setContextProperty("sensorModel", controller.sensors)

    # Корректно останавливаем фоновый опрос при закрытии окна
    app.aboutToQuit.connect(controller.shutdown)

    qml_file = Path(__file__).resolve().parent / "qml" / "Main.qml"
    engine.load(str(qml_file))
    if not engine.rootObjects():
        sys.exit(-1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
