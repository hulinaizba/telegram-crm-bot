# test_bot_commands.py - Тесты обработчиков команд и диалога /new

import asyncio
from datetime import datetime, timedelta

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


def test_today_includes_due_reminder_and_clears_it(bot_module, repo):
    # petr не срочен по этапу/давности контакта — попадает в /today только из-за напоминания
    asyncio.run(repo.update_field("petr", "reminder_date", datetime.now().strftime("%Y-%m-%d")))
    u = FakeUpdate()
    asyncio.run(bot_module.today(u, FakeContext()))
    text = u.message.replies[0]
    assert "🔔 @petr" in text
    assert repo.get("petr")["reminder_date"] == ""  # разовое напоминание снялось после показа


def test_today_ignores_future_reminder(bot_module, repo):
    future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    asyncio.run(repo.update_field("petr", "reminder_date", future))
    u = FakeUpdate()
    asyncio.run(bot_module.today(u, FakeContext()))
    text = u.message.replies[0]
    assert "@petr" not in text
    assert repo.get("petr")["reminder_date"] == future  # не наступило — не тронуто


# --- /remind ---

def test_remind_sets_reminder_date(bot_module, repo, fake_sheet):
    u = FakeUpdate()
    asyncio.run(bot_module.remind(u, FakeContext("@ivan", "2")))
    reply = u.message.replies[0]
    expected = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    assert expected in reply
    assert repo.get("ivan")["reminder_date"] == expected
    assert fake_sheet.rows[1][-1] == expected  # dата_напоминания — последний столбец фикстуры


def test_remind_clears_with_dash(bot_module, repo):
    asyncio.run(repo.update_field("ivan", "reminder_date", "2026-08-01"))
    u = FakeUpdate()
    asyncio.run(bot_module.remind(u, FakeContext("@ivan", "-")))
    assert "снято" in u.message.replies[0]
    assert repo.get("ivan")["reminder_date"] == ""


def test_remind_requires_two_args(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.remind(u, FakeContext("@ivan")))
    assert "Использование" in u.message.replies[0]


def test_remind_rejects_non_numeric(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.remind(u, FakeContext("@ivan", "скоро")))
    assert "Использование" in u.message.replies[0]


def test_remind_unknown_client(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.remind(u, FakeContext("@nobody", "1")))
    assert "не найден" in u.message.replies[0]


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


# --- /idea и /ideas ---

def test_idea_requires_text(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.add_idea(u, FakeContext()))
    assert "Использование" in u.message.replies[0]


def test_idea_saves_and_confirms(bot_module, monkeypatch):
    saved = {}

    async def fake_add_idea(author, text):
        saved["author"] = author
        saved["text"] = text
        return True

    monkeypatch.setattr(bot_module.ideas, "add_idea", fake_add_idea)
    u = FakeUpdate()
    asyncio.run(bot_module.add_idea(u, FakeContext("Добавить", "экспорт", "в", "PDF")))
    assert "записана" in u.message.replies[0]
    assert saved["text"] == "Добавить экспорт в PDF"


def test_idea_reports_save_failure_gracefully(bot_module, monkeypatch):
    async def fake_add_idea(author, text):
        return False

    monkeypatch.setattr(bot_module.ideas, "add_idea", fake_add_idea)
    u = FakeUpdate()
    asyncio.run(bot_module.add_idea(u, FakeContext("текст")))
    assert "не потеряется" in u.message.replies[0]


def test_ideas_lists_recent(bot_module, monkeypatch):
    async def fake_get_recent(limit=10):
        return [["2026-07-01 10:00", "Иван", "Идея про экспорт"]]

    monkeypatch.setattr(bot_module.ideas, "get_recent_ideas", fake_get_recent)
    u = FakeUpdate()
    asyncio.run(bot_module.show_ideas(u, FakeContext()))
    assert "Идея про экспорт" in u.message.replies[0]
    assert "Иван" in u.message.replies[0]


def test_ideas_empty_list(bot_module, monkeypatch):
    async def fake_get_recent(limit=10):
        return []

    monkeypatch.setattr(bot_module.ideas, "get_recent_ideas", fake_get_recent)
    u = FakeUpdate()
    asyncio.run(bot_module.show_ideas(u, FakeContext()))
    assert "Пока нет" in u.message.replies[0]


def test_ideas_sheet_unavailable(bot_module, monkeypatch):
    async def fake_get_recent(limit=10):
        return None

    monkeypatch.setattr(bot_module.ideas, "get_recent_ideas", fake_get_recent)
    u = FakeUpdate()
    asyncio.run(bot_module.show_ideas(u, FakeContext()))
    assert "не удалось" in u.message.replies[0].lower()


# --- /delete ---

def test_delete_asks_confirmation(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.delete_client_start(u, FakeContext("@ivan")))
    text = u.message.replies[0]
    assert "Удалить клиента @ivan" in text
    assert "нельзя отменить" in text


def test_delete_unknown_client(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.delete_client_start(u, FakeContext("@nobody")))
    assert "не найден" in u.message.replies[0]


def test_delete_requires_argument(bot_module):
    u = FakeUpdate()
    asyncio.run(bot_module.delete_client_start(u, FakeContext()))
    assert "Использование" in u.message.replies[0]


def test_delete_confirm_removes_client(bot_module, repo, fake_sheet):
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("del:confirm:ivan")
    asyncio.run(bot_module.delete_client_button(u, FakeContext()))
    assert "ivan" not in repo
    assert "удалён" in u.callback_query.message.edits[-1]
    assert len(fake_sheet.rows) == 2  # строка реально исчезла из таблицы


def test_delete_cancel_keeps_client(bot_module, repo):
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("del:cancel:ivan")
    asyncio.run(bot_module.delete_client_button(u, FakeContext()))
    assert "ivan" in repo
    assert "отменено" in u.callback_query.message.edits[-1]


def test_delete_confirm_already_gone(bot_module, repo):
    asyncio.run(repo.delete_client("ivan"))
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("del:confirm:ivan")
    asyncio.run(bot_module.delete_client_button(u, FakeContext()))
    assert "уже не найден" in u.callback_query.message.edits[-1]


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

def run_new_dialog_to_reminder(bot_module, ctx, answers):
    """Прогоняет диалог /new по шагам 1-7 (до вопроса про напоминание)."""
    u = FakeUpdate()
    asyncio.run(bot_module.new_client_start(u, ctx))
    steps = [
        bot_module.new_client_username,
        bot_module.new_client_name,
        bot_module.new_client_experience,
        bot_module.new_client_terminal,
        bot_module.new_client_deposit,
        bot_module.new_client_format,
        bot_module.new_client_note,
    ]
    for step, answer in zip(steps, answers):
        u = FakeUpdate(answer)
        state = asyncio.run(step(u, ctx))
    return u, state


BASE_ANSWERS = ["@Maria_FX", "Мария", "новичок", "MT5", "500", "авто"]


def test_new_client_note_step_prompts_reminder(bot_module):
    ctx = FakeContext()
    u, state = run_new_dialog_to_reminder(
        bot_module, ctx, BASE_ANSWERS + ["Просил перезвонить вечером"],
    )
    assert state == bot_module.NEW_REMINDER
    assert "Шаг 8/8" in u.message.replies[-1]
    assert ctx.user_data["new_client"]["notes"] == "Просил перезвонить вечером"


def test_new_client_note_skip_with_dash(bot_module):
    ctx = FakeContext()
    run_new_dialog_to_reminder(bot_module, ctx, BASE_ANSWERS + ["-"])
    assert ctx.user_data["new_client"]["notes"] == ""


def test_new_client_reminder_via_text_creates_client(bot_module, repo, fake_sheet):
    ctx = FakeContext()
    run_new_dialog_to_reminder(bot_module, ctx, BASE_ANSWERS + ["-"])
    u = FakeUpdate("3")
    state = asyncio.run(bot_module.new_client_reminder_text(u, ctx))
    assert state == ConversationHandler.END
    reply = u.message.replies[0]
    assert "Клиент добавлен" in reply and "(1/7)" in reply
    expected = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    assert repo.get("maria_fx")["reminder_date"] == expected
    assert f"🔔 Напоминание: {expected} в 08:00" in reply
    assert fake_sheet.rows[-1][0] == "@maria_fx"


def test_new_client_reminder_skip_via_dash(bot_module, repo):
    ctx = FakeContext()
    run_new_dialog_to_reminder(bot_module, ctx, BASE_ANSWERS + ["-"])
    u = FakeUpdate("-")
    state = asyncio.run(bot_module.new_client_reminder_text(u, ctx))
    assert state == ConversationHandler.END
    assert repo.get("maria_fx")["reminder_date"] == ""


def test_new_client_reminder_invalid_text_reprompts(bot_module, repo):
    ctx = FakeContext()
    run_new_dialog_to_reminder(bot_module, ctx, BASE_ANSWERS + ["-"])
    u = FakeUpdate("завтра")
    state = asyncio.run(bot_module.new_client_reminder_text(u, ctx))
    assert state == bot_module.NEW_REMINDER
    assert "maria_fx" not in repo  # клиент ещё не создан, ждём корректный ввод


def test_new_client_reminder_via_button(bot_module, repo, fake_sheet):
    ctx = FakeContext()
    run_new_dialog_to_reminder(bot_module, ctx, BASE_ANSWERS + ["-"])
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("newrem:5")
    state = asyncio.run(bot_module.new_client_reminder_button(u, ctx))
    assert state == ConversationHandler.END
    expected = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    assert repo.get("maria_fx")["reminder_date"] == expected
    assert "Клиент добавлен" in u.callback_query.message.replies[0]


def test_new_client_reminder_button_skip(bot_module, repo):
    ctx = FakeContext()
    run_new_dialog_to_reminder(bot_module, ctx, BASE_ANSWERS + ["-"])
    u = FakeUpdate()
    u.callback_query = FakeCallbackQuery("newrem:skip")
    state = asyncio.run(bot_module.new_client_reminder_button(u, ctx))
    assert state == ConversationHandler.END
    assert repo.get("maria_fx")["reminder_date"] == ""


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
