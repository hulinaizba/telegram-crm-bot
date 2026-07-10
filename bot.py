# bot.py - RoboCompanion v2: точка входа и обработчики Telegram

import asyncio
import logging
import sys
from datetime import datetime, timedelta
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import ideas
from repository import STATUS_ACTIVE, ClientRepository
from stages import (
    STAGES,
    TOTAL_STAGES,
    get_next_stage,
    get_progress,
    get_stage_name,
    is_last_stage,
)
from templates import format_template

logger = logging.getLogger(__name__)

CALLBACK_ACTIONS = ("short", "full", "contacted")

# Поля карточки, доступные для /edit: название в команде -> внутреннее имя поля
EDITABLE_FIELDS = {
    "имя": "name",
    "опыт": "experience",
    "терминал": "terminal",
    "депозит": "deposit",
    "формат": "format",
    "формат_торговли": "format",
}

# Состояния диалога /new
(
    NEW_USERNAME, NEW_NAME, NEW_EXPERIENCE, NEW_TERMINAL, NEW_DEPOSIT,
    NEW_FORMAT, NEW_NOTE, NEW_REMINDER,
) = range(8)
NEW_TOTAL_STEPS = 8

# Быстрые варианты в шаге настройки напоминания при /new
REMINDER_QUICK_DAYS = (1, 2, 3, 4, 5)

# Состояние диалога /broadcast
BROADCAST_TEXT = 10


# --- Состояние ---

repo = ClientRepository()


# --- Вспомогательные функции ---

def parse_username(raw: str) -> str:
    """Нормализует @username из аргумента команды: '@Ivan_Trader ' -> 'ivan_trader'."""
    return raw.strip().replace("@", "").lower()


def stages_help() -> str:
    """Нумерованный список этапов для подсказок."""
    return "\n".join(f"{i + 1}. {get_stage_name(s)}" for i, s in enumerate(STAGES))


def build_summary(username: str, client: dict) -> str:
    """Собирает сводку по клиенту для передачи менеджеру."""
    notes = client.get("notes", "").strip() or "— нет"
    stage = client.get("stage", STAGES[0])
    reminder_date = str(client.get("reminder_date", "")).strip()
    reminder_line = f"\n🔔 Напоминание: {reminder_date} в 08:00" if reminder_date else ""
    return (
        f"📋 Сводка по @{username}\n\n"
        f"Имя: {client.get('name') or '—'}\n"
        f"Опыт: {client.get('experience') or '—'}\n"
        f"Терминал: {client.get('terminal') or '—'}\n"
        f"Депозит: {client.get('deposit') or '—'}\n"
        f"Формат торговли: {client.get('format') or '—'}\n"
        f"Дата старта: {client.get('created_date') or '—'}\n"
        f"Этап: {get_stage_name(stage)} ({get_progress(stage)}){reminder_line}\n\n"
        f"📝 Заметки:\n{notes}"
    )


async def build_today_digest():
    """Собирает дайджест срочных задач: (текст, клавиатура) или (None, None).

    Клиент попадает в дайджест по двум независимым причинам: обычная
    срочность (ранний этап / давно не было контакта) или наступившая дата
    ручного напоминания (idea 2, /remind и шаг настройки в /new). Разовое
    напоминание снимается сразу после того, как попало в дайджест — не
    важно, вызван ли дайджест вручную (/today) или по расписанию в 08:00.
    """
    if not len(repo):
        return None, None

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    urgent = []
    for u, d in repo.items():
        days = (now - d.get("last_contact", now)).days
        reminder_date = str(d.get("reminder_date", "")).strip()
        reminder_due = bool(reminder_date) and reminder_date <= today_str
        if d.get("stage_index", 0) <= 2 or days >= 3 or reminder_due:
            urgent.append((u, d, days, reminder_due))

    if not urgent:
        return None, None

    text = f"📅 Задачи на сегодня ({len(urgent)} срочных)\n\n"
    keyboard = []

    # Напоминания — в начале списка (осознанное действие оператора важнее
    # автоматической срочности по этапу/давности)
    for username, data, days, reminder_due in sorted(
        urgent, key=lambda x: (not x[3], -x[2], x[1].get("stage_index", 99))
    ):
        name = data.get("name", username)
        stage = data.get("stage")
        if reminder_due:
            emoji = "🔔"
        elif days >= 3:
            emoji = "🔴"
        else:
            emoji = "🟠"
        text += f"{emoji} @{username} — {name}\n   Этап: {stage} ({days} дней)\n\n"

        keyboard.append([
            InlineKeyboardButton("Короткий", callback_data=f"short_{username}"),
            InlineKeyboardButton("Подробный", callback_data=f"full_{username}"),
        ])
        keyboard.append([
            InlineKeyboardButton("Отметить контакт", callback_data=f"contacted_{username}"),
        ])

        if reminder_due:
            await repo.update_field(username, "reminder_date", "")

    return text, InlineKeyboardMarkup(keyboard)


# --- Авторизация ---

CLIENT_GREETING = (
    "👋 Здравствуйте! Это служебный бот вашего менеджера.\n"
    "Писать сюда ничего не нужно — менеджер свяжется с вами напрямую."
)


def restricted(handler):
    """Декоратор: команды доступны только операторам из ALLOWED_USERS.

    Клиент из базы получает вежливое приветствие (его chat_id при этом
    запоминается обработчиком track_client_chat), посторонний — отказ.
    """

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in config.ALLOWED_USERS:
            username = (user.username or "").lower() if user else ""
            if update.effective_message:
                if username and username in repo:
                    logger.info("Клиент @%s написал боту — отправлено приветствие", username)
                    await update.effective_message.reply_text(CLIENT_GREETING)
                else:
                    logger.warning("Отказ в доступе: user_id=%s", user.id if user else "unknown")
                    await update.effective_message.reply_text("⛔ У вас нет доступа.")
            return
        return await handler(update, context)

    return wrapper


# --- Обработчики команд ---

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ RoboCompanion v2 — твой личный помощник\n\n"
        "Команды:\n"
        "/new — добавить клиента\n"
        "/today — задачи на сегодня (ГЛАВНЫЙ инструмент)\n"
        "/done @username — следующий этап\n"
        "/setstage @username N — изменить этап\n"
        "/note @username текст — добавить заметку\n"
        "/notes @username — посмотреть все заметки\n"
        "/search текст — поиск клиента\n"
        "/edit @username поле значение — редактировать\n"
        "/complete @username — сводка\n"
        "/clients — список всех\n"
        "/contacted @username — отметить контакт\n"
        "/activate @username — статус «реальный» (в broadcast-список)\n"
        "/broadcast — рассылка по реальным клиентам\n"
        "/reload — перечитать таблицу из Google Sheets\n"
        "/delete @username — удалить клиента (с подтверждением)\n"
        "/idea текст — записать идею по улучшению бота на будущее\n"
        "/ideas — посмотреть последние записанные идеи\n"
        "/remind @username N — напомнить об этом клиенте через N дней в 08:00\n\n"
        "Главный инструмент: /today"
    )


@restricted
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = await build_today_digest()
    if text is None:
        if not len(repo):
            await update.message.reply_text("Сегодня задач нет.")
        else:
            await update.message.reply_text("✅ Сегодня срочных задач нет.")
        return
    await update.message.reply_text(text, reply_markup=markup)


@restricted
async def clients_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not len(repo):
        await update.message.reply_text("Пока нет клиентов.")
        return
    text = "📋 Список всех клиентов\n\n"
    for u, d in repo.items():
        status = " ⭐" if str(d.get("status", "")).strip().lower() == STATUS_ACTIVE else ""
        text += f"@{u} — {d.get('name')} — {d.get('stage')}{status}\n"
    await update.message.reply_text(text)


@restricted
async def search_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /search текст")
        return
    query = " ".join(context.args).lower()
    found = [(u, d) for u, d in repo.items()
             if query in u or query in d.get("name", "").lower() or query in d.get("notes", "").lower()]
    if not found:
        await update.message.reply_text("Ничего не найдено.")
        return
    text = f"🔍 Найдено {len(found)} клиентов:\n\n"
    for u, d in found:
        text += f"@{u} — {d.get('name')} — {d.get('stage')}\n"
    await update.message.reply_text(text)


@restricted
async def note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /note @username текст заметки")
        return
    username = parse_username(context.args[0])
    note_text = " ".join(context.args[1:]).strip()
    if not username or not note_text:
        await update.message.reply_text("Использование: /note @username текст заметки")
        return
    if await repo.add_note(username, note_text):
        await update.message.reply_text(f"✅ Заметка добавлена для @{username}")
    else:
        await update.message.reply_text("Клиент не найден.")


@restricted
async def show_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /notes @username")
        return
    username = parse_username(context.args[0])
    client = repo.get(username)
    if client is None:
        await update.message.reply_text("Клиент не найден.")
        return
    notes = client.get("notes", "").strip()
    if not notes:
        await update.message.reply_text(f"У клиента @{username} пока нет заметок.")
        return
    await update.message.reply_text(f"📝 Заметки по @{username}:\n{notes}")


@restricted
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /done @username")
        return
    username = parse_username(context.args[0])
    client = repo.get(username)
    if client is None:
        await update.message.reply_text("Клиент не найден.")
        return
    current = client.get("stage", STAGES[0])
    if is_last_stage(current):
        await update.message.reply_text(
            f"@{username} уже на последнем этапе: {get_stage_name(current)} ({get_progress(current)})"
        )
        return
    new_stage = get_next_stage(current)
    await repo.set_stage(username, new_stage)
    await update.message.reply_text(
        f"✅ @{username} переведён на этап: {get_stage_name(new_stage)} ({get_progress(new_stage)})"
    )


@restricted
async def setstage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = f"Использование: /setstage @username N (1-{TOTAL_STAGES})\n\nЭтапы:\n{stages_help()}"
    if len(context.args) < 2 or not context.args[1].isdigit():
        await update.message.reply_text(usage)
        return
    username = parse_username(context.args[0])
    number = int(context.args[1])
    if not 1 <= number <= TOTAL_STAGES:
        await update.message.reply_text(usage)
        return
    if username not in repo:
        await update.message.reply_text("Клиент не найден.")
        return
    stage = STAGES[number - 1]
    await repo.set_stage(username, stage)
    await update.message.reply_text(
        f"✅ @{username} — установлен этап: {get_stage_name(stage)} ({get_progress(stage)})"
    )


@restricted
async def contacted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /contacted @username")
        return
    username = parse_username(context.args[0])
    if await repo.mark_contact(username):
        await update.message.reply_text(f"✅ Контакт с @{username} отмечен.")
    else:
        await update.message.reply_text("Клиент не найден.")


@restricted
async def complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /complete @username")
        return
    username = parse_username(context.args[0])
    client = repo.get(username)
    if client is None:
        await update.message.reply_text("Клиент не найден.")
        return
    await update.message.reply_text(build_summary(username, client))


@restricted
async def edit_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = (
        "Использование: /edit @username поле значение\n"
        f"Доступные поля: {', '.join(sorted(set(EDITABLE_FIELDS) - {'формат_торговли'}))}"
    )
    if len(context.args) < 3:
        await update.message.reply_text(usage)
        return
    username = parse_username(context.args[0])
    field_name = context.args[1].lower()
    value = " ".join(context.args[2:]).strip()
    field = EDITABLE_FIELDS.get(field_name)
    if not field or not value:
        await update.message.reply_text(usage)
        return
    if await repo.update_field(username, field, value):
        await update.message.reply_text(f"✅ @{username}: «{field_name}» → {value}")
    else:
        await update.message.reply_text("Клиент не найден.")


@restricted
async def reload_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = await asyncio.to_thread(repo.load)
    if count is None:
        await update.message.reply_text(
            "⚠️ Не удалось перечитать таблицу — продолжаю работать на прежних данных."
        )
        return
    await update.message.reply_text(f"🔄 Таблица перечитана: {count} клиентов.")


@restricted
async def add_idea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /idea текст предложения")
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Использование: /idea текст предложения")
        return
    author = f"@{update.effective_user.username}" if update.effective_user.username else f"id{update.effective_user.id}"
    if await ideas.add_idea(author, text):
        await update.message.reply_text("💡 Идея записана — рассмотрим её при следующем улучшении бота.")
    else:
        await update.message.reply_text(
            "⚠️ Не удалось сохранить идею в таблицу (проблема с подключением), "
            "но она осталась в логах бота — не потеряется."
        )


@restricted
async def show_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await ideas.get_recent_ideas(limit=10)
    if rows is None:
        await update.message.reply_text("⚠️ Не удалось прочитать журнал идей — таблица недоступна.")
        return
    if not rows:
        await update.message.reply_text("Пока нет ни одной записанной идеи. Добавить: /idea текст")
        return
    text = "💡 Последние идеи:\n\n"
    for row in rows:
        date = row[0] if len(row) > 0 else "—"
        author = row[1] if len(row) > 1 else "—"
        idea_text = row[2] if len(row) > 2 else "—"
        text += f"• {idea_text}\n   ({author}, {date})\n\n"
    await update.message.reply_text(text)


@restricted
async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usage = (
        "Использование: /remind @username N — напомнить через N дней в 08:00 "
        "(0 — сегодня)\n/remind @username - — снять напоминание"
    )
    if len(context.args) < 2:
        await update.message.reply_text(usage)
        return
    username = parse_username(context.args[0])
    if username not in repo:
        await update.message.reply_text("Клиент не найден.")
        return
    raw = context.args[1].strip()

    if raw == "-":
        await repo.update_field(username, "reminder_date", "")
        await update.message.reply_text(f"🔕 Напоминание для @{username} снято.")
        return

    if not raw.isdigit():
        await update.message.reply_text(usage)
        return

    days = int(raw)
    reminder_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    await repo.update_field(username, "reminder_date", reminder_date)
    await update.message.reply_text(
        f"🔔 Напоминание для @{username} установлено на {reminder_date} в 08:00 (через {days} дн.)"
    )


@restricted
async def activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /activate @username")
        return
    username = parse_username(context.args[0])
    client = repo.get(username)
    if client is None:
        await update.message.reply_text("Клиент не найден.")
        return
    await repo.update_field(username, "status", STATUS_ACTIVE)
    text = f"⭐ @{username} переведён в статус «{STATUS_ACTIVE}» и добавлен в broadcast-список."
    if not str(client.get("chat_id", "")).strip():
        text += (
            "\n\n⚠️ Клиент ещё не писал этому боту — рассылка ему станет доступна "
            "после того, как он отправит боту любое сообщение (chat_id запомнится автоматически)."
        )
    await update.message.reply_text(text)


@restricted
async def delete_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /delete @username")
        return
    username = parse_username(context.args[0])
    client = repo.get(username)
    if client is None:
        await update.message.reply_text("Клиент не найден.")
        return
    name = client.get("name") or username
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🗑 Да, удалить", callback_data=f"del:confirm:{username}"),
        InlineKeyboardButton("Отмена", callback_data=f"del:cancel:{username}"),
    ]])
    await update.message.reply_text(
        f"⚠️ Удалить клиента @{username} ({name})?\n"
        "Карточка, этап, заметки и строка в таблице будут удалены безвозвратно.\n"
        "Это действие нельзя отменить.",
        reply_markup=keyboard,
    )


@restricted
async def delete_client_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.message is None:
        await query.answer("⚠️ Сообщение устарело — вызови /delete заново", show_alert=True)
        return

    parts = (query.data or "").split(":", 2)  # del:confirm:username / del:cancel:username
    if len(parts) != 3:
        logger.warning("Некорректный callback удаления: %r", query.data)
        return
    _, action, username = parts

    if action == "cancel":
        await query.message.edit_text(f"❌ Удаление @{username} отменено.")
        return

    if action == "confirm":
        if await repo.delete_client(username):
            await query.message.edit_text(f"🗑 Клиент @{username} удалён.")
        else:
            await query.message.edit_text(f"Клиент @{username} уже не найден.")


# --- Диалог /new: пошаговое добавление клиента ---

@restricted
async def new_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_client"] = {}
    await update.message.reply_text(
        "➕ Новый клиент\n\n"
        f"Шаг 1/{NEW_TOTAL_STEPS}. Отправь @username клиента в Telegram.\n"
        "Отмена в любой момент — /cancel"
    )
    return NEW_USERNAME


async def new_client_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = parse_username(update.message.text)
    if not username:
        await update.message.reply_text("Не похоже на @username. Отправь ещё раз:")
        return NEW_USERNAME
    if username in repo:
        await update.message.reply_text(
            f"Клиент @{username} уже существует. Отправь другой @username или /cancel:"
        )
        return NEW_USERNAME
    context.user_data["new_client"]["username"] = username
    await update.message.reply_text(f"Шаг 2/{NEW_TOTAL_STEPS}. Имя клиента:")
    return NEW_NAME


async def new_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_client"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"Шаг 3/{NEW_TOTAL_STEPS}. Опыт клиента (например: новичок / есть опыт):"
    )
    return NEW_EXPERIENCE


async def new_client_experience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_client"]["experience"] = update.message.text.strip()
    await update.message.reply_text(f"Шаг 4/{NEW_TOTAL_STEPS}. Терминал (например: MT4 / MT5):")
    return NEW_TERMINAL


async def new_client_terminal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_client"]["terminal"] = update.message.text.strip()
    await update.message.reply_text(f"Шаг 5/{NEW_TOTAL_STEPS}. Депозит (например: 500):")
    return NEW_DEPOSIT


async def new_client_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_client"]["deposit"] = update.message.text.strip()
    await update.message.reply_text(
        f"Шаг 6/{NEW_TOTAL_STEPS}. Способ торговли (например: авто / полуавто):"
    )
    return NEW_FORMAT


async def new_client_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_client"]["format"] = update.message.text.strip()
    await update.message.reply_text(
        f"Шаг 7/{NEW_TOTAL_STEPS}. Есть что записать сразу? Напиши заметку "
        "или отправь «-», чтобы пропустить."
    )
    return NEW_NOTE


def _reminder_keyboard() -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(str(n), callback_data=f"newrem:{n}") for n in REMINDER_QUICK_DAYS]
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("Без напоминания", callback_data="newrem:skip")]])


async def new_client_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["new_client"]["notes"] = "" if text == "-" else text
    await update.message.reply_text(
        f"Шаг 8/{NEW_TOTAL_STEPS}. Через сколько дней напомнить об этом клиенте (в 08:00)?\n"
        "Нажми кнопку или введи своё число дней текстом. «-» — без напоминания.",
        reply_markup=_reminder_keyboard(),
    )
    return NEW_REMINDER


async def _finish_new_client(message, context: ContextTypes.DEFAULT_TYPE, reminder_days):
    """Завершает диалог /new: создаёт карточку клиента (общий код для кнопки и текста)."""
    data = context.user_data.pop("new_client", {})
    username = data.pop("username")
    if reminder_days is not None:
        data["reminder_date"] = (datetime.now() + timedelta(days=reminder_days)).strftime("%Y-%m-%d")
    else:
        data["reminder_date"] = ""

    if not await repo.add_client(username, data):
        await message.reply_text(f"Клиент @{username} уже существует.")
        return

    await message.reply_text(
        f"✅ Клиент добавлен, этап: {get_stage_name(STAGES[0])} (1/{TOTAL_STAGES})\n\n"
        + build_summary(username, repo.get(username))
    )


async def new_client_reminder_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw == "-":
        await _finish_new_client(update.message, context, None)
        return ConversationHandler.END
    if not raw.isdigit():
        await update.message.reply_text(
            "Введи число дней (например 3), нажми кнопку выше, или «-», чтобы пропустить.",
            reply_markup=_reminder_keyboard(),
        )
        return NEW_REMINDER
    await _finish_new_client(update.message, context, int(raw))
    return ConversationHandler.END


async def new_client_reminder_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = (query.data or "").split(":", 1)[-1]
    if raw == "skip":
        await _finish_new_client(query.message, context, None)
    elif raw.isdigit():
        await _finish_new_client(query.message, context, int(raw))
    else:
        logger.warning("Некорректный callback напоминания в /new: %r", query.data)
        return NEW_REMINDER
    return ConversationHandler.END


@restricted
async def new_client_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_client", None)
    await update.message.reply_text("❌ Добавление клиента отменено.")
    return ConversationHandler.END


def build_new_client_handler() -> ConversationHandler:
    """Диалог /new: username -> имя -> опыт -> терминал -> депозит -> формат -> заметка -> напоминание."""
    text_input = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("new", new_client_start)],
        states={
            NEW_USERNAME: [MessageHandler(text_input, new_client_username)],
            NEW_NAME: [MessageHandler(text_input, new_client_name)],
            NEW_EXPERIENCE: [MessageHandler(text_input, new_client_experience)],
            NEW_TERMINAL: [MessageHandler(text_input, new_client_terminal)],
            NEW_DEPOSIT: [MessageHandler(text_input, new_client_deposit)],
            NEW_FORMAT: [MessageHandler(text_input, new_client_format)],
            NEW_NOTE: [MessageHandler(text_input, new_client_note)],
            NEW_REMINDER: [
                CallbackQueryHandler(new_client_reminder_button, pattern=r"^newrem:"),
                MessageHandler(text_input, new_client_reminder_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", new_client_cancel)],
    )


# --- Диалог /broadcast: рассылка по реальным клиентам ---

def build_broadcast_keyboard(state: dict) -> InlineKeyboardMarkup:
    """Список получателей с чекбоксами + кнопки управления."""
    keyboard = []
    for username, client in state["clients"]:
        mark = "✅" if username in state["selected"] else "⬜"
        no_chat = "" if str(client.get("chat_id", "")).strip() else " (не писал боту)"
        label = f"{mark} @{username} — {client.get('name') or '—'}{no_chat}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"bc:t:{username}")])
    keyboard.append([
        InlineKeyboardButton("Выбрать всех", callback_data="bc:all"),
        InlineKeyboardButton(f"📤 Отправить ({len(state['selected'])})", callback_data="bc:send"),
    ])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="bc:cancel")])
    return InlineKeyboardMarkup(keyboard)


@restricted
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not repo.broadcast_list():
        await update.message.reply_text(
            "Broadcast-список пуст.\n"
            "Сначала переведи клиентов в статус «реальный»: /activate @username"
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "📣 Рассылка по реальным клиентам\n\n"
        "Отправь текст сообщения.\n"
        "Отмена — /cancel"
    )
    return BROADCAST_TEXT


async def broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Пустое сообщение. Отправь текст ещё раз:")
        return BROADCAST_TEXT
    state = {"text": text, "clients": repo.broadcast_list(), "selected": set()}
    context.user_data["broadcast"] = state
    await update.message.reply_text(
        f"Сообщение:\n\n{text}\n\nВыбери получателей и нажми «Отправить»:",
        reply_markup=build_broadcast_keyboard(state),
    )
    return ConversationHandler.END


@restricted
async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("broadcast", None)
    await update.message.reply_text("❌ Рассылка отменена.")
    return ConversationHandler.END


def build_broadcast_handler() -> ConversationHandler:
    """Собирает диалог /broadcast: текст сообщения -> список с чекбоксами."""
    text_input = filters.TEXT & ~filters.COMMAND
    return ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_TEXT: [MessageHandler(text_input, broadcast_text)],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )


@restricted
async def broadcast_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    state = context.user_data.get("broadcast")

    if state is None:
        await query.answer("Рассылка не активна — вызови /broadcast", show_alert=True)
        return

    if query.message is None:
        await query.answer("⚠️ Список устарел — вызови /broadcast заново", show_alert=True)
        return

    parts = query.data.split(":", 2)  # bc:t:username / bc:all / bc:send / bc:cancel
    action = parts[1] if len(parts) > 1 else ""

    if action == "t" and len(parts) == 3:
        username = parts[2]
        if username in state["selected"]:
            state["selected"].discard(username)
        else:
            state["selected"].add(username)
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=build_broadcast_keyboard(state))

    elif action == "all":
        state["selected"] = {u for u, _ in state["clients"]}
        await query.answer()
        await query.message.edit_reply_markup(reply_markup=build_broadcast_keyboard(state))

    elif action == "cancel":
        context.user_data.pop("broadcast", None)
        await query.answer()
        await query.message.edit_text("❌ Рассылка отменена.")

    elif action == "send":
        if not state["selected"]:
            await query.answer("Не выбран ни один получатель", show_alert=True)
            return
        await query.answer()
        context.user_data.pop("broadcast", None)

        clients_map = dict(state["clients"])
        sent, failed = [], []
        for username in sorted(state["selected"]):
            chat_id = str(clients_map.get(username, {}).get("chat_id", "")).strip()
            if not chat_id:
                failed.append(f"@{username} — клиент ещё не писал боту")
                continue
            try:
                await context.bot.send_message(chat_id=int(chat_id), text=state["text"])
                sent.append(f"@{username}")
                logger.info("Рассылка доставлена @%s", username)
            except Exception as e:
                logger.warning("Рассылка не доставлена @%s: %s", username, e)
                failed.append(f"@{username} — ошибка отправки")

        report = f"📣 Рассылка завершена\n\n✅ Доставлено: {len(sent)}"
        if sent:
            report += "\n" + ", ".join(sent)
        if failed:
            report += f"\n\n⚠️ Не доставлено: {len(failed)}\n" + "\n".join(failed)
        await query.message.edit_text(report)


# --- Автозапоминание chat_id клиентов ---

async def track_client_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Если клиент из базы написал боту — запоминаем его chat_id для рассылок."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or user.id in config.ALLOWED_USERS:
        return
    username = (user.username or "").lower()
    if not username or username not in repo:
        return
    client = repo.get(username)
    if str(client.get("chat_id", "")).strip() != str(chat.id):
        await repo.update_field(username, "chat_id", str(chat.id))
        logger.info("Запомнен chat_id клиента @%s", username)


# --- Обработчик inline-кнопок (/today) ---

@restricted
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if "_" not in data:
        logger.warning("Некорректный callback: %r", data)
        return
    action, username = data.split("_", 1)

    if action not in CALLBACK_ACTIONS:
        logger.warning("Неизвестное действие callback: %r", data)
        return

    if query.message is None:
        # Сообщение с кнопками слишком старое — Telegram уже не даёт на него ответить
        await query.answer("⚠️ Список устарел — вызови /today заново", show_alert=True)
        return

    client = repo.get(username)
    if client is None:
        await query.message.reply_text("Клиент не найден.")
        return

    name = client.get("name", username)
    stage = client.get("stage")

    if action in ("short", "full"):
        msg = format_template(stage, style=action, name=name, deposit=client.get("deposit", ""))
        await query.message.reply_text(f"📤 Шаблон для @{username}:\n\n{msg}\n\nСкопируй и отправь клиенту.")
        await repo.mark_contact(username)
        await query.message.reply_text("✅ Контакт отмечен.")

    elif action == "contacted":
        await repo.mark_contact(username)
        await query.message.reply_text(f"✅ Контакт с @{username} отмечен.")


# --- Фоновые задачи ---

async def auto_reload(context: ContextTypes.DEFAULT_TYPE):
    """Периодически перечитывает таблицу — правки менеджера подхватываются сами."""
    count = await asyncio.to_thread(repo.load)
    if count is None:
        logger.warning("Авто-перечитывание: таблица недоступна, работаем на прежних данных")


# --- Ежедневное напоминание ---

async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет операторам дайджест срочных задач (то же, что /today)."""
    text, markup = await build_today_digest()
    if text is None:
        logger.info("Ежедневное напоминание: срочных задач нет, ничего не отправлено")
        return
    for operator_id in config.ALLOWED_USERS:
        try:
            await context.bot.send_message(chat_id=operator_id, text=f"⏰ {text}", reply_markup=markup)
            logger.info("Напоминание отправлено оператору %s", operator_id)
        except Exception:
            logger.exception("Не удалось отправить напоминание оператору %s", operator_id)


# --- Глобальный обработчик ошибок ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Необработанная ошибка", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Произошла ошибка. Попробуй ещё раз.")
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")


# --- Запуск ---

def main():
    config.setup_logging()

    errors = config.validate_config()
    if errors:
        for err in errors:
            logger.critical("Конфигурация: %s", err)
        sys.exit(1)

    logger.info("Бот RoboCompanion v2 запускается...")
    repo.load()

    application = Application.builder().token(config.BOT_TOKEN).build()

    application.add_handler(build_new_client_handler())
    application.add_handler(build_broadcast_handler())
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("clients", clients_list))
    application.add_handler(CommandHandler("search", search_client))
    application.add_handler(CommandHandler("note", note))
    application.add_handler(CommandHandler("notes", show_notes))
    application.add_handler(CommandHandler("done", done))
    application.add_handler(CommandHandler("setstage", setstage))
    application.add_handler(CommandHandler("contacted", contacted))
    application.add_handler(CommandHandler("complete", complete))
    application.add_handler(CommandHandler("edit", edit_client))
    application.add_handler(CommandHandler("activate", activate))
    application.add_handler(CommandHandler("reload", reload_clients))
    application.add_handler(CommandHandler("delete", delete_client_start))
    application.add_handler(CommandHandler("idea", add_idea))
    application.add_handler(CommandHandler("ideas", show_ideas))
    application.add_handler(CommandHandler("remind", remind))
    application.add_handler(CallbackQueryHandler(broadcast_button, pattern=r"^bc:"))
    application.add_handler(CallbackQueryHandler(delete_client_button, pattern=r"^del:"))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.ALL, track_client_chat), group=1)
    application.add_error_handler(error_handler)

    reminder_time = config.parse_reminder_time()
    if application.job_queue and reminder_time:
        application.job_queue.run_daily(daily_reminder, time=reminder_time)
        logger.info("Ежедневное напоминание запланировано на %s", config.REMINDER_TIME)
    elif not reminder_time:
        logger.warning("REMINDER_TIME не задан или некорректен (%r) — напоминания отключены", config.REMINDER_TIME)
    else:
        logger.warning("JobQueue недоступен — напоминания отключены")

    if application.job_queue and config.RELOAD_INTERVAL_MINUTES > 0:
        interval = config.RELOAD_INTERVAL_MINUTES * 60
        application.job_queue.run_repeating(auto_reload, interval=interval, first=interval)
        logger.info("Авто-перечитывание таблицы каждые %d мин", config.RELOAD_INTERVAL_MINUTES)
    else:
        logger.info("Авто-перечитывание таблицы отключено (RELOAD_INTERVAL_MINUTES=%s)", config.RELOAD_INTERVAL_MINUTES)

    application.run_polling()


if __name__ == "__main__":
    main()
