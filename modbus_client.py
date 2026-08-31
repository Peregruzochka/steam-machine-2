# -*- coding: utf-8 -*-
"""Модуль работы с датчиками по MODBUS RTU.

Загружает настройки устройств из sensor.json, подключается к ним
и считывает данные из регистров, описанных в секции readers.
"""

import json

from loguru import logger
from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusIOException
from serial import SerialException


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
        logger.info("ModbusManager создан, файл конфигурации: {}", config_path)

    def load_config(self):
        """Читает sensor.json и возвращает список устройств."""
        logger.info("Загрузка конфигурации из файла {}", self.config_path)
        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                self.config = json.load(file)
        except FileNotFoundError:
            logger.error("Файл конфигурации не найден: {}", self.config_path)
            raise
        except json.JSONDecodeError as exc:
            logger.error("Ошибка разбора JSON: {}", exc)
            raise
        logger.info("Загружено устройств: {}", len(self.config))
        return self.config

    def connect_all(self):
        """Подключается ко всем устройствам из конфигурации и сохраняет клиентов."""
        logger.info("Подключение к устройствам ({} шт.)", len(self.config))
        for index, device in enumerate(self.config):
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
                # Порт не существует, занят другим процессом или недоступен
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

    def read_reader(self, device_index, reader):
        """Читает регистры одного устройства по описанию reader.

        reader — словарь вида {"address": 1234, "count": 1,
        "function_code": 3 | 4 | "holding" | "input"} (по умолчанию 3).
        Возвращает список значений регистров либо None при ошибке.
        """
        client = self.clients.get(device_index)
        if client is None:
            logger.error("Устройство {} не подключено", device_index)
            return None
        device_address = self.config[device_index]["address"]
        register_address = reader["address"]
        count = reader.get("count", 1)
        function = reader.get("function_code", "holding")
        if isinstance(function, str):
            # Имена функций: holding (3) и input (4)
            function = {"holding": 3, "input": 4}.get(function.lower())
        if function not in (3, 4):
            logger.error("Неизвестный функциональный код: {}", reader.get("function_code"))
            return None
        logger.info(
            "Чтение {} регистров с адреса {} функцией {} (устройство {}, адрес {})",
            count, register_address, function, device_index, device_address,
        )
        if function == 4:
            read_method = client.read_input_registers
        else:
            read_method = client.read_holding_registers
        try:
            try:
                result = read_method(
                    address=register_address, count=count, slave=device_address,
                )
            except TypeError:
                # В pymodbus 3.9+ параметр slave переименован в device_id
                result = read_method(
                    address=register_address, count=count, device_id=device_address,
                )
        except ModbusIOException as exc:
            # Устройство не ответило после повторных запросов
            logger.error("Нет ответа от устройства {}: {}", device_address, exc)
            return None
        if result.isError():
            logger.error("Ошибка чтения регистров: {}", result)
            return None
        logger.info("Прочитаны значения: {}", result.registers)
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
    manager = ModbusManager()
    manager.load_config()
    manager.connect_all()
    try:
        manager.read_all()
    finally:
        manager.disconnect_all()
