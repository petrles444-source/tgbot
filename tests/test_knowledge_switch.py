"""Выключатель базы знаний: работает и выключает по-настоящему.

Отдельный файл, потому что проверяет не поиск, а настройку: сломанный
выключатель заметен только тем, что база продолжает работать, когда
её выключили. Никакой ошибки, никакого сообщения — просто человек
решил, что отключил, а это не так.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOTS = ROOT / "bots" / "код"
if str(BOTS) not in sys.path:
    sys.path.insert(0, str(BOTS))

import ada_bot  # noqa: E402
import knowledge  # noqa: E402


def test_по_умолчанию_база_включена() -> None:
    """Нет настройки — база работает.

    Иначе бот молчал бы о своей системе после первого же обновления
    конфигурации: ключа нет, значит выключено, человек ничего не
    настраивал и ничего не получал.
    """
    assert ada_bot.knowledge_enabled({}) is True
    assert ada_bot.knowledge_enabled({"knowledge": {}}) is True


@pytest.mark.parametrize("настройка", [True, {"enabled": True}])
def test_явно_включено(настройка: object) -> None:
    assert ada_bot.knowledge_enabled({"knowledge": настройка}) is True


@pytest.mark.parametrize("настройка", [False, {"enabled": False}])
def test_явно_выключено(настройка: object) -> None:
    assert ada_bot.knowledge_enabled({"knowledge": настройка}) is False


def test_выключенная_база_не_даёт_заметку() -> None:
    """Главное: выключатель должен убирать заметку, а не только врать.

    Проверка самого флага бесполезна: он может возвращать `false`,
    а заметка всё равно уйдёт в запрос, если где-то ещё стоит вызов
    без проверки. Поэтому смотрим на результат целиком — так же,
    как это делает `handle()`.
    """
    config = {"knowledge": {"enabled": False}, "char_key": "ada"}
    заметка = ""
    if ada_bot.knowledge_enabled(config):
        заметка = knowledge.заметка_знаний("ada", 1, "что такое токен")
    assert заметка == "", "база выключена, а заметка всё равно собралась"


def test_включённая_база_даёт_заметку() -> None:
    """Обратная сторона: включённая база действительно отвечает."""
    config = {"knowledge": {"enabled": True}, "char_key": "ada"}
    assert ada_bot.knowledge_enabled(config) is True
    assert knowledge.заметка_знаний("ada", 1, "что такое токен"), (
        "база включена, но на точный вопрос не ответила")


def test_выключатель_читает_настоящий_файл_настроек() -> None:
    """Ключ из `config.json` должен читаться как задумано.

    Настройку легко положить не туда: внутрь первого провайдера или
    под именем, которого код не ждёт. Тогда ключ молча проигнорируется,
    и выключатель будет «работать», ничего не делая.
    """
    import json

    config_path = ROOT / "bots" / "config.json"
    assert config_path.is_file(), "нет файла настроек"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "knowledge" in data, "в config.json нет раздела knowledge"
    assert "enabled" in data["knowledge"], (
        "в разделе knowledge нет ключа enabled")
    assert ada_bot.knowledge_enabled(data) is True, (
        "база выключена в настройках — этого не ждали")