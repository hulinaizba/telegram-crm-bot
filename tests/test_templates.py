# test_templates.py - Тесты шаблонов сообщений

from stages import STAGES
from templates import TEMPLATES, format_template, get_template


def test_every_stage_has_short_and_full_template():
    for stage in STAGES:
        assert stage in TEMPLATES
        assert TEMPLATES[stage]["short"]
        assert TEMPLATES[stage]["full"]


def test_format_substitutes_name():
    msg = format_template("материалы", "full", name="Иван", deposit="500")
    assert "Иван" in msg
    assert "{name}" not in msg


def test_unknown_stage_falls_back_to_first():
    assert get_template("несуществующий", "full") == TEMPLATES["материалы"]["full"]


def test_missing_variables_do_not_crash():
    # шаблон с {name}, но подставляем пустой набор — возвращается как есть
    msg = format_template("материалы", "full")
    assert msg  # не упало, вернуло текст
