
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


def create_light_params():
    names = [
        # рецепты
        "SET_FR", "SET_BLUE", "SET_RED", "SET_WHITE",

        # выходы
        "PWM_FR", "PWM_BLUE", "PWM_RED", "PWM_WHITE",

        # сенсоры
        "S_405", "S_435", "S_470", "S_505",
        "S_545", "S_580", "S_620", "S_670",
    ]

    for name in names:
        if not db.session.query(Parameter).filter_by(controlled_parameter_name=name).first():
            p = Parameter(
                controlled_parameter_name=name,
                value="0",
                parameter_type="float",
                operation_type="чтение / запись"
            )
            db.session.add(p)

    db.session.commit()