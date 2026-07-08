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

IDEAS_HEADERS = ["дата", "автор", "текст"]


def _open_spreadsheet():
    """Открывает саму Google-таблицу (без выбора листа) или None при ошибке."""
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
        return client.open_by_key(config.SPREADSHEET_ID)
    except Exception:
        logger.exception("Ошибка подключения к Google Sheets")
        return None


def get_sheet():
    """Возвращает worksheet с клиентами или None при ошибке подключения."""
    spreadsheet = _open_spreadsheet()
    if not spreadsheet:
        return None
    try:
        sheet = spreadsheet.worksheet(config.WORKSHEET_NAME)
        logger.info("Успешно подключено к Google Sheets (лист «%s»)", config.WORKSHEET_NAME)
        return sheet
    except Exception:
        logger.exception("Ошибка подключения к листу «%s»", config.WORKSHEET_NAME)
        return None


def get_ideas_sheet():
    """Возвращает лист «Идеи» (журнал предложений по улучшению бота).

    Если такого листа ещё нет в таблице — создаёт его с заголовками.
    """
    spreadsheet = _open_spreadsheet()
    if not spreadsheet:
        return None
    try:
        try:
            sheet = spreadsheet.worksheet(config.IDEAS_WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(
                title=config.IDEAS_WORKSHEET_NAME, rows=500, cols=len(IDEAS_HEADERS)
            )
            sheet.append_row(IDEAS_HEADERS, value_input_option="USER_ENTERED")
            logger.info("Создан лист «%s» для журнала идей", config.IDEAS_WORKSHEET_NAME)
        return sheet
    except Exception:
        logger.exception("Ошибка подключения к листу «%s»", config.IDEAS_WORKSHEET_NAME)
        return None
