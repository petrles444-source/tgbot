"""Память о собеседнике: помнит, не путает, не раздувает.

Проверяется без сети и без модели: память живёт на диске и работает
словами, а не запросами. Настоящая память при этом не трогается —
тесты пишут во временную папку, иначе после прогона на диске
остался бы выдуманный собеседник.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOTS = ROOT / "bots" / "код"
if str(BOTS) not in sys.path:
    sys.path.insert(0, str(BOTS))

import memory  # noqa: E402

CHAT = 424_242


@pytest.fixture(autouse=True)
def temp_memory(tmp_path: Path) -> Any:
    """Настоящая память не трогается: подменяем папку."""
    memory.MEMORY_ROOT = tmp_path / "память"
    yield tmp_path
    memory.MEMORY_ROOT = ROOT / "bots" / "память"


# ================================================== что попадает в память


@pytest.mark.parametrize("text", [
    "меня зовут Иван",
    "я работаю над телеграм-ботами",
    "мне нравится питон",
    "люблю вариант 3 в проекте",
])
def test_факты_запоминаются(text: str) -> None:
    """То, что человек сказал о себе, помним."""
    assert memory.remember("ada", CHAT, text, "Иван"), (
        f"факт «{text}» не запомнился")


@pytest.mark.parametrize("text", [
    "а что я делаю?",
    "что ты умеешь?",
    "почему не работает?",
])
def test_вопросы_не_запоминаются(text: str) -> None:
    """Вопрос — не факт.

    Слова-признаки личных фактов ловят и вопросы: в «а что я делаю»
    есть «делаю». Без этой проверки память забивалась бы вопросами,
    и настоящие сведения о человеке тонули бы в них.
    """
    assert not memory.remember("ada", CHAT, text, "Иван"), (
        f"вопрос «{text}» попал в память как факт")


@pytest.mark.parametrize("text", ["ок", "да", "спасибо", "ага", "ага, понял"])
def test_мусор_не_запоминается(text: str) -> None:
    """«Ок» и «ага» памятью быть не могут.

    Запомнив их, через месяц в памяти не осталось бы ничего
    полезного: она сплошь состояла бы из междометий.
    """
    assert not memory.remember("ada", CHAT, text, "Иван"), (
        f"мусор «{text}» попал в память")


def test_повтор_не_плодит_дубли() -> None:
    """Сказанное дважды — всё ещё один факт."""
    memory.remember("ada", CHAT, "меня зовут Иван", "Иван")
    memory.remember("ada", CHAT, "меня зовут Иван", "Иван")
    assert len(memory.load("ada", CHAT)["facts"]) == 1


def test_память_не_растёт_без_предела() -> None:
    """Долгий разговор не должен съесть всю память."""
    for number in range(memory.MAX_FACTS + 40):
        memory.remember("ada", CHAT, f"я помню номер {number}", "Иван")
    facts = memory.load("ada", CHAT)["facts"]
    assert len(facts) <= memory.MAX_FACTS, (
        f"фактов {len(facts)}, лимит {memory.MAX_FACTS}: память "
        "растёт без границы и рано или поздно перестанет отвечать")


# ================================================= изоляция между ботами


def test_у_каждого_бота_своя_память() -> None:
    """Сказанное Аде не достаётся Анатолию.

    Решение человека: память своя у каждого. Общая память означала
    бы, что Ада «знает» о разговоре, которого с ней не было.
    """
    # Фразы-факты, а не «факт для Ады»: там ни глагола, ни признака,
    # и память их не держит. Проверка изоляции должна проверять
    # изоляцию, а не отсев признаков.
    memory.remember("ada", CHAT, "я купил велосипед", "Иван")

    ada = memory.recall("ada", CHAT, "что помнишь")
    anatoly = memory.recall("anatoly", CHAT, "что помнишь")

    assert any("велосипед" in line for line in ada), (
        "Ада не помнит то, что ей сказали")
    assert not any("велосипед" in line for line in anatoly), (
        "Анатолий узнал то, что говорили Аде: память не изолирована")


def test_памяти_лежат_в_разных_папках() -> None:
    """Файлы раздельны — иначе изоляция держалась бы на одном пути."""
    memory.remember("ada", CHAT, "я купил велосипед", "Иван")
    memory.remember("anatoly", CHAT, "я переехал в другой город",
                    "Иван")

    files = {p.parent.name for p in memory.MEMORY_ROOT.rglob("*.json")}
    assert files == {"ada", "anatoly"}, (
        f"файлы памяти лежат в {files}, а должны быть в разных папках")


# ================================================= сколько уходит в запрос


def test_в_запрос_идёт_немного() -> None:
    """Главное свойство памяти: помнить много, отдавать мало.

    Если в запрос уйдёт вся память, она сама станет тем длинным
    контекстом, ради которого её и делали: запрос будет дорогим,
    а модель — медленной.
    """
    for number in range(60):
        memory.remember("ada", CHAT, f"я работаю над задачей {number}",
                        "Иван")

    note = memory.memory_note("ada", CHAT, "что помнишь")
    lines = [line for line in note.splitlines() if line.strip().startswith("—")]

    assert note, "заметка памяти пустая при полной памяти"
    assert len(lines) <= 6, (
        f"в заметке {len(lines)} строк, а уходить должно не больше "
        f"{memory.RECALL_LINES}: память раздувает запрос")


def test_подбор_идёт_по_теме() -> None:
    """На вопрос по одной теме приходит память по ней же."""
    memory.remember("ada", CHAT, "я работаю над телеграм-ботами", "Иван")
    memory.remember("ada", CHAT, "люблю ездить на велосипеде", "Иван")

    about_bots = memory.recall("ada", CHAT, "что ты знаешь про ботов?")
    assert any("ботами" in line for line in about_bots), (
        "на вопрос про ботов пришла память про велосипед")


def test_пустая_память_не_шумит() -> None:
    """Пока нечего помнить, заметки в запросе нет вовсе."""
    assert memory.memory_note("ada", CHAT, "привет") == ""


# ================================================= забывание


def test_забыть_чистит_память() -> None:
    """«Забыть разговор» должен стереть память на диске.

    Иначе после перезапуска бот продолжил бы «помнить» то, что
    человек забыл: файл на диске переживает перезапуск.
    """
    memory.remember("ada", CHAT, "меня зовут Иван", "Иван")
    assert memory.load("ada", CHAT)["facts"], "память должна была записаться"

    memory.forget("ada", CHAT)
    assert memory.load("ada", CHAT)["facts"] == [], (
        "после забытия память осталась на диске")
    assert not memory.path_for("ada", CHAT).exists(), (
        "файл памяти не удалён")


def test_битая_память_не_роняет_бота() -> None:
    """Испорченный файл — не повод для падения.

    Человек теряет накопленное, но бот продолжает работать: потеря
    памяти не должна стоить работающего помощника.
    """
    path = memory.path_for("ada", CHAT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("это не json вовсе", encoding="utf-8")

    data = memory.load("ada", CHAT)
    assert data["facts"] == [], "битая память должна читаться как пустая"
    assert memory.memory_note("ada", CHAT, "привет") == ""
