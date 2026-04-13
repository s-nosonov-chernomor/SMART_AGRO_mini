# config.py
import os
import sqlite3
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Определяем базовую директорию
basedir = os.path.abspath(os.path.dirname(__file__))

# Основные настройки проекта
DB_NAME = os.getenv("DB_NAME", "mini_150825.db")
DB_PATH = os.path.join(basedir, DB_NAME)

class Config:
    # Секретный ключ для Flask-приложения
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'you-will-never-guess'
    # URI для подключения к базе данных SQLite
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + DB_PATH
    SQLALCHEMY_TRACK_MODIFICATIONS = False

def get_db_connection():
    """Возвращает подключение к базе данных SQLite."""
    try:
        connection = sqlite3.connect(DB_PATH, timeout=30)
        connection.row_factory = sqlite3.Row  # Для доступа по именам столбцов
        return connection
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        raise
