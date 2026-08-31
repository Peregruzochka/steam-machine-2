# -*- coding: utf-8 -*-
"""Модуль сохранения показаний устройств в базу данных SQLite."""

import json
import sqlite3
from datetime import datetime

from loguru import logger


class ReadingStorage:
    """Хранилище показаний на основе SQLite.

    Создаёт и наполняет таблицу readings, где каждая строка —
    результат чтения одного reader.
    """

    def __init__(self, db_path="readings.db"):
        """Открывает соединение с базой и создаёт таблицу при необходимости."""
        logger.info("Открытие базы данных: {}", db_path)
        self.connection = sqlite3.connect(db_path)
        self._init_db()

    def _init_db(self):
        """Создаёт таблицу показаний, если её ещё нет."""
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device_index INTEGER NOT NULL,
                device_info TEXT,
                register_address INTEGER,
                function_code INTEGER,
                info TEXT,
                unit TEXT,
                value REAL,
                values_json TEXT
            )
            """
        )
        self.connection.commit()
        logger.info("Таблица readings готова")

    def save_reading(self, device_index, device_info, reader, values):
        """Сохраняет результат чтения одного reader.

        values — список значений регистров/битов либо None при ошибке
        чтения (ошибки не сохраняются). Для одиночного числового значения
        дополнительно заполняется столбец value с учётом scale.
        """
        if values is None:
            return
        scaled = None
        if len(values) == 1 and isinstance(values[0], (int, float)):
            scaled = values[0] * (reader.get("scale") or 1)
        self.connection.execute(
            """
            INSERT INTO readings (
                timestamp, device_index, device_info, register_address,
                function_code, info, unit, value, values_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                device_index,
                device_info,
                reader.get("address"),
                reader.get("function_code", 3),
                reader.get("info"),
                reader.get("unit"),
                scaled,
                json.dumps(values, ensure_ascii=False),
            ),
        )
        self.connection.commit()
        logger.info(
            "Показание сохранено: устройство {}, регистр {}",
            device_index, reader.get("address"),
        )

    def close(self):
        """Закрывает соединение с базой данных."""
        logger.info("Закрытие базы данных")
        self.connection.close()
