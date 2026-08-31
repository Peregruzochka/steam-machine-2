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

    def _read_registers(self, client, info, count, read_register_address, device_address, function_code):
        ### тут начинается развязка того, что сделать с регистром
        ### важно чтобы значения сочетались с теми, которые есть в sensor.json
        try:
            if info in ("Температура внешнего датчика", "Давление"):
                # Комбинированный датчик читается блоком с нулевого адреса,
                # нужные регистры берутся срезом по смещению из конфигурации
                result = client.read_input_registers(0, count=16, device_id=device_address)
                registers = result.registers[read_register_address:read_register_address + count]
                logger.info("Получена последовательность регистров устройства {}: {}", device_address, registers)
                return registers
            # Остальные датчики читаются по своему адресу: функция 4 — input, иначе holding
            if function_code == 4:
                result = client.read_input_registers(read_register_address, count=count, device_id=device_address)
            else:
                result = client.read_holding_registers(read_register_address, count=count, device_id=device_address)
            logger.info("Получена последовательность регистров устройства {}: {}", device_address, result.registers)
            return result.registers
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
        function_code = reader.get("function_code", 3)

        registers = self._read_registers(client, info, count, read_register_address, device_address, function_code)
        if registers is None:
            return None

        value = self.parse_modbus_value(registers, info, scale)
        logger.info("Прочитаны значения: {}", value)
        return value

    def parse_modbus_value(self, registers, info, scale):
        value = None

        ### тут начинается развязка того, что сделать с регистром
        ### важно чтобы значения сочетались с теми, которые есть в sensor.json

        if len(registers) >= 2:
            # Два регистра собираются в 32-битное целое (младшее слово первым)
            packed_bytes = struct.pack('<HH', registers[0], registers[1])
            value = struct.unpack('<I', packed_bytes)[0]
        elif len(registers) == 1:
            # Одиночный регистр берётся как есть
            value = registers[0]

        return round(value * scale, 2)

    def write_input(self, device_index, input_def, value=None):
        """Записывает значение в регистр/катушку устройства по описанию input.

        input_def — словарь вида {"address": 0, "function_code": 5, "info": "Реле 1"}.
        Функция 5 — запись одной катушки: true — замкнуть, false — разомкнуть.
        Аргумент value перекрывает значение из описания. Возвращает True при успехе.
        """
        client = self.clients.get(device_index)
        if client is None:
            logger.error("Устройство {} не подключено, запись невозможна", device_index)
            return False
        device_address = self.config[device_index]["address"]
        register_address = input_def["address"]
        function_code = input_def.get("function_code", 5)
        if value is None:
            value = input_def.get("value", False)
        logger.info(
            "Запись значения {} в регистр {} (функция {}, устройство {}, адрес {})",
            value, register_address, function_code, device_index, device_address,
        )
        try:
            if function_code == 5:
                result = client.write_coil(register_address, bool(value), device_id=device_address)
            elif function_code == 6:
                result = client.write_register(register_address, int(value), device_id=device_address)
            else:
                logger.error("Запись с функцией {} не поддерживается", function_code)
                return False
        except ModbusIOException as exc:
            logger.error("Нет ответа от устройства {}: {}", device_address, exc)
            return False
        if result.isError():
            logger.error("Ошибка записи регистра: {}", result)
            return False
        logger.info("Значение {} записано в регистр {}", value, register_address)
        return True

    def find_relay_device(self):
        """Ищет в конфигурации модуль реле (устройство с inputs, содержащими «Реле 1»).

        Возвращает (индекс_устройства, описание_input) либо (None, None).
        """
        for index, device in enumerate(self.config):
            for input_def in device.get("inputs", []):
                if "Реле 1" in input_def.get("info", ""):
                    logger.info("Модуль реле найден: устройство {}", index)
                    return index, input_def
        logger.warning("Модуль реле не найден в конфигурации")
        return None, None

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
