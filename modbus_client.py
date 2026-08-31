# -*- coding: utf-8 -*-
"""Модуль работы с датчиками по MODBUS RTU.

Загружает настройки устройств из sensor.json, подключается к ним
и считывает данные из регистров, описанных в секции readers.
"""

import json
import struct

from loguru import logger
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException
from serial import SerialException

from storage import ReadingStorage


class ModbusManager:
    """Менеджер MODBUS RTU устройств.

    Отвечает за загрузку конфигурации из sensor.json,
    подключение к устройствам и чтение регистров из секции readers.
    """

    def __init__(self, config_path="sensor.json", storage=None):
        """Сохраняет путь к конфигурации, хранилище и создаёт пустые коллекции клиентов."""
        self.config_path = config_path
        self.storage = storage
        self.config = []
        self.clients = {}
        self.addresses = {}
        logger.info("ModbusManager создан, файл конфигурации: {}", config_path)

    def load_config(self):
        """Читает sensor.json и отбирает только Modbus-устройства."""
        logger.info("Загрузка конфигурации из файла {}", self.config_path)
        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                devices = json.load(file)
        except FileNotFoundError:
            logger.error("Файл конфигурации не найден: {}", self.config_path)
            raise
        except json.JSONDecodeError as exc:
            logger.error("Ошибка разбора JSON: {}", exc)
            raise

        # Устройства без поля protocol считаются Modbus
        self.config = [d for d in devices if d["protocol"] == "modbus"]
        logger.info("Загружено устройств: {}", len(self.config))
        return self.config

    def connect_all(self):
        """Подключается ко всем устройствам из конфигурации и сохраняет клиентов."""
        logger.info("Подключение к устройствам ({} шт.)", len(self.config))
        for index, device in enumerate(self.config):
            if device["port"] is None or device["address"] is None:
                # Порт или адрес не указан — устройство ещё не настроено
                logger.warning("У устройства {} не указан порт или адрес, пропуск", index)
                continue
            try:
                client = ModbusSerialClient(
                    port=device["port"],
                    baudrate=device.get("baudrate", 9600),
                    bytesize=device.get("bytesize", 8),
                    stopbits=device.get("stopbit", 1),
                    parity=device.get("parity", "N"),
                    timeout=3,
                )
                connected = client.connect()
            except SerialException as exc:
                logger.error(
                    "Ошибка открытия порта {} у устройства {}: {}",
                    device["port"], index, exc,
                )
                continue
            if connected:
                self.clients[index] = client
                logger.info(
                    "Устройство {} подключено (порт {}, адрес {})",
                    index, device["port"], device["address"],
                )
            else:
                logger.error(
                    "Не удалось подключиться к устройству {} (порт {})",
                    index, device["port"],
                )
        return self.clients

    def _read_registers(self, client, info, count, read_register_address, device_address):
        ### тут начинается развязка того, что сделать с регистром
        ### важно чтобы значения сочетались с теми, которые есть в sensor.json
        try:
            if info in ("Температура внешнего датчика", "Давление"):
                result = client.read_input_registers(0, count=16, device_id=device_address)
                register_address = result.registers[read_register_address]
                logger.info("Получена последовательность регистров устройства {}: {}", device_address, register_address)
                return register_address
        except ModbusIOException as exc:
            logger.error("Нет ответа от устройства {}: {}", device_address, exc)
            return None

    def read_reader(self, device_index, reader):
        client = self.clients.get(device_index)
        if client is None:
            return None
        read_register_address = reader["address"]
        count = reader.get("count", 1)
        info = reader.get("info")
        device_address = self.config[device_index]["address"]
        scale = reader.get("scale", 1)

        registers = self._read_registers(client, info, count, read_register_address, device_address)
        if registers is None:
            return None

        value = self.parse_modbus_value(registers, info, scale)
        logger.info("Прочитаны значения: {}", value)
        return value

    def parse_modbus_value(self, registers, info, scale):
        value = None

        ### тут начинается развязка того, что сделать с регистром
        ### важно чтобы значения сочетались с теми, которые есть в sensor.json

        if info in ("Температура внешнего датчика", "Давление"):
            packed_bytes = struct.pack('<HH', registers[0], registers[1])
            value = struct.unpack('<I', packed_bytes)[0]

        return round(value * scale, 2)

    def read_all(self):
        """Считывает данные со всех подключённых устройств по всем readers.

        Возвращает словарь вида {индекс_устройства: {адрес_регистра: значения}}.
        """
        logger.info("Чтение данных со всех устройств")
        data = {}
        for index, device in enumerate(self.config):
            device_data = {}
            for reader in device.get("readers", []):
                value = self.read_reader(index, reader)
                device_data[reader["address"]] = value
                if self.storage is not None:
                    self.storage.save_reading(index, device.get("info"), reader, value)
            data[index] = device_data
        logger.info("Итоговые данные: {}", data)
        return data

    def disconnect_all(self):
        """Закрывает соединения со всеми устройствами."""
        logger.info("Отключение всех устройств")
        for index, client in self.clients.items():
            client.close()
            logger.info("Устройство {} отключено", index)
        self.clients.clear()


if __name__ == "__main__":
    storage = ReadingStorage()
    manager = ModbusManager(storage=storage)
    manager.load_config()
    manager.connect_all()
    try:
        manager.read_all()
    finally:
        manager.disconnect_all()
        storage.close()
