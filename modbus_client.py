# -*- coding: utf-8 -*-
"""Модуль работы с датчиками по MODBUS RTU.

Загружает настройки устройств из sensor.json, подключается к ним
и считывает данные из регистров, описанных в секции readers.
"""

import json
import logging

from pymodbus.client import ModbusSerialClient

# Настройка вывода логов в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class ModbusManager:
    """Менеджер MODBUS RTU устройств.

    Отвечает за загрузку конфигурации из sensor.json,
    подключение к устройствам и чтение регистров из секции readers.
    """

    def __init__(self, config_path="sensor.json"):
        """Сохраняет путь к конфигурации и создаёт пустые коллекции клиентов."""
        self.config_path = config_path
        self.config = []
        self.clients = {}
        logger.info("ModbusManager создан, файл конфигурации: %s", config_path)

    def load_config(self):
        """Читает sensor.json и возвращает список устройств."""
        logger.info("Загрузка конфигурации из файла %s", self.config_path)
        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                self.config = json.load(file)
        except FileNotFoundError:
            logger.error("Файл конфигурации не найден: %s", self.config_path)
            raise
        except json.JSONDecodeError as exc:
            logger.error("Ошибка разбора JSON: %s", exc)
            raise
        logger.info("Загружено устройств: %d", len(self.config))
        return self.config

    def connect_all(self):
        """Подключается ко всем устройствам из конфигурации и сохраняет клиентов."""
        logger.info("Подключение к устройствам (%d шт.)", len(self.config))
        for index, device in enumerate(self.config):
            client = ModbusSerialClient(
                port=device["port"],
                baudrate=device.get("baudrate", 9600),
                bytesize=device.get("bytesize", 8),
                stopbits=device.get("stopbit", 1),
                parity=device.get("parity", "N"),
                timeout=3,
            )
            if client.connect():
                self.clients[index] = client
                logger.info(
                    "Устройство %d подключено (порт %s, адрес %s)",
                    index, device["port"], device["address"],
                )
            else:
                logger.error(
                    "Не удалось подключиться к устройству %d (порт %s)",
                    index, device["port"],
                )
        return self.clients

    def read_reader(self, device_index, reader):
        """Читает регистры одного устройства по описанию reader.

        reader — словарь вида {"address": 1234, "count": 1}.
        Возвращает список значений регистров либо None при ошибке.
        """
        client = self.clients.get(device_index)
        if client is None:
            logger.error("Устройство %d не подключено", device_index)
            return None
        device_address = self.config[device_index]["address"]
        register_address = reader["address"]
        count = reader.get("count", 1)
        logger.info(
            "Чтение %d регистров с адреса %d (устройство %d, адрес %d)",
            count, register_address, device_index, device_address,
        )
        try:
            result = client.read_holding_registers(
                address=register_address, count=count, slave=device_address,
            )
        except TypeError:
            # В pymodbus 3.9+ параметр slave переименован в device_id
            result = client.read_holding_registers(
                address=register_address, count=count, device_id=device_address,
            )
        if result.isError():
            logger.error("Ошибка чтения регистров: %s", result)
            return None
        logger.info("Прочитаны значения: %s", result.registers)
        return result.registers

    def read_all(self):
        """Считывает данные со всех подключённых устройств по всем readers.

        Возвращает словарь вида {индекс_устройства: {адрес_регистра: значения}}.
        """
        logger.info("Чтение данных со всех устройств")
        data = {}
        for index, device in enumerate(self.config):
            device_data = {}
            for reader in device.get("readers", []):
                device_data[reader["address"]] = self.read_reader(index, reader)
            data[index] = device_data
        logger.info("Итоговые данные: %s", data)
        return data

    def disconnect_all(self):
        """Закрывает соединения со всеми устройствами."""
        logger.info("Отключение всех устройств")
        for index, client in self.clients.items():
            client.close()
            logger.info("Устройство %d отключено", index)
        self.clients.clear()


if __name__ == "__main__":
    manager = ModbusManager()
    manager.load_config()
    manager.connect_all()
    try:
        manager.read_all()
    finally:
        manager.disconnect_all()
