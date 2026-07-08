# test_ideas.py - Тесты журнала идей (/idea, /ideas)

import asyncio

import ideas
from tests.conftest import FakeSheet


def _empty_ideas_sheet(**kwargs):
    return FakeSheet(rows=[["дата", "автор", "текст"]], **kwargs)


def test_add_idea_appends_row(monkeypatch):
    sheet = _empty_ideas_sheet()
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: sheet)
    ok = asyncio.run(ideas.add_idea("Иван", "Добавить кнопку X"))
    assert ok
    assert sheet.rows[-1][1] == "Иван"
    assert sheet.rows[-1][2] == "Добавить кнопку X"
    assert sheet.rows[-1][0]  # дата проставлена


def test_add_idea_survives_transient_failures(monkeypatch):
    sheet = _empty_ideas_sheet(failures=2)
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: sheet)
    ok = asyncio.run(ideas.add_idea("Иван", "Идея после сбоев"))
    assert ok
    assert sheet.rows[-1][2] == "Идея после сбоев"


def test_add_idea_total_failure_does_not_crash(monkeypatch):
    sheet = _empty_ideas_sheet(failures=999)
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: sheet)
    ok = asyncio.run(ideas.add_idea("Иван", "Потерянная идея"))
    assert not ok  # честно сообщаем, что не сохранилось, но исключений нет


def test_add_idea_sheet_unavailable(monkeypatch):
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: None)
    assert not asyncio.run(ideas.add_idea("Иван", "Без таблицы"))


def test_get_recent_ideas_returns_last_n(monkeypatch):
    sheet = FakeSheet(rows=[
        ["дата", "автор", "текст"],
        ["2026-07-01 10:00", "Иван", "Идея 1"],
        ["2026-07-02 10:00", "Пётр", "Идея 2"],
        ["2026-07-03 10:00", "Иван", "Идея 3"],
    ])
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: sheet)
    rows = asyncio.run(ideas.get_recent_ideas(limit=2))
    assert rows == [
        ["2026-07-02 10:00", "Пётр", "Идея 2"],
        ["2026-07-03 10:00", "Иван", "Идея 3"],
    ]


def test_get_recent_ideas_empty(monkeypatch):
    sheet = _empty_ideas_sheet()
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: sheet)
    assert asyncio.run(ideas.get_recent_ideas()) == []


def test_get_recent_ideas_sheet_unavailable(monkeypatch):
    monkeypatch.setattr(ideas, "get_ideas_sheet", lambda: None)
    assert asyncio.run(ideas.get_recent_ideas()) is None
