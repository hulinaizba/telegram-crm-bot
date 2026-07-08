# ideas.py - Журнал идей и предложений операторов по улучшению бота

import asyncio
import logging
import time
from datetime import datetime

from sheets import get_ideas_sheet

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0


async def add_idea(author: str, text: str) -> bool:
    """Добавляет идею в лист «Идеи». Возвращает False, если сохранить не удалось."""
    return await asyncio.to_thread(_add_idea_with_retries, author, text)


def _add_idea_with_retries(author: str, text: str) -> bool:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return _add_idea(author, text)
        except Exception as e:
            logger.warning(
                "Попытка %d/%d записи идеи не удалась: %s", attempt, RETRY_ATTEMPTS, e
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BASE_DELAY * attempt)
    # Идея не должна потеряться бесследно, даже если таблица недоступна —
    # оставляем её в логе, откуда можно восстановить вручную.
    logger.error("Идея НЕ сохранена в таблицу (сбой API), текст: [%s] %s", author, text)
    return False


def _add_idea(author: str, text: str) -> bool:
    sheet = get_ideas_sheet()
    if not sheet:
        return False
    row = [datetime.now().strftime("%Y-%m-%d %H:%M"), author, text]
    sheet.append_row(row, value_input_option="USER_ENTERED")
    logger.info("Идея сохранена: [%s] %s", author, text)
    return True


async def get_recent_ideas(limit: int = 10):
    """Возвращает последние `limit` идей (список строк [дата, автор, текст]) или None при сбое."""
    return await asyncio.to_thread(_get_recent_ideas, limit)


def _get_recent_ideas(limit: int):
    sheet = get_ideas_sheet()
    if not sheet:
        return None
    try:
        values = sheet.get_all_values()
    except Exception:
        logger.exception("Не удалось прочитать лист «Идеи»")
        return None
    rows = [row for row in values[1:] if row and row[0]]  # без заголовка и пустых строк
    return rows[-limit:]
