# -*- coding: utf-8 -*-
"""Бэкенд панели оператора: модель датчиков и контроллер для QML.

Строит список датчиков из sensor.json (элементы UI основаны на readers),
опрашивает реальные устройства через ModbusManager и PulsarManager
в фоновом потоке, управляет записью в CSV и реле подогрева.
"""

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Property,
    Signal,
    Slot,
    Qt,
)

from loguru import logger

from modbus_client import ModbusManager
from pulsar_client import PulsarManager
from storage import ReadingStorage

# Роли модели для доступа из QML
NAME_ROLE = Qt.UserRole + 1
VALUE_ROLE = Qt.UserRole + 2
UNIT_ROLE = Qt.UserRole + 3
DECIMALS_ROLE = Qt.UserRole + 4
CHART_VISIBLE_ROLE = Qt.UserRole + 5

# Категории датчиков — задают порядок строк в UI как в дизайне
CATEGORY_FLOW_COUNTER = 0   # счётчики воды Пульсар, м³
CATEGORY_PRESSURE = 1       # датчики давления
CATEGORY_TEMPERATURE = 2    # датчики температуры (отображаются на графике)
CATEGORY_PH = 3             # датчики pH
CATEGORY_FLOW_RATE = 4      # расходомер ВЗЛЕТ

# Названия категорий в интерфейсе; {n} заменяется на порядковый номер
CATEGORY_TITLES = {
    CATEGORY_FLOW_COUNTER: 'Счетчик воды ультразвуковой "Пульсар" №{n}',
    CATEGORY_PRESSURE: "Датчик давления №{n}",
    CATEGORY_TEMPERATURE: "Датчик температуры №{n}",
    CATEGORY_PH: "Датчик pH №{n}",
    CATEGORY_FLOW_RATE: 'Расходомер-счетчик "ВЗЛЕТ ТЭР"',
}

# Реальные диапазоны величин для нормировки на общую шкалу графика 0…100 %
# (значения датчика приводятся к доле своего диапазона)
RANGE_TEMPERATURE = (-40.0, 120.0)  # температура, °C
RANGE_PRESSURE = (0.0, 1013.25)     # давление, кПа (максимум датчика 10 атм ≈ 1013.25 кПа)
RANGE_PH = (0.0, 14.0)              # кислотность, pH (стандартная шкала)
RANGE_FLOW = (0.0, 10.0)            # расход/объём счётчиков, м³ и м³/ч

# Соответствие категории датчика и её рабочего диапазона
CATEGORY_RANGES = {
    CATEGORY_FLOW_COUNTER: RANGE_FLOW,
    CATEGORY_PRESSURE: RANGE_PRESSURE,
    CATEGORY_TEMPERATURE: RANGE_TEMPERATURE,
    CATEGORY_PH: RANGE_PH,
    CATEGORY_FLOW_RATE: RANGE_FLOW,
}

# Верх общей шкалы графика, % (все серии нормируются на 0…CHART_SCALE_MAX)
CHART_SCALE_MAX = 100.0

# Палитра цветов серий графика; цвет датчика = палитра[индекс строки]
CHART_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f1c40f",
    "#9b59b6", "#e67e22", "#1abc9c", "#ff6f91",
    "#00bfff", "#8bc34a", "#ffd166", "#b39ddb",
]

# Период опроса устройств, сек
POLL_INTERVAL = 2.0


@dataclass
class Sensor:
    """Один датчик в списке UI: имя, значение, единицы, точность и привязка к устройству."""
    name: str
    unit: str
    value: float = 0.0
    decimals: int = 2
    # Ключ показания: (протокол, индекс устройства в менеджере, идентификатор reader)
    key: tuple = field(default=())
    # Категория для сортировки и отбора серий графика
    category: int = CATEGORY_FLOW_RATE
    # Параметры графика: рабочий диапазон (для нормировки), цвет серии, видимость
    range_min: float = 0.0
    range_max: float = 1.0
    color: str = "#888888"
    visible: bool = False


def detect_category(protocol, reader):
    """Определяет категорию датчика по протоколу и описанию reader.

    Используется для сортировки списка как в дизайн-макете.
    """
    if protocol == "pulsar":
        return CATEGORY_FLOW_COUNTER
    # Приводим к нижнему регистру, чтобы ловить любые падежи («температуры» и т.п.)
    info = reader.get("info", "").lower()
    if "давлен" in info:
        return CATEGORY_PRESSURE
    if "температур" in info:
        return CATEGORY_TEMPERATURE
    if info == "ph":
        return CATEGORY_PH
    return CATEGORY_FLOW_RATE


def build_sensors(config_path="sensor.json"):
    """Строит список датчиков для UI из конфигурации устройств.

    Читает sensor.json целиком, каждому reader ставит в соответствие строку
    списка с именем по категории и порядковым номером.
    """
    import json

    with open(config_path, "r", encoding="utf-8") as file:
        devices = json.load(file)

    sensors = []
    # Счётчики индексов внутри каждого менеджера: менеджеры фильтруют конфигурацию
    # по протоколу, поэтому их нумерация устройств не совпадает с общим списком
    manager_indices = {"pulsar": 0, "modbus": 0}
    for device in devices:
        protocol = device.get("protocol", "modbus")
        manager_index = manager_indices[protocol]
        manager_indices[protocol] += 1
        for reader in device.get("readers", []):
            # Ключ совпадает с тем, по чему потом раскладываются данные опроса
            if protocol == "pulsar":
                key = ("pulsar", manager_index, reader.get("info", f"канал{reader.get('channel', 1)}"))
            else:
                key = ("modbus", manager_index, reader.get("address"))
            sensors.append(
                Sensor(
                    name="",  # имя заполняется после сортировки
                    unit=reader.get("unit", ""),
                    decimals=int(reader.get("decimals", 2)),
                    key=key,
                    category=detect_category(protocol, reader),
                )
            )

    # Сортировка по категории и порядку в конфигурации — как в дизайн-макете
    sensors.sort(key=lambda s: (s.category, s.key[1]))
    counters = {}
    for index, sensor in enumerate(sensors):
        counters[sensor.category] = counters.get(sensor.category, 0) + 1
        sensor.name = CATEGORY_TITLES[sensor.category].format(n=counters[sensor.category])
        # Рабочий диапазон для нормировки, цвет серии из палитры; по умолчанию
        # на графике показываются только датчики температуры
        sensor.range_min, sensor.range_max = CATEGORY_RANGES[sensor.category]
        sensor.color = CHART_COLORS[index % len(CHART_COLORS)]
        sensor.visible = sensor.category == CATEGORY_TEMPERATURE
    logger.info("Сформирован список датчиков: {} шт.", len(sensors))
    return sensors


def normalize_value(value, range_min, range_max):
    """Приводит значение к общей шкале графика 0…100 % по рабочему диапазону.

    Значения вне диапазона ограничиваются краями шкалы.
    """
    span = range_max - range_min
    if span <= 0:
        logger.warning("Нулевой диапазон графика, значение нормировано в 0")
        return 0.0
    percent = (value - range_min) / span * CHART_SCALE_MAX
    return max(0.0, min(CHART_SCALE_MAX, percent))


class SensorModel(QAbstractListModel):
    """Модель списка датчиков для QML (роли: name, value, unit, decimals)."""

    def __init__(self, sensors, parent=None):
        """Сохраняет готовый список датчиков, сформированный build_sensors."""
        super().__init__(parent)
        self._rows = sensors
        self._index_by_key = {row.key: i for i, row in enumerate(self._rows)}
        logger.info("SensorModel создан, строк: {}", len(self._rows))

    def rowCount(self, parent=QModelIndex()):
        """Возвращает число строк списка."""
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self):
        """Возвращает имена ролей модели для QML."""
        return {
            NAME_ROLE: b"name",
            VALUE_ROLE: b"value",
            UNIT_ROLE: b"unit",
            DECIMALS_ROLE: b"decimals",
            CHART_VISIBLE_ROLE: b"chartVisible",
        }

    def data(self, index, role=Qt.DisplayRole):
        """Возвращает данные строки по роли."""
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == NAME_ROLE:
            return row.name
        if role == VALUE_ROLE:
            return row.value
        if role == UNIT_ROLE:
            return row.unit
        if role == DECIMALS_ROLE:
            return row.decimals
        if role == CHART_VISIBLE_ROLE:
            return row.visible
        return None

    def set_visible(self, row_index, state):
        """Включает/выключает отображение датчика на графике (тумблер в UI)."""
        if not (0 <= row_index < len(self._rows)):
            logger.error("Неверный индекс строки датчика: {}", row_index)
            return
        row = self._rows[row_index]
        row.visible = bool(state)
        model_index = self.index(row_index, 0)
        self.dataChanged.emit(model_index, model_index, [CHART_VISIBLE_ROLE])
        logger.info("Датчик «{}» на графике: {}", row.name, "вкл" if state else "выкл")

    def emit_values_changed(self):
        """Сообщает QML об обновлении всех значений."""
        top = self.index(0, 0)
        bottom = self.index(len(self._rows) - 1, 0)
        self.dataChanged.emit(top, bottom, [VALUE_ROLE])

    def apply_readings(self, readings):
        """Раскладывает словарь показаний {ключ: значение} по строкам модели.

        Отсутствующие и None-значения оставляют предыдущее число на экране.
        """
        changed = False
        for key, value in readings.items():
            row_index = self._index_by_key.get(key)
            if row_index is None or value is None:
                continue
            self._rows[row_index].value = round(float(value), self._rows[row_index].decimals)
            changed = True
        if changed:
            self.emit_values_changed()

    def chart_series_info(self):
        """Возвращает описание серий графика: имя, цвет и видимость каждой."""
        return [
            {"name": row.name, "color": row.color, "visible": row.visible}
            for row in self._rows
        ]

    def rows(self):
        """Возвращает список строк модели."""
        return self._rows


class PollWorker(QObject):
    """Фоновый опросчик устройств.

    В отдельном потоке подключается к устройствам, циклически читает показания
    через ModbusManager и PulsarManager, управляет записью CSV и реле подогрева.
    Результаты передаются в GUI-поток сигналами.
    """

    # Показания готовы: словарь {ключ: значение} (object передаётся между
    # потоками без преобразования в C++-тип, dict Shiboken не конвертирует)
    readingsReady = Signal(object)
    # Команда подогреву выполнена: (успех, состояние)
    heatingDone = Signal(bool, bool)

    def __init__(self, sensors_model, parent=None):
        """Сохраняет модель датчиков и создаёт менеджеров устройств и хранилище."""
        super().__init__(parent)
        self._model = sensors_model
        # Колонки CSV «широкого» формата: имя датчика из UI + единицы измерения
        columns = [
            (row.key, f"{row.name}, {row.unit}") for row in self._model.rows()
        ]
        self._storage = ReadingStorage(columns=columns)
        self._modbus = ModbusManager(storage=self._storage)
        self._pulsar = PulsarManager(storage=self._storage)
        # Команды из GUI-потока (None — команды нет)
        self._pending_heating = None
        self._pending_recording = None
        # Событие остановки цикла
        self._stop_event = threading.Event()
        logger.info("PollWorker создан")

    def request_heating(self, state):
        """Ставит в очередь команду включения/выключения реле подогрева."""
        logger.info("Запрос управления подогревом: {}", state)
        self._pending_heating = state

    def request_recording(self, state):
        """Ставит в очередь команду включения/выключения записи в CSV."""
        logger.info("Запрос управления записью: {}", state)
        self._pending_recording = state

    def stop(self):
        """Останавливает цикл опроса."""
        logger.info("Остановка PollWorker")
        self._stop_event.set()

    def run(self):
        """Точка входа фонового потока: подключение и цикл опроса."""
        logger.info("PollWorker запущен в фоновом потоке")
        try:
            self._modbus.load_config()
            self._pulsar.load_config()
            self._modbus.connect_all()
            self._pulsar.connect_all()
            self._relay = self._modbus.find_relay_device()

            while not self._stop_event.is_set():
                started = time.monotonic()
                self._handle_commands()
                self._poll_once()
                # Спим мелкими шагами, чтобы быстро реагировать на остановку
                while not self._stop_event.is_set() and time.monotonic() - started < POLL_INTERVAL:
                    time.sleep(0.1)
        except Exception as exc:
            logger.exception("Ошибка в цикле опроса: {}", exc)
        finally:
            self._modbus.disconnect_all()
            self._pulsar.disconnect_all()
            self._storage.close()
            logger.info("PollWorker завершён")

    def _handle_commands(self):
        """Выполняет отложенные команды записи и подогрева из GUI-потока."""
        if self._pending_recording is not None:
            state = self._pending_recording
            self._pending_recording = None
            if state and not self._storage.is_recording:
                self._storage.start_recording()
            elif not state and self._storage.is_recording:
                self._storage.stop_recording()

        if self._pending_heating is not None:
            state = self._pending_heating
            self._pending_heating = None
            device_index, input_def = self._relay
            if device_index is None:
                logger.error("Реле подогрева не найдено, команда проигнорирована")
                self.heatingDone.emit(False, state)
            else:
                success = self._modbus.write_input(device_index, input_def, value=state)
                self.heatingDone.emit(success, state)

    def _poll_once(self):
        """Читает все устройства и рассылает показания в GUI-поток."""
        readings = {}
        for index, device_data in self._modbus.read_all().items():
            for address, value in device_data.items():
                readings[("modbus", index, address)] = value
        for index, device_data in self._pulsar.read_all().items():
            for reader_info, value in device_data.items():
                readings[("pulsar", index, reader_info)] = value
        # Все устройства опрошены — пишем строку цикла в CSV
        self._storage.flush_row()
        self.readingsReady.emit(readings)


class Controller(QObject):
    """Контроллер для QML: состояние записи/подогрева, модель датчиков, график."""

    recordingChanged = Signal()
    heatingChanged = Signal()
    chartPoint = Signal(int, float, float)
    chartSeriesChanged = Signal()
    statusMessage = Signal(str)

    def __init__(self, parent=None):
        """Создаёт модель датчиков из конфигурации и запускает фоновый опрос."""
        super().__init__(parent)
        self._recording = False
        self._heating = False
        self._sensors = SensorModel(build_sensors(), self)
        self._t0 = time.monotonic()

        # Фоновый поток опроса устройств
        self._worker = PollWorker(self._sensors)
        self._worker.readingsReady.connect(self._on_readings_ready)
        self._worker.heatingDone.connect(self._on_heating_done)
        self._thread = threading.Thread(target=self._worker.run, daemon=True)
        self._thread.start()
        logger.info("Контроллер создан, опрос запущен")

    @Property(QObject, constant=True)
    def sensors(self):
        """Возвращает модель датчиков для QML."""
        return self._sensors

    @Property(bool, notify=recordingChanged)
    def recording(self):
        """Признак включённой записи."""
        return self._recording

    @Property(bool, notify=heatingChanged)
    def heating(self):
        """Признак включённого подогрева."""
        return self._heating

    @Property("QVariantList", notify=chartSeriesChanged)
    def chartSeriesInfo(self):
        """Возвращает описание серий графика (имя, цвет, видимость) для QML."""
        return self._sensors.chart_series_info()

    @Slot(int, bool)
    def setSensorVisible(self, row_index, state):
        """Включает/выключает отображение датчика на графике (тумблер в списке)."""
        logger.info("Тумблер графика: датчик {} -> {}", row_index, state)
        self._sensors.set_visible(row_index, state)
        self.chartSeriesChanged.emit()

    @Slot()
    def toggleRecording(self):
        """Включает/выключает запись показаний в CSV."""
        self._recording = not self._recording
        self.recordingChanged.emit()
        self._worker.request_recording(self._recording)
        self.statusMessage.emit(
            "Запись включена" if self._recording else "Запись выключена"
        )
        logger.info("Переключатель записи: {}", self._recording)

    @Slot()
    def toggleHeating(self):
        """Включает/выключает подогрев через реле."""
        self._heating = not self._heating
        self.heatingChanged.emit()
        self._worker.request_heating(self._heating)
        self.statusMessage.emit(
            "Подогрев включен" if self._heating else "Подогрев выключен"
        )
        logger.info("Переключатель подогрева: {}", self._heating)

    def shutdown(self):
        """Останавливает фоновый поток и закрывает устройства."""
        logger.info("Остановка контроллера")
        self._worker.stop()
        self._thread.join(timeout=10)

    def _on_readings_ready(self, readings):
        """Обрабатывает порцию показаний: обновляет модель и точки графика.

        На график выдаются только видимые датчики; каждое значение нормируется
        на общую шкалу 0…100 % по рабочему диапазону своей величины.
        """
        self._sensors.apply_readings(readings)
        t = time.monotonic() - self._t0
        for row_index, row in enumerate(self._sensors.rows()):
            if not row.visible:
                continue
            y = normalize_value(row.value, row.range_min, row.range_max)
            self.chartPoint.emit(row_index, t, y)

    def _on_heating_done(self, success, state):
        """Логирует фактический результат команды подогрева."""
        if success:
            logger.info("Подогрев {}: реле переключено", "включен" if state else "выключен")
        else:
            self.statusMessage.emit("Ошибка управления подогревом")
            logger.error("Не удалось переключить реле подогрева (состояние {})", state)
