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
        self.config = [d for d in devices if d.get("protocol", "modbus") == "modbus"]
        logger.info("Загружено устройств: {}", len(self.config))
        return self.config

    def connect_all(self):
        """Подключается ко всем устройствам из конфигурации и сохраняет клиентов."""
        logger.info("Подключение к устройствам ({} шт.)", len(self.config))
        for index, device in enumerate(self.config):
            if device.get("port") is None or device.get("address") is None:
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
                # Порт не существует, занят другим процессом или недоступен
                logger.error(
                    "Ошибка открытия порта {} у устройства {}: {}",
                    device["port"], index, exc,
                )
                continue
            if connected:
                address = self.resolve_address(index)
                if address is None:
                    # Устройство не ответило — считаем его неподключённым
                    client.close()
                    continue
                self.clients[index] = client
                logger.info(
                    "Устройство {} подключено (порт {}, адрес {})",
                    index, device["port"], address,
                )
            else:
                logger.error(
                    "Не удалось подключиться к устройству {} (порт {})",
                    index, device["port"],
                )
        return self.clients

    def resolve_address(self, device_index):
        """Определяет реальный адрес устройства.

        Если в конфигурации задан address_register (регистр, где хранится адрес
        устройства), читает его по заводскому адресу из поля address.
        Иначе берёт адрес напрямую из конфигурации.
        Возвращает адрес либо None, если устройство не ответило.
        """
        device = self.config[device_index]
        address_register = device.get("address_register")
        if address_register is None:
            # Регистр адреса не задан — используем адрес из конфигурации как есть
            self.addresses[device_index] = device["address"]
            logger.info(
                "Устройство {}: адрес из конфигурации {}",
                device_index, device["address"],
            )
            return self.addresses[device_index]
        client = self.clients.get(device_index)
        if client is None:
            logger.error("Устройство {} не подключено", device_index)
            return None
        bootstrap = device.get("address", 1)
        logger.info(
            "Определение адреса устройства {}: чтение регистра {} по адресу {}",
            device_index, address_register, bootstrap,
        )
        registers = self._read_registers(
            client, 3, address_register, 1, bootstrap,
        )
        if registers is None:
            # Нет ответа — подключение к устройству невозможно
            logger.error("Не удалось определить адрес устройства {}", device_index)
            return None
        self.addresses[device_index] = registers[0]
        logger.info("Устройство {}: реальный адрес {}", device_index, registers[0])
        return self.addresses[device_index]

    def _read_registers(self, client, function, register_address, count, device_address):
        """Низкоуровневое чтение регистров с обработкой ошибок pymodbus.

        function — 1 (coil), 2 (discrete), 3 (holding) или 4 (input).
        Возвращает список значений регистров/битов либо None при ошибке.
        """
        if function == 1:
            read_method = client.read_coils
        elif function == 2:
            read_method = client.read_discrete_inputs
        elif function == 4:
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
        if function in (1, 2):
            # Для катушек и дискретных входов pymodbus возвращает биты
            return list(result.bits[:count])
        return result.registers

    def read_reader(self, device_index, reader):
        """Читает регистры одного устройства по описанию reader.

        reader — словарь вида {"address": 1234, "count": 1,
        "function_code": 1 | 2 | 3 | 4 | "coil" | "discrete" | "holding" | "input"}
        (по умолчанию 3).
        Возвращает список значений регистров/битов либо None при ошибке.
        """
        client = self.clients.get(device_index)
        if client is None:
            logger.error("Устройство {} не подключено", device_index)
            return None
        device_address = self.addresses.get(device_index)
        register_address = reader["address"]
        count = reader.get("count", 1)
        function = reader.get("function_code", "holding")
        if isinstance(function, str):
            # Имена функций: coil (1), discrete (2), holding (3), input (4)
            function = {"coil": 1, "discrete": 2, "holding": 3, "input": 4}.get(function.lower())
        if function not in (1, 2, 3, 4):
            logger.error("Неизвестный функциональный код: {}", reader.get("function_code"))
            return None
        logger.info(
            "Чтение {} регистров с адреса {} функцией {} (устройство {}, адрес {})",
            count, register_address, function, device_index, device_address,
        )
        registers = self._read_registers(client, function, register_address, count, device_address)
        if registers is None:
            return None
        logger.info("Прочитаны значения: {}", registers)
        return registers

    def write_input(self, device_index, input_def):
        """Записывает значение в регистр/катушку устройства по описанию input.

        input_def — словарь вида {"address": 1, "function_code": 5 | 6 | 16,
        "value": 1, "info": "Назначение"} (функция 5 — запись катушки,
        6 — запись одного регистра, 16 — запись нескольких).
        Для катушки значение трактуется как bool: true — замкнуть, false — разомкнуть.
        Возвращает True при успехе, иначе False.
        """
        client = self.clients.get(device_index)
        if client is None:
            logger.error("Устройство {} не подключено", device_index)
            return False
        device_address = self.addresses.get(device_index)
        if device_address is None:
            logger.error("У устройства {} не определён адрес", device_index)
            return False
        register_address = input_def["address"]
        value = input_def["value"]
        function = input_def.get("function_code", 6)
        logger.info(
            "Запись значения {} в регистр {} функцией {} (устройство {}, адрес {})",
            value, register_address, function, device_index, device_address,
        )
        try:
            try:
                if function == 5:
                    result = client.write_coil(
                        register_address, bool(value), slave=device_address,
                    )
                elif function == 16:
                    result = client.write_registers(
                        register_address, [value], slave=device_address,
                    )
                else:
                    result = client.write_register(
                        register_address, value, slave=device_address,
                    )
            except TypeError:
                # В pymodbus 3.9+ параметр slave переименован в device_id
                if function == 5:
                    result = client.write_coil(
                        register_address, bool(value), device_id=device_address,
                    )
                elif function == 16:
                    result = client.write_registers(
                        register_address, [value], device_id=device_address,
                    )
                else:
                    result = client.write_register(
                        register_address, value, device_id=device_address,
                    )
        except ModbusIOException as exc:
            # Устройство не ответило после повторных запросов
            logger.error("Нет ответа от устройства {}: {}", device_address, exc)
            return False
        if result.isError():
            logger.error("Ошибка записи регистра: {}", result)
            return False
        logger.info("Значение {} записано в регистр {}", value, register_address)
        return True

    def write_all(self):
        """Записывает все inputs с заданным value для всех подключённых устройств.

        Возвращает словарь вида {индекс_устройства: {имя_input: результат}}.
        """
        logger.info("Запись inputs для всех устройств")
        results = {}
        for index, device in enumerate(self.config):
            device_results = {}
            for input_def in device.get("inputs", []):
                if input_def.get("value") is None:
                    # Значение не задано — запись не выполняется
                    continue
                name = input_def.get("info", input_def["address"])
                device_results[name] = self.write_input(index, input_def)
            results[index] = device_results
        logger.info("Итоги записи: {}", results)
        return results

    def read_all(self):
        """Считывает данные со всех подключённых устройств по всем readers.

        Возвращает словарь вида {индекс_устройства: {адрес_регистра: значения}}.
        """
        logger.info("Чтение данных со всех устройств")
        data = {}
        for index, device in enumerate(self.config):
            device_data = {}
            for reader in device.get("readers", []):
                values = self.read_reader(index, reader)
                device_data[reader["address"]] = values
                if self.storage is not None:
                    # Сохраняем показание в базу данных
                    self.storage.save_reading(index, device.get("info"), reader, values)
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
        manager.write_all()
        manager.read_all()
    finally:
        manager.disconnect_all()
        storage.close()
