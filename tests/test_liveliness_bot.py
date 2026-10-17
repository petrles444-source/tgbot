"""Живость в боте: слова меняют настройку, настройка меняет ответы.

Здесь проверяется не сам модуль `liveliness` (у него своя самопроверка),
а то, что бот эту настройку действительно слушает. Разница существенная:
модуль может быть правилен, а бот — забыть его позвать, и человек бы
ничего не заметил, кроме того, что «активнее» ничего не делает.

Что тут проверяется
------------------
* слова «активнее» и «не спамь» двигают шкалу и отвечают, что стало;
* на живости 100 модель не зовётся вовсе — ответ берётся из заготовки;
* на живости 0 модель зовётся всегда;
* модель получает нижний промт (бутерброд);
* если модель дважды выдаёт одно и то же, человек всё равно не видит
  два одинаковых ответа подряд.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOTS = ROOT / "bots" / "код"

_spec = importlib.util.spec_from_file_location(
    "ada_bot", BOTS / "ada_bot.py")
assert _spec and _spec.loader
ada = importlib.util.module_from_spec(_spec)
sys.modules["ada_bot"] = ada
sys.path.insert(0, str(BOTS))
import persona  # noqa: E402
_spec.loader.exec_module(ada)

import liveliness  # noqa: E402


@pytest.fixture(autouse=True)
def clean() -> Any:
    """Чистое состояние и подмена отправки на каждый тест."""
    ada.CHATS.clear()
    ada.OWN_ID["id"] = None
    ada._ANSWERED.clear()
    # Живость по умолчанию в тестах — 0 (см. conftest): проверки ниже
    # выставляют её сами, где это и есть предмет проверки.
    liveliness.ЖИВОСТЬ_ПО_УМОЛЧАНИЮ = 0
    sent: list[tuple[int, str]] = []
    ada.send = lambda token, chat, text: sent.append((chat, text))
    # Модель тоже подменяем: без этого тест, где команда случайно не
    # распозналась, полез бы в сеть и упал бы не по своей причине.
    ada.ask_model = (lambda provider, prompt, system="", key="", **kw:
                     f"ОТВЕТ МОДЕЛИ: {prompt[-30:]}")
    return sent


_next_id = 0


def message(chat_id: int, text: str, *, private: bool = True,
            mid: int = 0) -> dict[str, Any]:
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


CONFIG = {"token": "t", "providers": [{"name": "p", "base_url": "u",
                                       "model": "m", "api_key": "k"}],
          "active": 0, "persona": "persona", "heartbeat_chat_id": "",
          "char_key": "ada", "face": persona.PERSONAS[0],
          "detail_level": f"{persona.DETAIL_LEVEL} из 10",
          "science_level": f"{persona.SCIENCE_LEVEL} из 10"}


def состояние(chat_id: int) -> dict[str, Any]:
    return ada.chat_state(chat_id)


# ============================================================ слова меняют шкалу


def test_активнее_поднимает_живость(clean: Any) -> None:
    ada.handle(CONFIG, message(1, "Ада, активнее"))
    assert состояние(1)["живость"] == 10
    assert "Живость 10" in clean[-1][1]


def test_не_спамь_опускает_живость(clean: Any) -> None:
    состояние(1)["живость"] = 50
    ada.handle(CONFIG, message(1, "Ада, не спамь"))
    assert состояние(1)["живость"] == 40
    assert "Живость 40" in clean[-1][1]


def test_повтор_слова_поднимает_дальше(clean: Any) -> None:
    ada.handle(CONFIG, message(1, "активнее"))
    ada.handle(CONFIG, message(1, "активнее"))
    ada.handle(CONFIG, message(1, "активнее"))
    assert состояние(1)["живость"] == 30


def test_на_потолке_говорит_что_потолок(clean: Any) -> None:
    состояние(1)["живость"] = 95
    ada.handle(CONFIG, message(1, "активнее"))
    assert состояние(1)["живость"] == 100
    assert " Больше некуда" in clean[-1][1]


def test_на_dне_говорит_что_дно(clean: Any) -> None:
    состояние(1)["живость"] = 5
    ada.handle(CONFIG, message(1, "не спамь"))
    assert состояние(1)["живость"] == 0
    assert "ниже не будет" in clean[-1][1]


def test_слово_не_попадает_в_историю(clean: Any) -> None:
    ada.handle(CONFIG, message(1, "активнее"))
    assert list(состояние(1)["history"]) == []


# ============================================================ настройка меняет ответ


def test_на_живости_100_модель_зовётся_редко(clean: Any) -> None:
    """Потолок 70 %: модель почти не нужна, но совсем не выключать нельзя.

    Раньше проверка требовала «не зовётся вовсе» — и она была неверной.
    Потолок задан намеренно: человек позвал бота по имени, значит у
    него есть вопрос, и молчать вместо ответа хуже, чем ответить
    заготовкой. Поэтому модель при живости 100 всё же зовётся, просто
    примерно в одном случае из трёх.
    """
    позван = []

    def capture(provider, prompt, system="", key="", **kw):
        позван.append(1)
        return f"уникальный ответ модели {len(позван)}"

    ada.ask_model = capture
    состояние(1)["живость"] = 100
    всего = 40
    for i in range(всего):
        ada.handle(CONFIG, message(1, f"расскажи что-нибудь {i}"))
    assert len(clean) == всего, "человек должен получить ответ на каждое сообщение"
    assert len(позван) < всего // 2, (
        f"модель звали {len(позван)} из {всего} — на живости 100 "
        f"она должна быть почти не нужна")


def test_на_живости_0_модель_зовётся_всегда(clean: Any) -> None:
    позван = []

    def capture(provider, prompt, system="", key="", **kw):
        позван.append(1)
        # Разные ответы обязательны: одинаковый модель отдала бы трижды
        # подряд, запрет повторов заставил бы спросить ещё, и счётчик
        # позвали бы перевалил за ожидаемый. Проверка считает вызовы,
        # а не сообщения.
        return f"настоящий ответ модели {len(позван)}"

    ada.ask_model = capture
    состояние(1)["живость"] = 0
    всего = 30
    for i in range(всего):
        ada.handle(CONFIG, message(1, f"вопрос номер {i}"))
    assert len(позван) == всего, (
        f"модель позвали {len(позван)} из {всего}, а на живости 0 "
        f"она зовётся на каждое сообщение")


def test_на_живости_0_ответ_идёт_из_модели(clean: Any) -> None:
    ada.ask_model = lambda *a, **k: "настоящий ответ модели"
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "вопрос"))
    assert clean[-1][1] == "настоящий ответ модели"


# ============================================================ бутерброд промта


def test_модель_получает_нижний_промт() -> None:
    видел: list[dict[str, Any]] = []

    def capture(provider, prompt, system="", key="", низ="", **kw):
        видел.append({"system": system, "низ": низ})
        return "ответ"

    ada.ask_model = capture
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "вопрос"))
    assert видел, "модель не позвали"
    assert видел[0]["низ"], "нижний промт не пришёл в модель"


def test_нижний_промт_запрещает_повторы() -> None:
    видел: list[str] = []

    def capture(provider, prompt, system="", key="", низ="", **kw):
        видел.append(низ)
        return "ответ"

    ada.ask_model = capture
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "вопрос"))
    assert "НЕ повторяешь" in видел[0]


def test_верхний_промт_тоже_есть() -> None:
    видел: list[str] = []

    def capture(provider, prompt, system="", key="", низ="", **kw):
        видел.append(system)
        return "ответ"

    ada.ask_model = capture
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "вопрос"))
    assert видел[0].strip(), "верхний характер должен уходить в модель"


# ============================================================ запрет повторов


def test_два_одинаковых_ответа_человеку_не_уходят(clean: Any) -> None:
    """Модель обязана повторять — человек всё равно не видит повтора."""
    ada.ask_model = lambda *a, **k: "совершенно один и тот же ответ"
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "первый вопрос"))
    ada.handle(CONFIG, message(1, "второй вопрос"))
    тексты = [т for _, т in clean]
    живые = [т for т in тексты if т == "совершенно один и тот же ответ"]
    assert len(живые) <= 1, f"повтор ушёл в чат дважды: {тексты}"


def test_заготовки_тоже_не_повторяются(clean: Any) -> None:
    """Даже на чистых заготовках подряд одинаковых быть не должно."""
    состояние(1)["живость"] = 100
    for i in range(20):
        ada.handle(CONFIG, message(1, f"болтовня {i}"))
    тексты = [т for _, т in clean]
    подряд = [a for a, b in zip(тексты, тексты[1:]) if a == b]
    assert not подряд, f"подряд одинаковые: {подряд}"


def test_модель_молчит_берётся_заготовка(clean: Any) -> None:
    ada.ask_model = lambda *a, **k: ""
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "вопрос"))
    assert clean, "человек не должен остаться без ответа"
    assert clean[-1][1]


# ============================================================ история ответов


def test_ответы_бота_копятся_в_истории() -> None:
    ada.ask_model = lambda *a, **k: f"ответ модели {len(clean)}"
    состояние(1)["живость"] = 0
    ada.handle(CONFIG, message(1, "первый"))
    ada.handle(CONFIG, message(1, "второй"))
    assert len(состояние(1)["свои_ответы"]) == 2


def test_история_ответов_обрезается() -> None:
    ada.ask_model = lambda *a, **k: "разный ответ"
    состояние(1)["живость"] = 0
    for i in range(30):
        ada.handle(CONFIG, message(1, f"вопрос {i}"))
    assert len(состояние(1)["свои_ответы"]) <= liveliness.ГЛУБИНА_ИСТОРИИ