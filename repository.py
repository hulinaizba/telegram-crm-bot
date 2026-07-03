# repository.py - Слой данных: кэш клиентов в памяти + синхронизация с Google Sheets

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta

from sheets import get_sheet
from stages import STAGES, get_stage_index

logger = logging.getLogger(__name__)

# Соответствие полей карточки клиента заголовкам столбцов таблицы
FIELD_COLUMNS = {
    "name": "имя",
    "experience": "опыт",
    "terminal": "терминал",
    "deposit": "депозит",
    "format": "формат_торговли",
    "stage": "текущий_этап",
    "notes": "заметки",
    "created_date": "дата_старта",
    "last_contact": "последний_контакт",
    "status": "статус",
    "chat_id": "chat_id",
}

USERNAME_COLUMN = "username"

# Статус клиента после закрытия на реальный счёт (/activate)
STATUS_ACTIVE = "реальный"
DATE_FORMAT = "%Y-%m-%d %H:%M"

# Если дата последнего контакта неизвестна, считаем что прошло 2 дня
# (прежнее поведение бота — клиент попадает в /today как требующий внимания)
DEFAULT_CONTACT_AGE = timedelta(days=2)

# Повторные попытки при сбоях Google Sheets API
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0  # секунды; задержка растёт с каждой попыткой


def _parse_last_contact(raw: str) -> datetime:
    """Разбирает дату последнего контакта из таблицы, при неудаче — 2 дня назад."""
    for fmt in (DATE_FORMAT, "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return datetime.now() - DEFAULT_CONTACT_AGE


class ClientRepository:
    """Хранит карточки клиентов и записывает изменения обратно в Google Sheets.

    Чтение — из памяти (мгновенно). Запись — сначала в память, затем
    в таблицу через поток (asyncio.to_thread), чтобы не блокировать бота.
    При сбое API запись повторяется с переподключением; если все попытки
    исчерпаны, изменение остаётся в памяти и логируется.
    """

    def __init__(self):
        self._clients = {}
        self._sheet = None
        self._columns = {}   # заголовок -> номер столбца (1-based)
        self._rows = {}      # username -> номер строки (1-based)
        self._lock = threading.Lock()

    # --- Загрузка ---

    def load(self):
        """(Пере)читывает клиентов из Google Sheets.

        Возвращает количество клиентов при успехе или None при сбое.
        При сбое текущие данные в памяти НЕ трогаются — бот продолжает
        работать на прежних данных (важно для /reload и авто-перечитывания).
        """
        sheet = get_sheet()
        if not sheet:
            logger.warning("Google Sheets недоступен — данные не перечитаны")
            return None

        values = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                values = sheet.get_all_values()
                break
            except Exception as e:
                logger.warning("Попытка %d/%d чтения таблицы не удалась: %s", attempt, RETRY_ATTEMPTS, e)
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BASE_DELAY * attempt)
                    sheet = get_sheet() or sheet
        if values is None:
            logger.error("Не удалось прочитать таблицу — работаем на прежних данных")
            return None

        try:
            new_clients = {}
            new_rows = {}
            new_columns = {}

            if len(values) >= 2:
                headers = [str(h).strip().lower() for h in values[0]]
                new_columns = {h: i + 1 for i, h in enumerate(headers) if h}

                # Поля, которые могли жить только в памяти (нет столбца в таблице) —
                # при перечитывании их нельзя терять
                has_contact_col = "последний_контакт" in new_columns
                has_status_col = "статус" in new_columns
                has_chat_col = "chat_id" in new_columns

                for row_number, row in enumerate(values[1:], start=2):
                    if not row or not row[0]:
                        continue
                    row_dict = {headers[i]: str(row[i]).strip() for i in range(min(len(headers), len(row)))}
                    username = str(row_dict.get(USERNAME_COLUMN, "")).lower().replace("@", "")
                    if not username:
                        continue
                    stage = row_dict.get("текущий_этап", STAGES[0])
                    client = {
                        "name": row_dict.get("имя", ""),
                        "experience": row_dict.get("опыт", ""),
                        "terminal": row_dict.get("терминал", ""),
                        "deposit": row_dict.get("депозит", ""),
                        "format": row_dict.get("формат_торговли", ""),
                        "stage": stage,
                        "stage_index": get_stage_index(stage),
                        "notes": row_dict.get("заметки", ""),
                        "created_date": row_dict.get("дата_старта", "—"),
                        "last_contact": _parse_last_contact(row_dict.get("последний_контакт", "")),
                        "status": row_dict.get("статус", ""),
                        "chat_id": row_dict.get("chat_id", ""),
                    }

                    old = self._clients.get(username)
                    if old:
                        if not has_contact_col:
                            client["last_contact"] = old.get("last_contact", client["last_contact"])
                        if not has_status_col and old.get("status"):
                            client["status"] = old["status"]
                        if not has_chat_col and old.get("chat_id"):
                            client["chat_id"] = old["chat_id"]

                    new_clients[username] = client
                    new_rows[username] = row_number
            else:
                logger.info("Таблица пуста — клиентов нет")

            # Атомарная подмена состояния — только после успешного разбора
            self._sheet = sheet
            self._columns = new_columns
            self._clients = new_clients
            self._rows = new_rows

            logger.info("Загружено %d клиентов", len(self._clients))
            return len(self._clients)
        except Exception:
            logger.exception("Ошибка разбора данных — работаем на прежних данных")
            return None

    def _reconnect(self) -> bool:
        """Пересоздаёт подключение к таблице (токен мог истечь, сеть моргнуть)."""
        logger.info("Переподключение к Google Sheets...")
        new_sheet = get_sheet()
        if new_sheet:
            self._sheet = new_sheet
            try:
                headers = [str(h).strip().lower() for h in new_sheet.row_values(1)]
                if headers:
                    self._columns = {h: i + 1 for i, h in enumerate(headers) if h}
            except Exception:
                logger.exception("Не удалось обновить заголовки после переподключения")
            return True
        return False

    # --- Чтение ---

    def __contains__(self, username: str) -> bool:
        return username in self._clients

    def __len__(self) -> int:
        return len(self._clients)

    def get(self, username: str):
        return self._clients.get(username)

    def items(self):
        return self._clients.items()

    def broadcast_list(self):
        """Клиенты со статусом «реальный» — получатели рассылок /broadcast."""
        return [(u, c) for u, c in self._clients.items()
                if str(c.get("status", "")).strip().lower() == STATUS_ACTIVE]

    # --- Изменения (память + таблица) ---

    async def add_note(self, username: str, note_text: str) -> bool:
        """Добавляет заметку и сохраняет полный список заметок в таблицу."""
        client = self._clients.get(username)
        if client is None:
            return False
        if "notes" not in client:
            client["notes"] = ""
        client["notes"] += f"\n• {note_text}"
        await self._persist(username, "notes")
        return True

    async def mark_contact(self, username: str) -> bool:
        """Отмечает контакт текущим временем и сохраняет в таблицу."""
        client = self._clients.get(username)
        if client is None:
            return False
        client["last_contact"] = datetime.now()
        await self._persist(username, "last_contact")
        return True

    async def set_stage(self, username: str, stage: str) -> bool:
        """Устанавливает этап клиента и сохраняет в таблицу."""
        client = self._clients.get(username)
        if client is None:
            return False
        client["stage"] = stage
        client["stage_index"] = get_stage_index(stage)
        await self._persist(username, "stage")
        return True

    async def update_field(self, username: str, field: str, value: str) -> bool:
        """Обновляет одно поле карточки клиента и сохраняет в таблицу."""
        client = self._clients.get(username)
        if client is None or field not in FIELD_COLUMNS:
            return False
        client[field] = value
        await self._persist(username, field)
        return True

    async def add_client(self, username: str, data: dict) -> bool:
        """Создаёт карточку клиента (этап 1) и добавляет строку в таблицу."""
        if username in self._clients:
            return False
        now = datetime.now()
        self._clients[username] = {
            "name": data.get("name", ""),
            "experience": data.get("experience", ""),
            "terminal": data.get("terminal", ""),
            "deposit": data.get("deposit", ""),
            "format": data.get("format", ""),
            "stage": STAGES[0],
            "stage_index": 0,
            "notes": "",
            "created_date": now.strftime("%Y-%m-%d"),
            "last_contact": now,
            "status": "",
            "chat_id": "",
        }
        await asyncio.to_thread(self._append_row_with_retries, username)
        return True

    def _append_row_with_retries(self, username: str) -> bool:
        """Добавление строки клиента с повторными попытками при сбоях API."""
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self._append_row(username)
            except Exception as e:
                logger.warning(
                    "Попытка %d/%d добавления строки @%s не удалась: %s",
                    attempt, RETRY_ATTEMPTS, username, e,
                )
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BASE_DELAY * attempt)
                    self._reconnect()
        logger.error("Строка @%s не добавлена в таблицу — карточка осталась только в памяти", username)
        return False

    def _append_row(self, username: str) -> bool:
        """Синхронное добавление строки. Вызывается только через ретрай-обёртку."""
        if not self._sheet:
            logger.warning("Таблица недоступна — карточка @%s только в памяти", username)
            return False
        if not self._columns:
            logger.warning("В таблице нет заголовков — карточка @%s только в памяти", username)
            return False

        client = self._clients[username]
        row = [""] * max(self._columns.values())

        username_col = self._columns.get(USERNAME_COLUMN)
        if username_col:
            row[username_col - 1] = f"@{username}"

        for field, header in FIELD_COLUMNS.items():
            col = self._columns.get(header)
            if not col:
                continue
            value = client.get(field, "")
            if isinstance(value, datetime):
                value = value.strftime(DATE_FORMAT)
            row[col - 1] = str(value)

        with self._lock:
            self._sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Клиент @%s добавлен в таблицу", username)
        return True

    # --- Запись в Google Sheets ---

    async def _persist(self, username: str, *fields: str) -> None:
        """Пишет указанные поля клиента в таблицу, не блокируя event loop."""
        try:
            for field in fields:
                saved = await asyncio.to_thread(self._persist_field_with_retries, username, field)
                if saved:
                    logger.info("Сохранено в таблицу: @%s.%s", username, field)
        except Exception:
            logger.exception("Не удалось сохранить @%s в таблицу (изменение осталось в памяти)", username)

    def _persist_field_with_retries(self, username: str, field: str) -> bool:
        """Запись поля с повторными попытками и переподключением при сбоях API."""
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self._persist_field(username, field)
            except Exception as e:
                logger.warning(
                    "Попытка %d/%d записи @%s.%s не удалась: %s",
                    attempt, RETRY_ATTEMPTS, username, field, e,
                )
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BASE_DELAY * attempt)
                    self._reconnect()
        logger.error(
            "Все попытки записи @%s.%s исчерпаны — изменение осталось только в памяти",
            username, field,
        )
        return False

    def _persist_field(self, username: str, field: str) -> bool:
        """Синхронная запись одного поля. Вызывается только через ретрай-обёртку."""
        if not self._sheet:
            logger.warning("Таблица недоступна — @%s.%s не сохранено", username, field)
            return False

        header = FIELD_COLUMNS.get(field)
        col = self._columns.get(header)
        if not col:
            logger.warning("В таблице нет столбца «%s» — поле %s хранится только в памяти", header, field)
            return False

        row = self._find_row(username)
        if not row:
            logger.warning("Строка клиента @%s не найдена в таблице", username)
            return False

        value = self._clients[username][field]
        if isinstance(value, datetime):
            value = value.strftime(DATE_FORMAT)

        with self._lock:
            self._sheet.update_cell(row, col, str(value))
        return True

    def _find_row(self, username: str):
        """Возвращает номер строки клиента, перепроверяя кэш (строки могли сдвинуться)."""
        username_col = self._columns.get(USERNAME_COLUMN)
        if not username_col:
            return None

        cached = self._rows.get(username)
        if cached:
            cell_value = str(self._sheet.cell(cached, username_col).value or "")
            if cell_value.lower().replace("@", "").strip() == username:
                return cached

        # Кэш неверен (строки сдвинулись) — ищем заново по столбцу username
        column_values = self._sheet.col_values(username_col)
        for row_number, cell_value in enumerate(column_values[1:], start=2):
            if str(cell_value).lower().replace("@", "").strip() == username:
                self._rows[username] = row_number
                return row_number
        return None
