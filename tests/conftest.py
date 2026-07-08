# conftest.py - Общие фикстуры: фейковая таблица, фейковые Update/Context

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config  # noqa: E402
import ideas  # noqa: E402
import repository  # noqa: E402

repository.RETRY_BASE_DELAY = 0.01  # ускоряем ретраи в тестах
ideas.RETRY_BASE_DELAY = 0.01

HEADERS = [
    "username", "имя", "опыт", "терминал", "депозит",
    "формат_торговли", "текущий_этап", "заметки", "дата_старта", "последний_контакт",
    "статус", "chat_id",
]


class FakeSheet:
    """Имитация gspread-worksheet: данные в списках, опционально «сбоит» первые N записей."""

    def __init__(self, rows=None, failures=0):
        self.rows = rows if rows is not None else [
            list(HEADERS),
            ["@ivan", "Иван", "новичок", "MT5", "500", "авто", "материалы", "", "2026-06-30", "2026-07-01 10:00", "", ""],
            ["@petr", "Пётр", "есть опыт", "MT4", "2000", "полуавто", "недельный_контроль", "\n• важный клиент", "2026-06-20", "", "реальный", "555"],
        ]
        self.failures_left = failures
        self.write_calls = 0

    def _maybe_fail(self):
        if self.failures_left > 0:
            self.failures_left -= 1
            raise ConnectionError("Симулированный сбой API")

    def get_all_values(self):
        return self.rows

    def row_values(self, r):
        return self.rows[r - 1]

    def cell(self, r, c):
        value = self.rows[r - 1][c - 1]
        return type("Cell", (), {"value": value})()

    def col_values(self, c):
        return [row[c - 1] if len(row) >= c else "" for row in self.rows]

    def update_cell(self, r, c, v):
        self._maybe_fail()
        self.rows[r - 1][c - 1] = v
        self.write_calls += 1

    def delete_rows(self, start_index, end_index=None):
        self._maybe_fail()
        end_index = end_index or start_index
        del self.rows[start_index - 1:end_index]
        self.write_calls += 1

    def append_row(self, row, **kwargs):
        self._maybe_fail()
        self.rows.append(list(row))
        self.write_calls += 1


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []
        self.edits = []
        self.markups = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        self.markups.append(reply_markup)


class FakeUser:
    def __init__(self, user_id, username=None):
        self.id = user_id
        self.username = username


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeUpdate:
    def __init__(self, text="", user_id=None, username=None):
        uid = user_id if user_id is not None else config.ALLOWED_USERS[0]
        self.message = FakeMessage(text)
        self.effective_message = self.message
        self.effective_user = FakeUser(uid, username)
        self.effective_chat = FakeChat(uid)
        self.callback_query = None


class FakeCallbackQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message if message is not None else FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeBot:
    def __init__(self, fail_chat_ids=None):
        self.sent = []
        self.fail_chat_ids = set(fail_chat_ids or [])

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_chat_ids:
            raise RuntimeError("Клиент заблокировал бота")
        self.sent.append((chat_id, text))


class FakeContext:
    def __init__(self, *args):
        self.args = list(args)
        self.user_data = {}
        self.bot = FakeBot()


@pytest.fixture
def fake_sheet():
    return FakeSheet()


@pytest.fixture
def repo(fake_sheet, monkeypatch):
    monkeypatch.setattr(repository, "get_sheet", lambda: fake_sheet)
    r = repository.ClientRepository()
    r.load()
    return r


@pytest.fixture
def bot_module(repo, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "repo", repo)
    return bot
