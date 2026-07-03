# test_broadcast.py - Тесты /activate, /broadcast, chat_id и ежедневного напоминания

import asyncio

from telegram.ext import ConversationHandler

import config
from repository import STATUS_ACTIVE
from tests.conftest import FakeCallbackQuery, FakeContext, FakeUpdate


# --- /activate ---

def test_activate_sets_status(bot_module, repo, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.activate(u, FakeContext("@ivan")))
    assert STATUS_ACTIVE in u.message.replies[0]
    assert repo.get("ivan")["status"] == STATUS_ACTIVE
    assert fake_sheet.rows[1][10] == STATUS_ACTIVE  # столбец «статус»
    # у Ивана нет chat_id — оператор предупреждён
    assert "не писал этому боту" in u.message.replies[0]


def test_activate_unknown_client(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.activate(u, FakeContext("@nobody")))
    assert "не найден" in u.message.replies[0]


def test_broadcast_list_contains_only_active(repo):
    usernames = [u for u, _ in repo.broadcast_list()]
    assert usernames == ["petr"]  # у Петра статус «реальный» в фикстуре


# --- Автозапоминание chat_id ---

def test_track_client_chat_saves_chat_id(bot_module, repo, fake_sheet):
    u = FakeUpdate(user_id=777, username="Ivan")  # клиент пишет боту
    asyncio.run(bot_module.track_client_chat(u, FakeContext()))
    assert repo.get("ivan")["chat_id"] == "777"
    assert fake_sheet.rows[1][11] == "777"  # столбец «chat_id»


def test_track_ignores_operators_and_strangers(bot_module, repo):
    # оператор
    u = FakeUpdate(username="ivan")
    asyncio.run(bot_module.track_client_chat(u, FakeContext()))
    assert repo.get("ivan")["chat_id"] == ""
    # незнакомец, которого нет в базе
    u = FakeUpdate(user_id=888, username="stranger")
    asyncio.run(bot_module.track_client_chat(u, FakeContext()))
    assert "stranger" not in repo


# --- /broadcast ---

def start_broadcast(bot_module, ctx, text="Важная новость"):
    u = FakeUpdate()
    state = asyncio.run(bot_module.broadcast_start(u, ctx))
    assert state == bot_module.BROADCAST_TEXT
    u = FakeUpdate(text)
    state = asyncio.run(bot_module.broadcast_text(u, ctx))
    assert state == ConversationHandler.END
    return ctx.user_data["broadcast"]


def test_broadcast_empty_list_aborts(bot_module, repo):
    asyncio.run(repo.update_field("petr", "status", ""))  # убираем единственного «реального»
    u = FakeUpdate()
    state = asyncio.run(bot_module.broadcast_start(u, FakeContext()))
    assert state == ConversationHandler.END
    assert "пуст" in u.message.replies[0]


def test_broadcast_toggle_and_send(bot_module):
    ctx = FakeContext()
    state = start_broadcast(bot_module, ctx)
    assert state["selected"] == set()

    # выбор получателя чекбоксом
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("bc:t:petr")
    asyncio.run(bot_module.broadcast_button(u, ctx))
    assert "petr" in ctx.user_data["broadcast"]["selected"]
    assert u.callback_query.message.markups  # клавиатура перерисована

    # отправка
    u2 = FakeUpdate()
    u2.callback_query = FakeCallbackQuery("bc:send")
    asyncio.run(bot_module.broadcast_button(u2, ctx))
    assert ctx.bot.sent == [(555, "Важная новость")]  # chat_id Петра из фикстуры
    report = u2.callback_query.message.edits[-1]
    assert "Доставлено: 1" in report and "@petr" in report
    assert "broadcast" not in ctx.user_data  # состояние очищено


def test_broadcast_select_all_and_missing_chat_id(bot_module, repo):
    asyncio.run(repo.update_field("ivan", "status", STATUS_ACTIVE))  # Иван активен, но без chat_id
    ctx = FakeContext()
    start_broadcast(bot_module, ctx)

    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("bc:all")
    asyncio.run(bot_module.broadcast_button(u, ctx))
    assert ctx.user_data["broadcast"]["selected"] == {"ivan", "petr"}

    u2 = FakeUpdate()
    u2.callback_query = FakeCallbackQuery("bc:send")
    asyncio.run(bot_module.broadcast_button(u2, ctx))
    report = u2.callback_query.message.edits[-1]
    assert "Доставлено: 1" in report                      # Пётр получил
    assert "@ivan — клиент ещё не писал боту" in report   # Иван — честно в отчёте


def test_broadcast_send_without_selection_warns(bot_module):
    ctx = FakeContext()
    start_broadcast(bot_module, ctx)
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("bc:send")
    asyncio.run(bot_module.broadcast_button(u, ctx))
    assert ctx.bot.sent == []
    assert "broadcast" in ctx.user_data  # состояние не потеряно


def test_broadcast_cancel_button(bot_module):
    ctx = FakeContext()
    start_broadcast(bot_module, ctx)
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("bc:cancel")
    asyncio.run(bot_module.broadcast_button(u, ctx))
    assert "broadcast" not in ctx.user_data
    assert "отменена" in u.callback_query.message.edits[-1]


def test_broadcast_delivery_failure_reported(bot_module):
    ctx = FakeContext()
    ctx.bot.fail_chat_ids = {555}  # Пётр заблокировал бота
    start_broadcast(bot_module, ctx)
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("bc:t:petr")
    asyncio.run(bot_module.broadcast_button(u, ctx))
    u2 = FakeUpdate()
    u2.callback_query = FakeCallbackQuery("bc:send")
    asyncio.run(bot_module.broadcast_button(u2, ctx))
    report = u2.callback_query.message.edits[-1]
    assert "Доставлено: 0" in report and "ошибка отправки" in report


# --- Ежедневное напоминание ---

def test_daily_reminder_sends_digest_to_operators(bot_module):
    ctx = FakeContext()
    asyncio.run(bot_module.daily_reminder(ctx))
    chat_ids = [chat_id for chat_id, _ in ctx.bot.sent]
    assert chat_ids == config.ALLOWED_USERS
    assert all(text.startswith("⏰") and "@ivan" in text for _, text in ctx.bot.sent)


def test_daily_reminder_silent_when_no_tasks(bot_module, repo, monkeypatch):
    # делаем всех «не срочными»: этап > 3 и свежий контакт
    from datetime import datetime
    for _, client in repo.items():
        client["stage_index"] = 5
        client["last_contact"] = datetime.now()
    ctx = FakeContext()
    asyncio.run(bot_module.daily_reminder(ctx))
    assert ctx.bot.sent == []


# --- Конфигурация времени напоминания ---

def test_parse_reminder_time_valid(monkeypatch):
    monkeypatch.setattr(config, "REMINDER_TIME", "08:30")
    t = config.parse_reminder_time()
    assert (t.hour, t.minute) == (8, 30)
    assert t.tzinfo is not None


def test_parse_reminder_time_invalid(monkeypatch):
    monkeypatch.setattr(config, "REMINDER_TIME", "не время")
    assert config.parse_reminder_time() is None
    monkeypatch.setattr(config, "REMINDER_TIME", "")
    assert config.parse_reminder_time() is None


# --- Ротация логов ---

def test_file_handler_is_rotating(monkeypatch, tmp_path):
    from logging.handlers import RotatingFileHandler
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "bot.log"))
    handler = config.build_file_handler()
    try:
        assert isinstance(handler, RotatingFileHandler)
        assert handler.maxBytes == config.LOG_MAX_BYTES
        assert handler.backupCount == config.LOG_BACKUP_COUNT
    finally:
        handler.close()
