# sync_module.py main mini

import os
import time
import logging
from datetime import datetime, date
from dotenv import load_dotenv
import minimalmodbus
import requests
from sqlalchemy import and_, func
import threading
import queue
import time as _time

from app import db
from app_instance import app
from app.models import Parameter, Scenario, MixingParameter, Log, DensityRecord

# ─────────────────────────────────────────────────────────────────────────────
#                           Загрузка конфигурации
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

DEBUG_MODE             = os.getenv("DEBUG_MODE", "False").lower() == "true"
DEBUG_COM_PORT         = os.getenv("DEBUG_COM_PORT", "COM4")
DEBUG_BAUDRATE         = int(os.getenv("DEBUG_BAUDRATE", "115200"))
DEBUG_TIMEOUT_COM      = float(os.getenv("DEBUG_TIMEOUT_COM", "0.1"))
DEBUG_HANDLE_ECHO      = os.getenv("DEBUG_HANDLE_ECHO", "True").lower() == "true"

TELEGRAM_TOKEN         = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID       = os.getenv("TELEGRAM_CHAT_ID")

CRITICAL_ALERT_INTERVAL= int(os.getenv("CRITICAL_ALERT_INTERVAL", "60"))  # в минутах
OFFLINE_ALERT_THRESHOLDS = [5, 10, 30, 60]  # в минутах

PH_LOW, PH_HIGH        = 5.5, 7.5
EC_LOW, EC_HIGH        = 1.5, 2.5

# Интервал пакетной отправки (сек)
TELEGRAM_BATCH_INTERVAL = int(os.getenv("TELEGRAM_BATCH_INTERVAL", "10"))

WATER_FLOW_THRESHOLD = float(os.getenv("WATER_FLOW_THRESHOLD", "10"))  # л/м

# ─────────────────────────────────────────────────────────────────────────────
#                             Логирование & Сессия
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
session = db.session

# ─────────────────────────────────────────────────────────────────────────────
#                         Глобальные структуры
# ─────────────────────────────────────────────────────────────────────────────
# Очередь для сообщений в Telegram
telegram_queue = queue.Queue()

modbus_clients         = {}   # name -> minimalmodbus.Instrument
offline_counters       = {}   # name -> {"start": datetime, "sent": set()}
low_level_notifications= {}   # param.id -> last_sent_datetime
last_critical_alerts   = {"PH": None, "EC": None}
feed_started_flags     = set()# set of timer_name strings
last_device_values     = {}   # param.id -> last known device_val
previous_device_values = {}
previous_db_values     = {}
first_run = True
last_critical_links    = {}
_prev_water_flow     = None
_prev_total_volume   = None

mix_state = {
    "mix_start":   None,
    "stabilized":  False,
    "prev_ec":     None,
    "prev_ph":     None,
    "expected_ec": None,
    "expected_ph": None,
}

# Словарь связанных критических условий:
# формат: (параметр1, должен_быть_включен1, параметр2, должен_быть_включен2)
CRITICAL_LINKS = [
    ("Насос", True, "Уровень 1 минимум", False),
    ("Подача A в бак", True, "Уровень А", False),
    ("Подача В в бак", True, "Уровень В", False),
    ("Подача кислоты в бак", True, "Уровень К", False),
]

STIRRER_PARAM       = "Перемешивание"
FEED_RELAYS         = [
    "Подача A в бак",
    "Подача В в бак",
    "Подача кислоты в бак",
]

# ─────────────────────────────────────────────────────────────────────────────
#                          Вспомогательные функции DB
# ─────────────────────────────────────────────────────────────────────────────
def _manage_stirrer(pump_on: bool):
    """
    Если растворный узел работает (pump_on=True):
      • когда ни одно из FEED_RELAYS не активно → включаем STIRRER_PARAM
      • когда хотя бы одно активно     → отключаем STIRRER_PARAM
    Если растворный узел остановлен (pump_on=False):
      • в этом сбросе (внутри handle_feed_timers) тоже отключаем STIRRER_PARAM
    """
    try:
        # проверяем, какие реле сейчас в БД включены
        any_running = any(
            float(get_parameter_value(relay) or 0) > 0
            for relay in FEED_RELAYS
        )
        if pump_on:
            # внутри работы растворного узла
            new_val = "0" if any_running else "1"
            update_parameter_value(STIRRER_PARAM, new_val)
        else:
            # в момент полного сброса
            update_parameter_value(STIRRER_PARAM, "0")
    except Exception:
        # в случае ошибки логируем, но не падаем
        logger.exception("Ошибка управления мешалкой")

def monitor_water_flow(current_flow: float):
    """
    Отслеживает параметр «Расход чистой воды» (л/м):
      • при переходе через WATER_FLOW_THRESHOLD вверх — лог «Зафиксирован расход Xл/м»
      • при падении ниже порога — лог «Расход воды прекратился»
    """
    global _prev_water_flow
    if _prev_water_flow is None:
        _prev_water_flow = current_flow
        return

    # вверх через порог
    if _prev_water_flow <= WATER_FLOW_THRESHOLD < current_flow:
        insert_log_message(f"Зафиксирован расход воды {current_flow:.1f}л/м", "INFO")
    # вниз через порог
    elif _prev_water_flow > WATER_FLOW_THRESHOLD >= current_flow:
        insert_log_message("Расход воды прекратился", "INFO")

    _prev_water_flow = current_flow

def monitor_total_volume(current_volume: float):
    """
    Отслеживает параметр «Объем чистой воды» (л):
      • при увеличении — лог «Вылито +Δ л воды. Всего X л.»
    """
    global _prev_total_volume
    if _prev_total_volume is None:
        _prev_total_volume = current_volume
        return

    delta = current_volume - _prev_total_volume
    if delta > 0:
        # округлим до целых литров
        d_int = int(round(delta))
        insert_log_message(f"Вылито +{d_int} л воды. Всего {current_volume:.1f} л.", "INFO")

    _prev_total_volume = current_volume

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=data)
        if r.status_code != 200:
            logger.error(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        logger.error(f"Telegram exception: {e}")

def insert_log_message(message: str, level: str="INFO"):
    """
    Записывает лог в БД и ставит в очередь для пакетной отправки.
    """
    try:
        entry = Log(message=message, level=level, timestamp=datetime.now())
        session.add(entry)
        session.commit()
        # Ограничиваем размер БД-логов
        total = session.query(Log).count()
        if total > 1000:
            for old in session.query(Log).order_by(Log.timestamp).limit(total-1000):
                session.delete(old)
            session.commit()
        # Кладём в очередь только текст для Телеграм
        telegram_queue.put((level, message))
    except Exception as e:
        logger.error(f"Ошибка логирования: {e}")
        session.rollback()

def get_parameter_value(name: str) -> str:
    """Возвращает Parameter.value по имени."""
    p = session.query(Parameter).filter_by(controlled_parameter_name=name).first()
    if not p:
        raise ValueError(f"Параметр '{name}' не найден")
    return p.value

def update_parameter_value(name: str, value: str):
    """Обновляет Parameter.value и .value_date."""
    p = session.query(Parameter).filter_by(controlled_parameter_name=name).first()
    if not p:
        raise ValueError(f"Параметр '{name}' не найден")
    p.value = value
    p.value_date = datetime.now()
    session.commit()

def get_mixing_parameters() -> MixingParameter:
    """Возвращает первую запись MixingParameter."""
    mp = session.query(MixingParameter).first()
    if not mp:
        raise ValueError("MixingParameter не найден")
    return mp

def insert_density_record(name: str, value: float):
    """Запись в таблицу DensityRecord; держит до 150 записей."""
    dr = DensityRecord(density_name=name, value=value, timestamp=datetime.now())
    session.add(dr)
    session.commit()
    count = session.query(DensityRecord).count()
    if count > 150:
        old = session.query(DensityRecord).order_by(DensityRecord.timestamp).limit(count-150).all()
        for r in old:
            session.delete(r)
        session.commit()

def insert_buffer_capacity_record(bf_value: float):
    """Запись буферности как DensityRecord."""
    dr = DensityRecord(density_name="buffer_capacity", value=bf_value, timestamp=datetime.now())
    session.add(dr)
    session.commit()

def format_value(param: Parameter, raw_val: str) -> str:
    """
    Форматирует raw_val по parameter_type и имени параметра.
    """
    v = float(raw_val)
    t = param.parameter_type.lower()
    name = param.controlled_parameter_name
    if t == "bool":
        return "включен" if v != 0 else "отключен"
    if name in ("Уровень PH", "Уровень EC"):
        return f"{v:.1f}"
    return str(int(round(v)))

def _telegram_dispatcher():
    """
    Каждые TELEGRAM_BATCH_INTERVAL секунд забираем всю очередь
    и отсылаем единым запросом.
    """
    while True:
        _time.sleep(TELEGRAM_BATCH_INTERVAL)

        batch = []
        # стягиваем всё из очереди
        while True:
            try:
                level, msg = telegram_queue.get_nowait()
                batch.append((level, msg))
            except queue.Empty:
                break

        if not batch:
            continue

        text = "\n".join(f"[{lvl}] {m}" for lvl, m in batch)
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML"
                },
                timeout=5
            )
            if resp.status_code != 200:
                logger.error(f"Telegram batch error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Telegram batch exception: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#                           Вспомогательные Modbus
# ─────────────────────────────────────────────────────────────────────────────
def setup_modbus_client(param: Parameter) -> minimalmodbus.Instrument:
    mode = param.mode
    addr = int(param.network_address)
    if mode == "com":
        port    = DEBUG_COM_PORT if DEBUG_MODE else str(param.com)
        baud    = DEBUG_BAUDRATE if DEBUG_MODE else int(param.speed)
        to      = DEBUG_TIMEOUT_COM if DEBUG_MODE else float(getattr(param, "timeout", 1.0))
        inst    = minimalmodbus.Instrument(port, addr, mode=minimalmodbus.MODE_RTU)
        inst.serial.baudrate   = baud
        inst.serial.timeout    = to
        inst.serial.bytesize   = 8
        inst.serial.parity     = minimalmodbus.serial.PARITY_NONE
        inst.serial.stopbits   = 1
        inst.handle_local_echo = DEBUG_HANDLE_ECHO
    elif mode == "tcp":
        host = param.ip_address
        inst = minimalmodbus.Instrument(host, addr, mode=minimalmodbus.MODE_TCP)
        inst.serial.timeout = 1
    else:
        raise ValueError(f"Unknown mode {mode}")
    return inst

def read_registers_batch(group: dict):
    name  = group["device_name"]
    regs  = group["parameters"]
    start = group["start_address"]
    cnt   = group["count"]
    rtype = group["register_type"]

    if name not in modbus_clients:
        try:
            modbus_clients[name] = setup_modbus_client(regs[0])
        except Exception:
            update_offline_counter(name)
            return None
    inst = modbus_clients[name]
    try:
        if   rtype=="1": vals = inst.read_bits(start, cnt, functioncode=1)
        elif rtype=="2": vals = inst.read_bits(start, cnt, functioncode=2)
        elif rtype=="3": vals = inst.read_registers(start, cnt, functioncode=3)
        elif rtype=="4": vals = inst.read_registers(start, cnt, functioncode=4)
        else:            return None
        reset_offline_counter(name)
        out = []
        for i,p in enumerate(regs):
            K = float(p.K) if p.K else 1.0
            out.append(vals[i]/K)
        return out
    except Exception as e:
        logger.error(f"Modbus read error {name}: {e}")
        modbus_clients.pop(name, None)
        update_offline_counter(name)
        return None

def write_parameter_value(param: Parameter, value: float) -> bool:
    name = f"{param.device_type}_addr{param.network_address}"
    if name not in modbus_clients:
        try:
            modbus_clients[name] = setup_modbus_client(param)
        except Exception:
            update_offline_counter(name)
            return False
    inst = modbus_clients[name]
    try:
        addr = int(param.register_number)
        K    = float(param.K) if param.K else 1.0
        val  = int(value * K)
        if param.register_type=="1":
            inst.write_bit(addr, val, functioncode=5)
        elif param.register_type=="3":
            inst.write_register(addr, val, functioncode=6)
        else:
            return False
        reset_offline_counter(name)
        return True
    except Exception as e:
        logger.error(f"Modbus write error {param.controlled_parameter_name}: {e}")
        update_offline_counter(name)
        return False

def group_parameters(params):
    grouped = []
    sorted_p = sorted(params, key=lambda p: (
        p.device_type, p.network_address, p.mode,
        p.com if p.mode=="com" else p.ip_address,
        p.register_type, int(p.register_number)
    ))
    grp = {}
    prev= None
    for p in sorted_p:
        name = f"{p.device_type}_addr{p.network_address}"
        chan = p.mode
        comm = p.com if chan=="com" else p.ip_address
        rtype= p.register_type
        addr = int(p.register_number)

        if (not prev or
            name!=grp.get("device_name") or
            chan!=grp.get("mode") or
            comm!=grp.get("comm") or
            rtype!=grp.get("register_type") or
            addr!=grp.get("start_address",0)+grp.get("count",0)
        ):
            if prev:
                grouped.append(grp)
            grp = {
                "device_name": name,
                "mode":        chan,
                "comm":        comm,
                "register_type":rtype,
                "start_address":addr,
                "count":         1,
                "parameters":    [p]
            }
        else:
            grp["count"] += 1
            grp["parameters"].append(p)
        prev = p
    if grp:
        grouped.append(grp)
    return grouped

# ─────────────────────────────────────────────────────────────────────────────
#                         Сценарии (execute_scenarios)
# ─────────────────────────────────────────────────────────────────────────────
def execute_scenarios():
    today = date.today()
    now_t = datetime.now().time()
    try:
        scs = session.query(Scenario).filter(
            and_(
                Scenario.time <= now_t,
                (Scenario.last_execution==None) |
                (func.date(Scenario.last_execution) < today)
            )
        ).order_by(Scenario.time, Scenario.id).all()

        for sc in scs:
            p = session.query(Parameter).filter_by(controlled_parameter_name=sc.parameter).first()
            if not p:
                logger.error(f"Сценарий: {sc.parameter} not found")
                continue
            p.value = sc.value
            p.value_date = datetime.now().replace(second=0,microsecond=0)
            session.commit()

            # Получаем объекты PH и EC для форматирования
            p_ph = session.query(Parameter).filter_by(controlled_parameter_name="Уровень PH").first()
            p_ec = session.query(Parameter).filter_by(controlled_parameter_name="Уровень EC").first()

            # Форматируем значения
            ph_fmt = format_value(p_ph, p_ph.value)
            ec_fmt = format_value(p_ec, p_ec.value)
            val_fmt = format_value(p, p.value)

            # Формируем сообщение
            msg = (
                f"Сценарий: {p.controlled_parameter_name} → {val_fmt}. "
                f"PH={ph_fmt}, EC={ec_fmt}"
            )

            # Обновляем результат сценария и время исполнения
            sc.result = f"Записано {val_fmt}"
            sc.last_execution = datetime.now()
            session.commit()

            insert_log_message(msg, "INFO")

    except Exception as e:
        logger.error(f"Scenario error: {e}")
        session.rollback()

# ─────────────────────────────────────────────────────────────────────────────
#                           Off-line уведомления
# ─────────────────────────────────────────────────────────────────────────────
def update_offline_counter(dev):
    now = datetime.now()
    ctr = offline_counters.get(dev)
    if not ctr:
        offline_counters[dev] = {"start": now, "sent": set()}

def reset_offline_counter(dev):
    offline_counters.pop(dev, None)

def check_offline_alarms():
    now = datetime.now()
    for dev, d in list(offline_counters.items()):
        mins = (now - d["start"]).total_seconds()/60
        for th in OFFLINE_ALERT_THRESHOLDS:
            if mins>=th and th not in d["sent"]:
                insert_log_message(f"Нет связи с устройством {dev} уже {int(mins)} мин.", "ERROR")
                d["sent"].add(th)

# ─────────────────────────────────────────────────────────────────────────────
#                      Управление подачей (handle_feed_timers)
# ─────────────────────────────────────────────────────────────────────────────
def handle_feed_timers(params_dict: dict):
    """
    Управление таймерами подачи и реле по фазам:
      idle → stabilizing → regulating → countdown → post_stabilization → regulating → …
    """
    now = datetime.now()
    mp  = get_mixing_parameters()
    mode = os.getenv("PUMP_ACTIVATION_MODE", "вместе").lower()

    # Считываем флаг перемешивания
    try:
        pump_on = float(get_parameter_value("Растворный узел") or 0) > 0
    except:
        pump_on = False

    # 2) Если мешалка должна управляться автоматом — только в режиме работы растворного узла
    # if pump_on:
    #     _manage_stirrer(True)

    # Если мешалка выключена — сбрасываем всё и уходим в idle
    if not pump_on:
        if mix_state.get("phase") != "idle":
            # Сбрасываем таймеры и реле
            for _, _, _, timer_name, relay_name in [
                ("A", "expected_ec", insert_density_record, "Время подачи A в бак", "Подача А в бак"),
                ("В", "expected_ec", insert_density_record, "Время подачи В в бак", "Подача В в бак"),
                ("кислоты", "expected_ph", insert_buffer_capacity_record, "Время подачи кислоты в бак", "Подача кислоты в бак"),
            ]:
                update_parameter_value(timer_name, "0")
                update_parameter_value(relay_name, "0")

            # сброс мешалки
            # update_parameter_value(STIRRER_PARAM, "0")
            insert_log_message("Растворный узел выключен — сброс фаз и таймеров", "INFO")
        mix_state.clear()
        mix_state["phase"] = "idle"
        return

    phase = mix_state.get("phase", "idle")

    # 1) Перешли от idle к stabilizing
    if phase == "idle":
        mix_state["mix_start"]  = now
        mix_state["phase"]      = "stabilizing"
        insert_log_message(f"Растворный узел включен — ждем {mp.stabilization_time}s стабилизации", "INFO")
        return

    # 2) Ожидание стабилизации
    if phase == "stabilizing":
        elapsed = (now - mix_state["mix_start"]).total_seconds()
        if elapsed >= mp.stabilization_time:
            mix_state["phase"]      = "regulating"
            mix_state["prev_ec"]    = float(get_parameter_value("Уровень EC") or 0)
            mix_state["prev_ph"]    = float(get_parameter_value("Уровень PH") or 0)
            insert_log_message("Стабилизация завершена — начинаем цикл регулирования", "INFO")
        return

    # 3) Расчет таймеров и отправка стартового отчета
    if phase == "regulating":
        # Сразу берём самые свежие значения из БД:
        curr_ec = float(get_parameter_value("Уровень EC") or 0)
        curr_ph = float(get_parameter_value("Уровень PH") or 0)

        delta_ec = mp.target_ec - curr_ec
        delta_ph = curr_ph - mp.target_ph

        # EC
        if delta_ec > mp.ec_deviation:
            vol_ec     = round(1000 * delta_ec * mp.tank_volume / (mp.density_a + mp.density_b), 0)
            rate_ec    = mp.pump_flow_rate * 2 / 60000  # л/сек
            t_full_ec  = round(vol_ec / (rate_ec * 1000), 0)
            t_work_ec  = min(t_full_ec, mp.maxtime)
            units_ec   = int(t_work_ec * 10)
            update_parameter_value("Время подачи A в бак", str(units_ec))
            update_parameter_value("Время подачи В в бак", str(units_ec))
            mix_state["expected_ec"] = (delta_ec * t_work_ec / t_full_ec) if t_full_ec else 0
            mix_state["prev_ec"] = float(get_parameter_value("Уровень EC") or 0)
        else:
            vol_ec = t_full_ec = t_work_ec = units_ec = 0
            mix_state["expected_ec"] = 0

        # pH
        if delta_ph > mp.ph_deviation:
            vol_ph     = round(delta_ph * mp.bf * mp.tank_volume / 1000, 0)
            rate_ph    = mp.pump_flow_rate / 60000
            t_full_ph  = round(vol_ph / (rate_ph * 1000) , 0)
            t_work_ph  = min(t_full_ph, mp.maxtime)
            units_ph   = int(t_work_ph * 10)
            update_parameter_value("Время подачи кислоты в бак", str(units_ph))
            mix_state["expected_ph"] = (delta_ph * t_work_ph / t_full_ph) if t_full_ph else 0
            mix_state["prev_ph"] = float(get_parameter_value("Уровень PH") or 0)
        else:
            vol_ph = t_full_ph = t_work_ph = units_ph = 0
            mix_state["expected_ph"] = 0

        # Если оба = 0 → сразу выходим (без детальной сводки)
        if mix_state["expected_ec"] == 0 and mix_state["expected_ph"] == 0:
            insert_log_message("Регулирование EC и pH не потребовалось", "INFO")
            mix_state["phase"] = "idle"
            return

        # Иначе формируем «Начало цикла регулирования» по каждому параметру отдельно:
        lines = ["Начало цикла регулирования."]

        # 1) EC
        if mix_state["expected_ec"] == 0:
            lines.append(
                f"Требуемый EC = {mp.target_ec:.1f}, текущий EC = {curr_ec:.1f} — регулирование по EC не требуется.")
        else:
            # vol_ec, t_full_ec, t_work_ec уже вычислены выше
            lines.append(
                f"Требуемый EC = {mp.target_ec:.1f}, текущий EC = {curr_ec:.1f}, "
                f"нужно {vol_ec:.0f} мл ({t_full_ec}s работы насоса)."
            )
            lines.append(
                f"С учётом ограничений включено A и B на {t_work_ec}s. "
                f"Ожидаемое изменение EC на {mix_state['expected_ec']:.3f} до уровня EC={curr_ec + mix_state['expected_ec']:.3f}."
            )

        # 2) pH
        if mix_state["expected_ph"] == 0:
            lines.append(
                f"Требуемый pH = {mp.target_ph:.1f}, текущий pH = {curr_ph:.1f} — регулирование по pH не требуется.")
        else:
            # vol_ph, t_full_ph, t_work_ph уже вычислены выше
            lines.append(
                f"Требуемый pH = {mp.target_ph:.1f}, текущий pH = {curr_ph:.1f}, "
                f"нужно {vol_ph:.1f} мл кислоты ({t_full_ph}s работы насоса)."
            )
            lines.append(
                f"С учётом ограничений включена кислота на {t_work_ph}s. "
                f"Ожидаемое изменение pH на {mix_state['expected_ph']:.3f} до уровня pH={curr_ph - mix_state['expected_ph']:.3f}."
            )

        insert_log_message("\n".join(lines), "INFO")
        mix_state["phase"] = "countdown"
        return

    # 4) Обратный отсчёт и включение реле (только console-лог)
    def process_pump(comp, exp_key, record_fn, timer_name, relay_name):
        raw = get_parameter_value(timer_name) or "0"
        rem = int(float(raw))
        if rem > 0 and timer_name not in feed_started_flags:
            logger.info(f"[feed] Запуск {relay_name}, остаток {rem*0.1:.1f}s")
            update_parameter_value(relay_name, "1")
            feed_started_flags.add(timer_name)
        if rem > 0:
            new = max(0, rem - 10)
            update_parameter_value(timer_name, str(new))
        else:
            new = 0
        if new == 0 and timer_name in feed_started_flags:
            logger.info(f"[feed] Окончание {relay_name}")
            update_parameter_value(relay_name, "0")
            feed_started_flags.remove(timer_name)
        return new

    sequence = [
        ("A",       "expected_ec", insert_density_record,        "Время подачи A в бак",         "Подача A в бак"),
        ("В",       "expected_ec", insert_density_record,        "Время подачи В в бак",         "Подача В в бак"),
        ("кислоты", "expected_ph", insert_buffer_capacity_record,"Время подачи кислоты в бак", "Подача кислоты в бак"),
    ]

    if mode == "вместе":
        for args in sequence:
            process_pump(*args)
        all_zero = all(
            int(float(get_parameter_value(tn) or "0")) == 0
            for *_, tn, __ in sequence
        )
        if phase == "countdown" and all_zero:
            mix_state["phase"]     = "post_stabilization"
            mix_state["mix_start"] = now
            insert_log_message("Подачи завершены — ждем стабилизации перед завершением цикла", "INFO")
    else:  # по очереди
        any_active = False
        for args in sequence:
            if process_pump(*args) > 0:
                any_active = True
                break
        if phase == "countdown" and not any_active:
            mix_state["phase"]     = "post_stabilization"
            mix_state["mix_start"] = now
            insert_log_message("Подачи по очереди завершены — ждем стабилизации перед завершением цикла", "INFO")

    # 5) Пост-стабилизация и итоговый отчет
    if mix_state.get("phase") == "post_stabilization":
        elapsed2 = (now - mix_state["mix_start"]).total_seconds()
        if elapsed2 >= mp.stabilization_time:
            lines = ["По завершении цикла регулирования получили:"]

            # 1) EC
            if mix_state["expected_ec"] == 0:
                lines.append("Регулирование по EC не выполнялось.")
            else:
                curr_ec = float(get_parameter_value("Уровень EC") or 0)
                actual_ec_change = curr_ec - mix_state["prev_ec"]
                eff_ec = (actual_ec_change / mix_state["expected_ec"] * 100) if mix_state["expected_ec"] else 0
                lines.append(
                    f"Ожидаемое изменение EC = {mix_state['expected_ec']:.3f}, "
                    f"факт = {actual_ec_change:.3f}, эффективность = {eff_ec:.0f}%"
                )

            # 2) pH 
            if mix_state["expected_ph"] == 0:
                lines.append("Регулирование по pH не выполнялось.")
            else:
                curr_ph = float(get_parameter_value("Уровень PH") or 0)
                actual_ph_change = mix_state["prev_ph"] - curr_ph
                eff_ph = (actual_ph_change / mix_state["expected_ph"] * 100) if mix_state["expected_ph"] else 0
                lines.append(
                    f"Ожидаемое изменение pH = {mix_state['expected_ph']:.3f}, "
                    f"факт = {actual_ph_change:.3f}, эффективность = {eff_ph:.0f}%"
                )

            insert_log_message("\n".join(lines), "INFO")

            # готовимся к новому циклу регулирования (возврат в регуляцию)
            mix_state["phase"] = "regulating"
            mix_state["mix_start"] = None


# ─────────────────────────────────────────────────────────────────────────────
#                        Основной опрос параметров
# ─────────────────────────────────────────────────────────────────────────────

def poll_parameters():
    global first_run, previous_device_values, previous_db_values, last_critical_links

    # 1) Загружаем все параметры
    all_params = session.query(Parameter).all()

    # Функция для безопасного преобразования bytes в str
    def to_str(value):
        if isinstance(value, bytes):
            return value.decode('utf-8')  # Используйте 'cp1251' если UTF-8 не работает
        return value

    monitored_params = {
        to_str(p.controlled_parameter_name)
        for p in all_params
        if to_str(p.operation_type).lower() == "чтение / запись"
    }

    low_level_params = {
        to_str(p.controlled_parameter_name)
        for p in all_params
        if "минимум" in to_str(p.controlled_parameter_name).lower()
    }

    # Создаем словарь с ДЕКОДИРОВАННЫМИ ключами
    params_dict = {to_str(p.controlled_parameter_name): p for p in all_params}

    ph = float(params_dict["Уровень PH"].value or 0)
    ec = float(params_dict["Уровень EC"].value or 0)
    now = datetime.now()

    # 2) Критические одиночные PH и EC
    if not first_run:
        # PH
        if ph < PH_LOW or ph > PH_HIGH:
            last = last_critical_alerts["PH"]
            if not last or (now - last).total_seconds() >= CRITICAL_ALERT_INTERVAL * 60:
                insert_log_message(
                    f"Критический PH = {ph:.1f} (мин {PH_LOW}, макс {PH_HIGH})",
                    level="ERROR"
                )
                last_critical_alerts["PH"] = now
        # EC
        if ec < EC_LOW or ec > EC_HIGH:
            last = last_critical_alerts["EC"]
            if not last or (now - last).total_seconds() >= CRITICAL_ALERT_INTERVAL * 60:
                insert_log_message(
                    f"Критический EC = {ec:.1f} (мин {EC_LOW}, макс {EC_HIGH})",
                    level="ERROR"
                )
                last_critical_alerts["EC"] = now

    # 3) Критические связки параметров
    for idx, (p1_name, must_on1, p2_name, must_on2) in enumerate(CRITICAL_LINKS):
        p1 = params_dict.get(p1_name)
        p2 = params_dict.get(p2_name)
        if not p1 or not p2:
            continue

        # Проверяем текущие состояния как булевы
        val1 = (float(p1.value or 0) != 0)
        val2 = (float(p2.value or 0) != 0)
        if val1 == must_on1 and val2 == must_on2:
            last = last_critical_links.get(idx)
            if not last or (now - last).total_seconds() >= CRITICAL_ALERT_INTERVAL * 60:
                state1 = format_value(p1, p1.value)
                state2 = format_value(p2, p2.value)
                insert_log_message(
                    f"Критическое событие: {p1_name} — {state1}, {p2_name} — {state2}",
                    level="ERROR"
                )
                last_critical_links[idx] = now

    # 4) Опрос Modbus и синхронизация
    grp_params = [p for p in all_params if p.mode in ("com", "tcp")]
    for grp in group_parameters(grp_params):
        vals = read_registers_batch(grp)
        if vals is None:
            continue

        for i, p in enumerate(grp["parameters"]):
            # Приводим к числам
            dev_raw_f  = float(vals[i])
            db_raw_f   = float(p.value or 0)
            prev_dev_f = previous_device_values.get(p.id)
            prev_db_f  = previous_db_values.get(p.id)

            # Отладочный вывод
            # logger.info(
            #     f"[sync-debug] {p.controlled_parameter_name}: "
            #     f"prev_dev={prev_dev_f}, prev_db={prev_db_f}, "
            #     f"dev_raw={dev_raw_f}, db_raw={db_raw_f}, first_run={first_run}"
            # )

            # ИНИЦИАЛИЗАЦИЯ И ПЕРВИЧНАЯ СИНХРОНИЗАЦИЯ
            if first_run:
                # если двунаправленный параметр и рассогласование — пишем в устройство
                if (
                        p.operation_type.lower() == "чтение / запись"
                        and p.controlled_parameter_name in monitored_params
                        and dev_raw_f != db_raw_f
                ):
                    write_parameter_value(p, db_raw_f)
                    insert_log_message(
                        f"{p.controlled_parameter_name}: первичная синхронизация WEB→Device → "
                        f"{format_value(p, str(db_raw_f))}",
                        level="INFO"
                    )
                # при любом operation_type — заполняем кэши
                previous_device_values[p.id] = db_raw_f  # после sync на устройстве теперь db_raw_f
                previous_db_values[p.id] = db_raw_f
                continue

            # ДЛЯ ВСЕХ ДРУГИХ ПРОГНОВ — как раньше
            # 1) только «чтение» — просто обновляем из устройства
            if p.operation_type.lower() == "чтение":
                p.value = str(dev_raw_f)
                p.value_date = now
                previous_device_values[p.id] = dev_raw_f
                previous_db_values[p.id] = dev_raw_f
                continue

            # 2) если не в monitored — тоже просто читаем
            if p.controlled_parameter_name not in monitored_params:
                p.value = str(dev_raw_f)
                p.value_date = now
                previous_device_values[p.id] = dev_raw_f
                previous_db_values[p.id] = dev_raw_f
                continue

            # 3) двунаправленная синхронизация
            # WEB → Device
            if db_raw_f != prev_db_f and dev_raw_f == prev_dev_f:
                write_parameter_value(p, db_raw_f)
                insert_log_message(
                    f"{p.controlled_parameter_name} изменено с WEB-интерфейса → "
                    f"{format_value(p, str(db_raw_f))}",
                    level="INFO"
                )
                previous_db_values[p.id]     = db_raw_f
                previous_device_values[p.id] = db_raw_f

            # Device → DB
            elif dev_raw_f != prev_dev_f and db_raw_f == prev_db_f:
                p.value = str(dev_raw_f)
                p.value_date = now
                session.commit()
                insert_log_message(
                    f"{p.controlled_parameter_name} изменено на устройстве → "
                    f"{format_value(p, str(dev_raw_f))}",
                    level="INFO"
                )
                previous_device_values[p.id] = dev_raw_f
                previous_db_values[p.id]     = dev_raw_f

            # Конфликт
            elif db_raw_f != prev_db_f and dev_raw_f != prev_dev_f:
                # insert_log_message(
                #     f"Конфликт {p.controlled_parameter_name}: "
                #     f"WEB={format_value(p, str(db_raw_f))}, "
                #     f"PLC={format_value(p, str(dev_raw_f))}",
                #     level="WARNING"
                # )
                write_parameter_value(p, db_raw_f)
                p.value = str(db_raw_f)
                p.value_date = now
                session.commit()
                previous_db_values[p.id]     = db_raw_f
                previous_device_values[p.id] = db_raw_f

            # иначе — ничего не меняем

    session.commit()

    # 5) Низкие уровни
    if not first_run:
        for name in low_level_params:
            p = params_dict.get(name)
            if p and p.value == "0":
                last = low_level_notifications.get(p.id)
                if not last or (now - last).total_seconds() >= 300:
                    insert_log_message(f"Низкий уровень {name}", level="WARNING")
                    low_level_notifications[p.id] = now
            else:
                low_level_notifications.pop(p.id, None)

    # ─────────────────────────────────────────────────────────
    # Мониторим новый параметр «Расход чистой воды» и «Объем чистой воды»
    try:
        flow_param = float(params_dict["Расход чистой воды"].value or 0)
        volume_param = float(params_dict["Объем чистой воды"].value or 0)
        monitor_water_flow(flow_param)
        monitor_total_volume(volume_param)
    except KeyError:
        # если параметров нет в БД — просто пропускаем
        pass

    # 6) Управление подачей
    handle_feed_timers(params_dict)

    first_run = False


# ─────────────────────────────────────────────────────────────────────────────
#                             Запуск сервиса
# ─────────────────────────────────────────────────────────────────────────────
# Запускаем фоновый поток для пакетной отправки Telegram–сообщений
# _dispatcher_thread = threading.Thread(target=_telegram_dispatcher, daemon=True)
# _dispatcher_thread.start()

def run_sync():
    with app.app_context():
        last_act = session.query(func.max(Log.timestamp)).scalar()
        last_str = last_act.strftime("%Y-%m-%d %H:%M:%S") if last_act else "—"
        now_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snap = "\n".join(f"{p.controlled_parameter_name} = {format_value(p, str(p.value or '0'))}" for p in session.query(Parameter).all())
        insert_log_message(f"Запуск синхронизации: {now_str}\nПоследняя активность: {last_str}\nСостояние:\n{snap}", "INFO")

        while True:
            try:
                # Проверяем режим эксплуатации
                mode_param = session.query(Parameter).filter_by(
                    controlled_parameter_name="Режим эксплуатации"
                ).first()
                mode = mode_param.value if mode_param else "0"

                if mode != "1":
                    execute_scenarios()
                # else:
                    # logger.info("Режим эксплуатации=1 → пропускаем execute_scenarios()")

                poll_parameters()
                check_offline_alarms()

            except Exception as e:
                logger.error(f"Ошибка в основном цикле: {e}", exc_info=True)
                session.rollback()
            time.sleep(1)

if __name__ == "__main__":
    run_sync()
