# -*- coding: utf-8 -*-
"""Модуль записи показаний устройств в CSV-файл.

Формат файла — «широкий»: первая колонка «Время» (дд.мм.гггг чч:мм:сс),
далее по колонке на каждый параметр датчика с заголовком вида
«Название параметра, единицы измерения». Одна строка файла соответствует
одному полному циклу опроса всех устройств.

Запись ведётся только во время включённой ЗАПИСИ: при старте создаётся
новый файл recordings/recording_ГГГГММДД_ЧЧММСС.csv с заголовком,
показания накапливаются в буфере цикла и дописываются строкой
по команде flush_row().
"""

import csv
from datetime import datetime
from pathlib import Path

from loguru import logger


class ReadingStorage:
    """Хранилище показаний на основе CSV в «широком» формате.

    Умеет включать/выключать запись (start_recording/stop_recording),
    накапливать показания одного цикла опроса (save_reading)
    и дописывать готовую строку цикла в файл (flush_row).
    """

    def __init__(self, directory="recordings", columns=None):
        """Сохраняет каталог для файлов записи и список колонок.

        columns — список пар (ключ_показания, заголовок_колонки);
        ключи совпадают с теми, по которым клиенты передают показания.
        """
        logger.info("Создано хранилище CSV, каталог: {}, колонок: {}", directory, len(columns or []))
        self.directory = Path(directory)
        self.columns = list(columns or [])
        self._file = None
        self._writer = None
        # Буфер показаний текущего цикла опроса: {ключ: значение}
        self._buffer = {}

    @property
    def is_recording(self):
        """Возвращает True, если запись сейчас включена."""
        return self._file is not None

    def start_recording(self):
        """Включает запись: создаёт новый CSV-файл и пишет строку заголовков.

        Первая колонка — «Время», остальные — параметры датчиков.
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
        self._writer.writerow(["Время"] + [header for _, header in self.columns])
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
        self._buffer.clear()
        logger.info("Запись выключена")

    def save_reading(self, key, value):
        """Запоминает одно показание в буфере текущего цикла (без записи в файл).

        key — ключ колонки, value — число либо None при ошибке чтения
        (None даёт пустую ячейку в строке цикла).
        """
        if not self.is_recording:
            return
        self._buffer[key] = value
        logger.info("Показание помещено в буфер цикла: ключ {} = {}", key, value)

    def flush_row(self):
        """Дописывает строку текущего цикла опроса в CSV (только при включённой записи).

        Первая ячейка — текущее время (дд.мм.гггг чч:мм:сс), далее значения
        по колонкам; отсутствующие показания пишутся пустыми ячейками.
        """
        if not self.is_recording:
            return
        if not self._buffer:
            # Ни один датчик не вернул данные за цикл — строку не пишем
            logger.warning("Цикл опроса без показаний, строка пропущена")
            return
        row = [datetime.now().strftime("%d.%m.%Y %H:%M:%S")]
        row += [self._buffer.get(key, "") for key, _ in self.columns]
        self._writer.writerow(row)
        self._file.flush()
        logger.info("Строка цикла записана в CSV: {}", row)
        self._buffer.clear()

    def close(self):
        """Закрывает файл записи, если он открыт."""
        if self.is_recording:
            self.stop_recording()
        logger.info("Хранилище CSV закрыто")
