import time
import numpy as np
from datetime import datetime
from app import db
from app_instance import app
from app.models import Parameter

# =========================
# МАТРИЦА ВЛИЯНИЯ (ВАША)
# =========================
A = np.array([
    [642, 1380, 1572, 1175],   # 405
    [672, 21545, 1519, 3933],  # 435
    [698, 5008, 1544, 1648],   # 470
    [523, 353, 1330, 5217],    # 505
    [838, 366, 1725, 8514],    # 545
    [913, 421, 2764, 10728],   # 580
    [920, 522, 52276, 8792],   # 620
    [11855, 654, 37840, 3822], # 670
], dtype=float)

# =========================
# СПИСОК ПАРАМЕТРОВ
# =========================

PWM_NAMES = [
    "PWM_FR",
    "PWM_BLUE",
    "PWM_RED",
    "PWM_WHITE"
]

RECIPE_NAMES = [
    "SET_FR",
    "SET_BLUE",
    "SET_RED",
    "SET_WHITE"
]

SENSOR_NAMES = [
    "S_405",
    "S_435",
    "S_470",
    "S_505",
    "S_545",
    "S_580",
    "S_620",
    "S_670",
]

# =========================
# УТИЛИТЫ
# =========================

def get_param(name):
    return db.session.query(Parameter).filter_by(
        controlled_parameter_name=name
    ).first()

def get_value(name):
    p = get_param(name)
    return float(p.value or 0) if p else 0

def set_value(name, val):
    p = get_param(name)
    if p:
        p.value = str(round(val, 2))
        p.value_date = datetime.now()
    db.session.commit()

# =========================
# ОСНОВНАЯ ЛОГИКА
# =========================

def read_pwm():
    return np.array([get_value(n) / 100 for n in PWM_NAMES])

def read_recipe():
    return np.array([get_value(n) / 100 for n in RECIPE_NAMES])

def read_sensor():
    return np.array([get_value(n) for n in SENSOR_NAMES])

def compute_target(recipe):
    return A @ recipe

def solve_pwm(y_need):
    # least squares с ограничениями
    u, *_ = np.linalg.lstsq(A, y_need, rcond=None)
    u = np.clip(u, 0, 1)
    return u

def step():
    pwm_now = read_pwm()
    recipe = read_recipe()
    y_now = read_sensor()

    # целевой спектр
    y_target = compute_target(recipe)

    # оценка внешнего света
    y_ext = y_now - (A @ pwm_now)

    # сколько нужно добрать
    y_need = y_target - y_ext
    y_need = np.maximum(y_need, 0)

    # решение
    pwm_new = solve_pwm(y_need)

    # сглаживание (очень важно!)
    # 1. сначала сглаживание
    pwm_smooth = pwm_now * 0.8 + pwm_new * 0.2

    # 2. потом ограничение скорости
    delta = pwm_smooth - pwm_now
    delta = np.clip(delta, -0.05, 0.05)

    pwm_final = pwm_now + delta

    # запись
    for i, name in enumerate(PWM_NAMES):
        set_value(name, pwm_final[i] * 100)

# =========================
# MAIN LOOP
# =========================

def run():
    with app.app_context():
        print("Light control started")
        while True:
            try:
                step()
            except Exception as e:
                print("Error:", e)
                db.session.rollback()
            time.sleep(1)


if __name__ == "__main__":
    run()