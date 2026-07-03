# stages.py - Центральное управление всеми этапами клиента

STAGES = [
    "материалы",
    "проверка_ознакомления",
    "проверка_запуска",
    "понимание_логики",
    "риск_контроль",
    "тестер",
    "недельный_контроль"
]

# Красивые названия для отображения пользователю
STAGE_NAMES = {
    "материалы": "📚 Материалы",
    "проверка_ознакомления": "📖 Проверка ознакомления",
    "проверка_запуска": "🚀 Проверка запуска",
    "понимание_логики": "🧠 Понимание логики",
    "риск_контроль": "⚠️ Риск-контроль",
    "тестер": "📊 Тестирование",
    "недельный_контроль": "📅 Недельный контроль"
}

TOTAL_STAGES = len(STAGES)

def get_stage_name(stage_key: str) -> str:
    """Возвращает красивое название этапа"""
    return STAGE_NAMES.get(stage_key, stage_key.capitalize())

def get_stage_index(stage_key: str) -> int:
    """Возвращает номер этапа (0-based)"""
    try:
        return STAGES.index(stage_key)
    except ValueError:
        return 0

def get_next_stage(current_stage: str) -> str:
    """Возвращает следующий этап"""
    try:
        idx = STAGES.index(current_stage)
        if idx < TOTAL_STAGES - 1:
            return STAGES[idx + 1]
        return current_stage  # последний этап
    except ValueError:
        return STAGES[0]

def is_last_stage(stage_key: str) -> bool:
    """Проверяет, является ли этап последним"""
    return stage_key == STAGES[-1]

def get_progress(stage_key: str) -> str:
    """Возвращает прогресс в формате 3/7"""
    idx = get_stage_index(stage_key)
    return f"{idx + 1}/{TOTAL_STAGES}"
