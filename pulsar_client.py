# -*- coding: utf-8 -*-
"""Модуль работы со счётчиками воды Пульсар с цифровым выходом.

Реализует фирменный протокол обмена (НЕ Modbus): пакеты вида
ADDR(4, BCD) / F(1) / L(1) / DATA / ID(2) / CRC16(2) поверх COM-порта.
Поддержано чтение текущего объёма (F=0x01) и системного времени (F=0x04).
"""

import json
import struct

import serial
from loguru import logger

# Коды функций протокола Пульсар
FUNC_READ_VOLUME = 0x01
FUNC_READ_TIME = 0x04

# Таймаут ожидания ответа, сек
RESPONSE_TIMEOUT = 3


def calc_crc16(data):
    """Вычисляет CRC16 по алгоритму из описания протокола (полином 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            low_bit = crc & 0x1
            crc >>= 1
            if low_bit == 1:
                crc ^= 0xA001
    return crc


def address_to_bcd(address):
    """Преобразует серийный номер прибора в 4 байта BCD (старшим байтом вперёд).

    Например, №12345678 -> байты 12h 34h 56h 78h.
    """
    digits = str(int(address)).zfill(8)
    if not digits.isdigit() or len(digits) > 8:
        logger.error("Некорректный адрес прибора: {}", address)
        raise ValueError(f"Некорректный адрес прибора: {address}")
    return bytes(
        (int(digits[index]) << 4) | int(digits[index + 1]) for index in range(0, 8, 2)
    )


def build_packet(address, function, data=b"", request_id=b"\xfd\xec"):
    """Собирает пакет запроса по протоколу Пульсар.

    L — общая длина пакета: ADDR(4) + F(1) + L(1) + DATA + ID(2) + CRC16(2).
    """
    packet_without_crc = address_to_bcd(address) + bytes([function, len(data) + 10]) + data + request_id
    return packet_without_crc + struct.pack("<H", calc_crc16(packet_without_crc))


class PulsarManager:
    """Менеджер счётчиков Пульсар.

    Отвечает за загрузку конфигурации (устройства с protocol == "pulsar"),
    подключение по COM-порту и чтение показаний.
    """

    def __init__(self, config_path="sensor.json", storage=None):
        """Сохраняет путь к конфигурации, хранилище и создаёт пустые коллекции портов."""
        self.config_path = config_path
        self.storage = storage
        self.config = []
        self.ports = {}
        logger.info("PulsarManager создан, файл конфигурации: {}", config_path)

    def load_config(self):
        """Читает sensor.json и отбирает только устройства Пульсар."""
        logger.info("Загрузка конфигурации из файла {}", self.config_path)
        with open(self.config_path, "r", encoding="utf-8") as file:
            devices = json.load(file)
        self.config = [d for d in devices if d.get("protocol") == "pulsar"]
        logger.info("Загружено устройств Пульсар: {}", len(self.config))
        return self.config

    def connect_all(self):
        """Открывает COM-порты для всех устройств Пульсар."""
        logger.info("Подключение к устройствам Пульсар ({} шт.)", len(self.config))
        for index, device in enumerate(self.config):
            if device.get("port") is None or device.get("address") is None:
                # Порт или адрес не указан — устройство ещё не настроено
                logger.warning("У устройства {} не указан порт или адрес, пропуск", index)
                continue
            try:
                self.ports[index] = serial.Serial(
                    port=device["port"],
                    baudrate=device.get("baudrate", 9600),
                    bytesize=device.get("bytesize", 8),
                    stopbits=device.get("stopbit", 1),
                    parity=device.get("parity", "N"),
                    timeout=RESPONSE_TIMEOUT,
                )
                logger.info(
                    "Прибор {} подключен (порт {}, №{})",
                    index, device["port"], device["address"],
                )
            except serial.SerialException as exc:
                # Порт не существует, занят другим процессом или недоступен
                logger.error(
                    "Ошибка открытия порта {} у прибора {}: {}",
                    device["port"], index, exc,
                )
        return self.ports

    def exchange(self, device_index, function, data=b""):
        """Отправляет запрос и получает проверенный ответ прибора.

        Возвращает DATA_OUT (bytes) либо None при ошибке.
        """
        port = self.ports.get(device_index)
        if port is None:
            logger.error("Прибор {} не подключен", device_index)
            return None
        address = self.config[device_index]["address"]
        packet = build_packet(address, function, data)
        logger.info("Запрос прибору {}: функция {:02X}, данные {}", address, function, data.hex())
        port.write(packet)
        header = port.read(6)
        if len(header) < 6:
            logger.error("Нет ответа от прибора {}", address)
            return None
        response_function = header[4]
        total_length = header[5]
        response = header + port.read(total_length - 6)
        if len(response) < total_length:
            logger.error("Неполный ответ от прибора {}", address)
            return None
        crc_received = struct.unpack("<H", response[-2:])[0]
        if crc_received != calc_crc16(response[:-2]):
            logger.error("Неверная CRC в ответе прибора {}", address)
            return None
        if response_function == 0x00:
            # F=0x00 — ответ на некорректный запрос, первый байт DATA — код ошибки
            logger.error(
                "Прибор {} сообщил об ошибке, код {}",
                address, response[6] if total_length > 6 else "?",
            )
            return None
        # DATA_OUT расположен между заголовком (6 байт) и ID+CRC (4 байта)
        return response[6:total_length - 4]

    def read_volume(self, device_index, channel=1):
        """Читает текущее значение объёма (F=0x01) по битовой маске канала.

        Возвращает float (м³) либо None при ошибке.
        """
        logger.info("Чтение объёма прибора {}, канал {}", device_index, channel)
        mask = struct.pack("<I", 1 << (channel - 1))
        payload = self.exchange(device_index, FUNC_READ_VOLUME, mask)
        if payload is None or len(payload) < 4:
            logger.error("Нет данных объёма от прибора {}", device_index)
            return None
        volume = struct.unpack("<f", payload[:4])[0]
        logger.info("Объём прибора {}: {} м³", device_index, volume)
        return volume

    def read_time(self, device_index):
        """Читает системное время прибора (F=0x04).

        Возвращает словарь с полями год/мес/день/час/мин/сек либо None при ошибке.
        """
        logger.info("Чтение системного времени прибора {}", device_index)
        payload = self.exchange(device_index, FUNC_READ_TIME)
        if payload is None or len(payload) < 6:
            logger.error("Нет данных времени от прибора {}", device_index)
            return None
        result = {
            "год": 2000 + payload[0],
            "мес": payload[1],
            "день": payload[2],
            "час": payload[3],
            "мин": payload[4],
            "сек": payload[5],
        }
        logger.info("Время прибора {}: {}", device_index, result)
        return result

    def read_all(self):
        """Считывает объём со всех подключённых приборов по всем каналам из readers."""
        logger.info("Чтение данных со всех приборов Пульсар")
        data = {}
        for index, device in enumerate(self.config):
            device_data = {}
            for reader in device.get("readers", []):
                channel = reader.get("channel", 1)
                value = self.read_volume(index, channel)
                if value is not None and reader.get("scale"):
                    value *= reader["scale"]
                device_data[reader.get("info", f"канал{channel}")] = value
                if self.storage is not None:
                    # Ключ совпадает с колонкой CSV и ключом модели датчиков
                    self.storage.save_reading(
                        ("pulsar", index, reader.get("info", f"канал{channel}")), value
                    )
            data[index] = device_data
        logger.info("Итоговые данные: {}", data)
        return data

    def disconnect_all(self):
        """Закрывает все открытые COM-порты."""
        logger.info("Отключение всех приборов Пульсар")
        for index, port in self.ports.items():
            port.close()
            logger.info("Прибор {} отключен", index)
        self.ports.clear()


if __name__ == "__main__":
    manager = PulsarManager()
    manager.load_config()
    manager.connect_all()
    try:
        manager.read_all()
    finally:
        manager.disconnect_all()
