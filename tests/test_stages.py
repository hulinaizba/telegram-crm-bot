# test_stages.py - Тесты логики этапов

from stages import (
    STAGES,
    TOTAL_STAGES,
    get_next_stage,
    get_progress,
    get_stage_index,
    get_stage_name,
    is_last_stage,
)


def test_stage_order_is_fixed():
    assert STAGES == [
        "материалы",
        "проверка_ознакомления",
        "проверка_запуска",
        "понимание_логики",
        "риск_контроль",
        "тестер",
        "недельный_контроль",
    ]
    assert TOTAL_STAGES == 7


def test_get_next_stage_moves_forward():
    assert get_next_stage("материалы") == "проверка_ознакомления"
    assert get_next_stage("тестер") == "недельный_контроль"


def test_last_stage_does_not_advance():
    assert get_next_stage("недельный_контроль") == "недельный_контроль"
    assert is_last_stage("недельный_контроль")
    assert not is_last_stage("материалы")


def test_unknown_stage_falls_back_to_first():
    assert get_next_stage("несуществующий") == STAGES[0]
    assert get_stage_index("несуществующий") == 0


def test_progress_format():
    assert get_progress("материалы") == "1/7"
    assert get_progress("недельный_контроль") == "7/7"


def test_stage_name_has_display_value():
    assert get_stage_name("материалы") == "📚 Материалы"
    assert get_stage_name("нечто_новое") == "Нечто_новое"
