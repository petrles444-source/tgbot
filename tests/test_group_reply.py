"""В беседе отвечает только один бот из трёх.

Почему это отдельный файл
------------------------
Боты работают тремя потоками в одном процессе и в беседе видят
одно и то же сообщение. Пока защиты не было, на реплику приходило
три одинаковых ответа — на скриншоте видно два «Привет. Ну как
ты? Рассказывай.» подряд.

Проверяемо настоящими потоками, потому что смысл защиты именно в
гонке: если бы мы звали `handle()` по очереди из одного потока,
гонки бы не было и проверка ничего не значила.

Что проверяется
---------------
* на одно сообщение в беседе — ровно один ответ;
* на следующее сообщение — снова ровно один, а не ноль;
* в личном чате отвечают все: там каждый бот нужен сам по себе.
"""

import threading
from typing import Any

import persona
import pytest

from conftest import load_bot

ada = load_bot("ada_bot")

CONFIG: dict[str, Any] = {
    "token": "t",
    "providers": [{"name": "p", "base_url": "u", "model": "m",
                   "api_key": "k"}],
    "active": 0,
    "persona": "persona",
    "char_key": "ada",
    "heartbeat_chat_id": "",
    "face": persona.PERSONAS[0],
    "detail_level": f"{persona.DETAIL_LEVEL} из 10",
    "science_level": f"{persona.SCIENCE_LEVEL} из 10",
}

_next_id = 0


def message(chat_id: int, text: str, *, private: bool = False,
            mid: int = 0) -> dict[str, Any]:
    """Сообщение телеграма с номером.

    Номер задаётся явно: от него зависит защита от дублей, и без
    него проверки на «три бота, одна реплика» ничего не значили бы.
    """
    global _next_id
    if not mid:
        _next_id += 1
        mid = _next_id
    return {
        "message_id": mid,
        "chat": {"id": chat_id, "type": "private" if private else "group"},
        "from": {"id": 7, "is_bot": False, "first_name": "Иван"},
        "text": text,
    }


@pytest.fixture(autouse=True)
def clean() -> list:
    """Чистое состояние: пустая память чатов и ответов отвеченного."""
    ada.CHATS.clear()
    ada.OWN_ID["id"] = None
    ada._ANSWERED.clear()
    sent: list[tuple[int, str]] = []
    ada.send = lambda token, chat, text: sent.append((chat, text))
    ada.ask_model = lambda provider, prompt, system="", key="": \
        "ответ модели"
    return sent


# ======================================================= защита от дублей


def test_в_беседе_отвечает_один_из_трёх(clean: list) -> None:
    """Три потока, одно сообщение — один ответ.

    Именно потоки, а не последовательные вызовы: защита работает с
    общим словарём и блокировкой, и по очереди гонки не видно.
    """
    incoming = {
        "message_id": 42,
        "chat": {"id": 777, "type": "group"},
        "from": {"id": 7, "is_bot": False, "first_name": "Иван"},
        "text": "давай поиграем",
    }

    threads = []
    for name in ("Ада", "Анатолий", "Кети"):
        thread = threading.Thread(target=ada.handle,
                                  args=(CONFIG, incoming), name=name)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    assert len(clean) == 1, (
        f"на одно сообщение в беседе пришло {len(clean)} ответов "
        "вместо одного")


def test_следующее_сообщение_снова_получает_ответ(clean: list) -> None:
    """Защита не превращается в молчание.

    Регрессия на саму защиту: словарь живёт пять минут, и если бы
    он не сбрасывался, бот замолчал бы в беседе навсегда.
    """
    for message_id in (42, 43):
        ada.handle(CONFIG, message(
            778, "давай поиграем", mid=message_id))

    assert len(clean) == 2, (
        f"на два сообщения пришло {len(clean)} ответов вместо двух")


def test_в_личном_чате_отвечают_все(clean: list) -> None:
    """Личный чат не ограничиваем: там бот нужен сам по себе.

    Ограничение одно на беседу, а не на человека: если бы оно
    действовало везде, то позвав Аду и Анатолия в разных личках
    отвечал бы только один.
    """
    for chat_id in (1001, 1002, 1003):
        ada.handle(CONFIG, message(chat_id, "привет", private=True))

    assert len(clean) == 3, (
        f"в трёх личных чатах пришло {len(clean)} ответов вместо трёх")


def test_команда_в_беседе_не_блокируется(clean: list) -> None:
    """Команду не глотает защита от дублей.

    `/статус` человек шлёт конкретному боту. Если бы он попал под
    то же ограничение, что и обычная реплика, второй бот занял бы
    сообщение первым, и адресат не ответил бы вообще.
    """
    ada.handle(CONFIG, message(779, "/status", mid=50))
    assert clean, "команда в беседе осталась без ответа"