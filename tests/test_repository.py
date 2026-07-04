# test_repository.py - Тесты слоя данных (загрузка, запись, ретраи)

import asyncio

import repository
from tests.conftest import FakeSheet


def test_load_parses_clients(repo):
    assert len(repo) == 2
    ivan = repo.get("ivan")
    assert ivan["name"] == "Иван"
    assert ivan["stage"] == "материалы"
    assert ivan["stage_index"] == 0
    assert ivan["deposit"] == "500"
    assert ivan["last_contact"].strftime("%Y-%m-%d %H:%M") == "2026-07-01 10:00"


def test_load_handles_empty_last_contact(repo):
    # у Петра дата пустая — подставляется fallback (2 дня назад), бот не падает
    assert repo.get("petr")["last_contact"] is not None


def test_add_note_persists_to_sheet(repo, fake_sheet):
    ok = asyncio.run(repo.add_note("ivan", "созвон прошёл"))
    assert ok
    assert "• созвон прошёл" in repo.get("ivan")["notes"]
    assert "• созвон прошёл" in fake_sheet.rows[1][7]


def test_add_note_unknown_client(repo):
    assert not asyncio.run(repo.add_note("nobody", "текст"))


def test_mark_contact_persists(repo, fake_sheet):
    assert asyncio.run(repo.mark_contact("petr"))
    assert fake_sheet.rows[2][9] != ""


def test_set_stage_persists(repo, fake_sheet):
    assert asyncio.run(repo.set_stage("ivan", "риск_контроль"))
    assert repo.get("ivan")["stage_index"] == 4
    assert fake_sheet.rows[1][6] == "риск_контроль"


def test_update_field_persists(repo, fake_sheet):
    assert asyncio.run(repo.update_field("ivan", "deposit", "1500"))
    assert fake_sheet.rows[1][4] == "1500"


def test_update_field_rejects_unknown_field(repo):
    assert not asyncio.run(repo.update_field("ivan", "city", "Москва"))


def test_add_client_appends_row(repo, fake_sheet):
    ok = asyncio.run(repo.add_client("maria", {
        "name": "Мария", "experience": "новичок", "terminal": "MT5",
        "deposit": "500", "format": "авто",
    }))
    assert ok
    assert repo.get("maria")["stage"] == "материалы"
    new_row = fake_sheet.rows[-1]
    assert new_row[0] == "@maria"
    assert new_row[1] == "Мария"
    assert new_row[6] == "материалы"
    assert new_row[8] != ""  # дата_старта проставлена


def test_add_client_rejects_duplicate(repo):
    assert not asyncio.run(repo.add_client("ivan", {"name": "Дубль"}))


def test_delete_client_removes_from_memory_and_sheet(repo, fake_sheet):
    ok = asyncio.run(repo.delete_client("ivan"))
    assert ok
    assert "ivan" not in repo
    assert len(repo) == 1
    # строка Ивана (2-я) удалена, Пётр остался единственной строкой данных
    assert len(fake_sheet.rows) == 2
    assert fake_sheet.rows[1][0] == "@petr"


def test_delete_client_unknown_returns_false(repo, fake_sheet):
    assert not asyncio.run(repo.delete_client("nobody"))
    assert len(fake_sheet.rows) == 3  # ничего не изменилось


def test_delete_client_shifts_other_rows_correctly(repo, fake_sheet):
    # добавляем третьего клиента, удаляем первого — строки должны сдвинуться,
    # а поиск по username подхватить сдвиг благодаря _find_row
    asyncio.run(repo.add_client("maria", {"name": "Мария"}))
    asyncio.run(repo.delete_client("ivan"))
    assert "petr" in repo and "maria" in repo
    # после удаления первой строки данных строки Петра и Марии сдвинулись на 1 вверх;
    # проверяем, что запись по username всё ещё попадает в правильную строку
    asyncio.run(repo.mark_contact("maria"))
    maria_row = next(row for row in fake_sheet.rows if row[0] == "@maria")
    assert maria_row[9] != ""  # last_contact записан в правильную (сдвинутую) строку


def test_delete_client_survives_transient_failures(monkeypatch):
    sheet = FakeSheet(failures=2)
    monkeypatch.setattr(repository, "get_sheet", lambda: sheet)
    repo = repository.ClientRepository()
    repo.load()
    assert asyncio.run(repo.delete_client("ivan"))
    assert len(sheet.rows) == 2


def test_delete_client_total_failure_keeps_memory_consistent(monkeypatch):
    sheet = FakeSheet(failures=999)
    monkeypatch.setattr(repository, "get_sheet", lambda: sheet)
    repo = repository.ClientRepository()
    repo.load()
    # запись в таблицу не удалась, но из памяти клиент всё равно должен исчезнуть —
    # иначе бот покажет клиента как существующего, а таблица будет главным источником правды
    assert asyncio.run(repo.delete_client("ivan"))
    assert "ivan" not in repo
    assert len(sheet.rows) == 3  # строка в таблице осталась (сбой записи)


def test_write_survives_transient_failures(monkeypatch):
    # первые 2 записи падают — третья попытка должна пройти
    sheet = FakeSheet(failures=2)
    monkeypatch.setattr(repository, "get_sheet", lambda: sheet)
    repo = repository.ClientRepository()
    repo.load()
    assert asyncio.run(repo.add_note("ivan", "тест ретраев"))
    assert "• тест ретраев" in sheet.rows[1][7]


def test_total_api_failure_keeps_change_in_memory(monkeypatch):
    sheet = FakeSheet(failures=999)
    monkeypatch.setattr(repository, "get_sheet", lambda: sheet)
    repo = repository.ClientRepository()
    repo.load()
    # исключений нет, изменение живёт в памяти
    assert asyncio.run(repo.add_note("ivan", "офлайн-заметка"))
    assert "• офлайн-заметка" in repo.get("ivan")["notes"]


def test_row_shift_is_detected(repo, fake_sheet):
    # менеджер вставил строку сверху — запись должна попасть в правильную строку
    fake_sheet.rows.insert(1, ["@new_one", "Новый", "", "", "", "", "материалы", "", "", ""])
    asyncio.run(repo.set_stage("petr", "тестер"))
    assert fake_sheet.rows[3][6] == "тестер"      # строка Петра (сдвинутая)
    assert fake_sheet.rows[1][6] == "материалы"   # чужая строка не тронута


def test_reload_picks_up_manual_edits(repo, fake_sheet):
    # менеджер руками переименовал клиента и добавил нового
    fake_sheet.rows[1][1] = "Иван Иванов"
    fake_sheet.rows.append(["@new_guy", "Новый", "", "", "", "", "материалы", "", "", "", "", ""])
    count = repo.load()
    assert count == 3
    assert repo.get("ivan")["name"] == "Иван Иванов"
    assert "new_guy" in repo


def test_reload_failure_keeps_old_data(repo, monkeypatch):
    monkeypatch.setattr(repository, "get_sheet", lambda: None)
    assert repo.load() is None
    assert len(repo) == 2  # старые данные не стёрты
    assert repo.get("ivan")["name"] == "Иван"


def test_reload_preserves_memory_only_fields(monkeypatch):
    # таблица БЕЗ столбцов последний_контакт/статус/chat_id —
    # эти поля живут в памяти и не должны теряться при перечитывании
    headers = ["username", "имя", "опыт", "терминал", "депозит",
               "формат_торговли", "текущий_этап", "заметки", "дата_старта"]
    sheet = FakeSheet(rows=[
        headers,
        ["@ivan", "Иван", "новичок", "MT5", "500", "авто", "материалы", "", "2026-06-30"],
    ])
    monkeypatch.setattr(repository, "get_sheet", lambda: sheet)
    repo = repository.ClientRepository()
    repo.load()

    asyncio.run(repo.update_field("ivan", "chat_id", "777"))
    asyncio.run(repo.update_field("ivan", "status", "реальный"))
    asyncio.run(repo.mark_contact("ivan"))
    contact_before = repo.get("ivan")["last_contact"]

    assert repo.load() == 1  # перечитали таблицу
    ivan = repo.get("ivan")
    assert ivan["chat_id"] == "777"
    assert ivan["status"] == "реальный"
    assert ivan["last_contact"] == contact_before


def test_missing_column_does_not_crash(monkeypatch):
    # в таблице нет столбца «последний_контакт» — контакт хранится только в памяти
    headers = ["username", "имя", "опыт", "терминал", "депозит",
               "формат_торговли", "текущий_этап", "заметки", "дата_старта"]
    sheet = FakeSheet(rows=[
        headers,
        ["@ivan", "Иван", "новичок", "MT5", "500", "авто", "материалы", "", "2026-06-30"],
    ])
    monkeypatch.setattr(repository, "get_sheet", lambda: sheet)
    repo = repository.ClientRepository()
    repo.load()
    assert asyncio.run(repo.mark_contact("ivan"))
    assert repo.get("ivan")["last_contact"] is not None
