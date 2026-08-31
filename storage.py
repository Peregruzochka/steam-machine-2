# -*- coding: utf-8 -*-
"""Модуль записи показаний устройств в CSV-файл.

Запись ведётся только во время включённой ЗАПИСИ: при старте создаётся
новый файл recordings/recording_ГГГГММДД_ЧЧММСС.csv с заголовком,
каждое показание дописывается строкой.
"""

import csv
from datetime import datetime
from pathlib import Path

from loguru import logger


class ReadingStorage:
    """Хранилище показаний на основе CSV.

    Умеет включать/выключать запись (start_recording/stop_recording)
    и дописывать показания в открытый файл (save_reading).
    """

    def __init__(self, directory="recordings"):
        """Сохраняет каталог для файлов записи и помечает запись выключенной."""
        logger.info("Создано хранилище CSV, каталог: {}", directory)
        self.directory = Path(directory)
        self._file = None
        self._writer = None

    @property
    def is_recording(self):
        """Возвращает True, если запись сейчас включена."""
        return self._file is not None

    def start_recording(self):
        """Включает запись: создаёт новый CSV-файл и пишет заголовок.

        Возвращает путь к созданному файлу.
        """
        if self.is_recording:
            logger.warning("Запись уже включена")
            return self._file.name
        self.directory.mkdir(parents=True, exist_ok=True)
        file_name = f"recording_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = self.directory / file_name
        logger.info("Включение записи, файл: {}", path)
        self._file = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._file, delimiter=";")
        self._writer.writerow(
            ["время", "устройство", "параметр", "единицы", "значение"]
        )
        self._file.flush()
        logger.info("Запись включена: {}", path)
        return path

    def stop_recording(self):
        """Выключает запись и закрывает текущий CSV-файл."""
        if not self.is_recording:
            logger.warning("Запись уже выключена")
            return
        logger.info("Выключение записи, файл: {}", self._file.name)
        self._file.close()
        self._file = None
        self._writer = None
        logger.info("Запись выключена")

    def save_reading(self, device_index, device_info, reader, value):
        """Дописывает одно показание в CSV (только при включённой записи).

        value — число либо None при ошибке чтения (ошибки не записываются).
        """
        if not self.is_recording:
            return
        if value is None:
            return
        self._writer.writerow(
            [
                datetime.now().isoformat(timespec="seconds"),
                device_info,
                reader.get("info"),
                reader.get("unit"),
                value,
            ]
        )
        self._file.flush()
        logger.info(
            "Показание записано в CSV: устройство {}, параметр {} = {}",
            device_index, reader.get("info"), value,
        )

    def close(self):
        """Закрывает файл записи, если он открыт."""
        if self.is_recording:
            self.stop_recording()
        logger.info("Хранилище CSV закрыто")
