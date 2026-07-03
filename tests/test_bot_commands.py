# test_bot_commands.py - Тесты обработчиков команд и диалога /new

import asyncio

from telegram.ext import ConversationHandler

from tests.conftest import FakeCallbackQuery, FakeContext, FakeUpdate


# --- Авторизация ---

def test_unauthorized_user_is_rejected(bot_module):
    u = FakeUpdate(user_id=999)
    asyncio.run(bot_module.today(u, FakeContext()))
    assert "⛔" in u.message.replies[0]


def test_known_client_gets_friendly_greeting(bot_module):
    # клиент из базы пишет /start — приветствие вместо отказа
    u = FakeUpdate(user_id=777, username="Ivan")
    asyncio.run(bot_module.start(u, FakeContext()))
    reply = u.message.replies[0]
    assert "⛔" not in reply
    assert "менеджер" in reply


def test_known_client_greeting_plus_chat_id_capture(bot_module, repo):
    # полный путь: клиент пишет /start -> приветствие + chat_id запомнен
    ctx = FakeContext()
    u = FakeUpdate(user_id=777, username="Ivan")
    asyncio.run(bot_module.start(u, ctx))            # group 0: приветствие
    asyncio.run(bot_module.track_client_chat(u, ctx))  # group 1: запоминание chat_id
    assert "менеджер" in u.message.replies[0]
    assert repo.get("ivan")["chat_id"] == "777"


def test_stranger_with_username_still_rejected(bot_module):
    u = FakeUpdate(user_id=888, username="stranger")
    asyncio.run(bot_module.today(u, FakeContext()))
    assert "⛔" in u.message.replies[0]


def test_authorized_user_passes(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.start(u, FakeContext()))
    assert "RoboCompanion" in u.message.replies[0]


# --- /today ---

def test_today_lists_urgent_clients(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.today(u, FakeContext()))
    text = u.message.replies[0]
    assert "@ivan" in text  # этап 1 (<= 3) — всегда срочный


# --- /note и /notes ---

def test_note_and_show_notes(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.note(u, FakeContext("@ivan", "первая", "заметка")))
    assert "✅" in u.message.replies[0]

    u = FakeUpdate()
    asyncio.run(bot_module.show_notes(u, FakeContext("@ivan")))
    assert "первая заметка" in u.message.replies[0]


def test_note_requires_text(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.note(u, FakeContext("@ivan")))
    assert "Использование" in u.message.replies[0]


def test_note_unknown_client(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.note(u, FakeContext("@nobody", "текст")))
    assert "не найден" in u.message.replies[0]


# --- /done и /setstage ---

def test_done_advances_stage(bot_module, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.done(u, FakeContext("@ivan")))
    assert "(2/7)" in u.message.replies[0]
    assert fake_sheet.rows[1][6] == "проверка_ознакомления"


def test_done_on_last_stage_stays(bot_module, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.done(u, FakeContext("@petr")))
    assert "уже на последнем" in u.message.replies[0]
    assert fake_sheet.rows[2][6] == "недельный_контроль"


def test_setstage_by_number(bot_module, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.setstage(u, FakeContext("@ivan", "5")))
    assert "(5/7)" in u.message.replies[0]
    assert fake_sheet.rows[1][6] == "риск_контроль"


def test_setstage_invalid_number_shows_stage_list(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.setstage(u, FakeContext("@ivan", "9")))
    assert "Использование" in u.message.replies[0]
    assert "7." in u.message.replies[0]


# --- /contacted и /complete ---

def test_contacted_command(bot_module, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.contacted(u, FakeContext("@ivan")))
    assert "Контакт с @ivan отмечен" in u.message.replies[0]
    assert fake_sheet.rows[1][9] != "2026-07-01 10:00"


def test_complete_builds_full_summary(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.complete(u, FakeContext("@petr")))
    summary = u.message.replies[0]
    for part in ("Сводка по @petr", "Пётр", "есть опыт", "MT4", "2000",
                 "полуавто", "Недельный контроль", "важный клиент"):
        assert part in summary


# --- /edit ---

def test_edit_updates_field(bot_module, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.edit_client(u, FakeContext("@ivan", "депозит", "1500")))
    assert "«депозит» → 1500" in u.message.replies[0]
    assert fake_sheet.rows[1][4] == "1500"


def test_edit_rejects_unknown_field(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.edit_client(u, FakeContext("@ivan", "город", "Москва")))
    assert "Использование" in u.message.replies[0]


# --- /reload и авто-перечитывание ---

def test_reload_command(bot_module, repo, fake_sheet):
    fake_sheet.rows[1][1] = "Переименован"
    u = FakeUpdate()
    asyncio.run(bot_module.reload_clients(u, FakeContext()))
    assert "2 клиентов" in u.message.replies[0]
    assert repo.get("ivan")["name"] == "Переименован"


def test_reload_command_failure_is_graceful(bot_module, monkeypatch):
    import repository
    monkeypatch.setattr(repository, "get_sheet", lambda: None)
    u = FakeUpdate()
    asyncio.run(bot_module.reload_clients(u, FakeContext()))
    assert "Не удалось" in u.message.replies[0]


def test_auto_reload_job(bot_module, repo, fake_sheet):
    fake_sheet.rows[1][6] = "тестер"  # менеджер сменил этап руками
    asyncio.run(bot_module.auto_reload(FakeContext()))
    assert repo.get("ivan")["stage"] == "тестер"


# --- Диалог /new ---

def run_new_dialog(bot_module, ctx, answers):
    """Прогоняет диалог /new по шагам, возвращает последний FakeUpdate."""
    u = FakeUpdate()
    asyncio.run(bot_module.new_client_start(u, ctx))
    steps = [
        bot_module.new_client_username,
        bot_module.new_client_name,
        bot_module.new_client_experience,
        bot_module.new_client_terminal,
        bot_module.new_client_deposit,
        bot_module.new_client_format,
    ]
    for step, answer in zip(steps, answers):
        u = FakeUpdate(answer)
        state = asyncio.run(step(u, ctx))
    return u, state


def test_new_client_full_dialog(bot_module, repo, fake_sheet):
    ctx = FakeContext()
    u, state = run_new_dialog(
        bot_module, ctx,
        ["@Maria_FX", "Мария", "новичок", "MT5", "500", "авто"],
    )
    assert state == ConversationHandler.END
    assert "Клиент добавлен" in u.message.replies[0]
    assert "(1/7)" in u.message.replies[0]
    assert repo.get("maria_fx")["deposit"] == "500"
    assert fake_sheet.rows[-1][0] == "@maria_fx"


def test_new_client_duplicate_username_stays_on_step_one(bot_module):
    ctx = FakeContext()
    u = FakeUpdate()
    asyncio.run(bot_module.new_client_start(u, ctx))
    u = FakeUpdate("@ivan")
    state = asyncio.run(bot_module.new_client_username(u, ctx))
    assert state == bot_module.NEW_USERNAME
    assert "уже существует" in u.message.replies[0]


def test_new_client_cancel_clears_draft(bot_module):
    ctx = FakeContext()
    u = FakeUpdate()
    asyncio.run(bot_module.new_client_start(u, ctx))
    u = FakeUpdate()
    state = asyncio.run(bot_module.new_client_cancel(u, ctx))
    assert state == ConversationHandler.END
    assert "new_client" not in ctx.user_data


# --- Кнопки ---

def test_button_template_marks_contact(bot_module, fake_sheet):
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("full_ivan")
    asyncio.run(bot_module.button_handler(u, FakeContext()))
    replies = u.callback_query.message.replies
    assert "Шаблон для @ivan" in replies[0]
    assert "Иван" in replies[0]           # {name} подставлено
    assert "Контакт отмечен" in replies[1]
    assert fake_sheet.rows[1][9] != "2026-07-01 10:00"  # дата контакта обновлена


def test_button_unknown_client(bot_module):
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("full_nobody")
    asyncio.run(bot_module.button_handler(u, FakeContext()))
    assert "не найден" in u.callback_query.message.replies[0]


def test_button_malformed_data_is_ignored(bot_module):
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("garbage")
    asyncio.run(bot_module.button_handler(u, FakeContext()))
    assert u.callback_query.message.replies == []  # молча проигнорировано, без падения
