# config.py - Центральная конфигурация приложения

import os
import logging
from datetime import time as dt_time
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    """Читает целое число из .env, при мусоре возвращает default."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Список операторов с доступом к боту.
# Можно переопределить через .env: ALLOWED_USERS=5769727981,6492135923
_default_allowed = "5769727981,6492135923"
ALLOWED_USERS = [
    int(uid) for uid in os.getenv("ALLOWED_USERS", _default_allowed).split(",")
    if uid.strip().isdigit()
]

# --- Google Sheets ---
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Клиенты")

# Автоматическое перечитывание таблицы каждые N минут (0 — отключено)
RELOAD_INTERVAL_MINUTES = _int_env("RELOAD_INTERVAL_MINUTES", 10)

# --- Напоминания ---
# Время ежедневного напоминания операторам в формате ЧЧ:ММ (пусто — отключено)
REMINDER_TIME = os.getenv("REMINDER_TIME", "08:00")

# Часовой пояс, в котором понимается REMINDER_TIME (IANA-имя, например Asia/Jerusalem,
# Europe/Moscow). НЕ зависит от того, в каком часовом поясе живёт сам сервер —
# поэтому переезд бота на VPS в другой стране не сдвигает время напоминания.
REMINDER_TIMEZONE = os.getenv("REMINDER_TIMEZONE", "Asia/Jerusalem")

# --- Логирование ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "bot.log")
LOG_MAX_BYTES = _int_env("LOG_MAX_BYTES", 1_000_000)   # ~1 МБ на файл
LOG_BACKUP_COUNT = _int_env("LOG_BACKUP_COUNT", 3)     # bot.log.1 ... bot.log.3


def parse_reminder_time():
    """Возвращает время напоминания (datetime.time с поясом REMINDER_TIMEZONE) или None.

    Пояс берётся из REMINDER_TIMEZONE (IANA-имя), а не из системных настроек
    сервера, поэтому корректно учитывает переход на летнее/зимнее время и не
    зависит от того, где физически размещён сервер.
    """
    raw = (REMINDER_TIME or "").strip()
    if not raw:
        return None
    try:
        hours, minutes = raw.split(":")
        tz = ZoneInfo(REMINDER_TIMEZONE)
        return dt_time(int(hours), int(minutes), tzinfo=tz)
    except Exception:
        # Некорректный REMINDER_TIME или неизвестное имя пояса — отключаем
        # напоминание, а не падаем при старте бота.
        return None


def build_file_handler() -> RotatingFileHandler:
    """Файловый обработчик логов с ротацией: bot.log не растёт бесконечно."""
    return RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )


def setup_logging() -> None:
    """Настраивает логирование в консоль и файл (с ротацией)."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            build_file_handler(),
        ],
    )
    # Приглушаем шумные логи httpx (каждый запрос polling)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def validate_config() -> list:
    """Возвращает список ошибок конфигурации (пустой — всё в порядке)."""
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN не задан в .env")
    if not ALLOWED_USERS:
        errors.append("ALLOWED_USERS пуст — никто не получит доступ к боту")
    if not GOOGLE_CREDENTIALS_FILE:
        errors.append("GOOGLE_CREDENTIALS_FILE не задан в .env")
    if not SPREADSHEET_ID:
        errors.append("SPREADSHEET_ID не задан в .env")
    return errors
