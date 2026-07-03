# sheets.py - Подключение к Google Sheets

import os
import logging

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def get_sheet():
    """Возвращает worksheet с клиентами или None при ошибке подключения."""
    creds_file = config.GOOGLE_CREDENTIALS_FILE

    if not creds_file:
        logger.error("В .env не указан GOOGLE_CREDENTIALS_FILE")
        return None

    if not os.path.exists(creds_file):
        logger.error("Файл ключа не найден по пути: %s", creds_file)
        return None

    try:
        creds = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(config.SPREADSHEET_ID)
        sheet = spreadsheet.worksheet(config.WORKSHEET_NAME)
        logger.info("Успешно подключено к Google Sheets (лист «%s»)", config.WORKSHEET_NAME)
        return sheet
    except Exception:
        logger.exception("Ошибка подключения к Google Sheets")
        return None
