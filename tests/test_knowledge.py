"""База знаний: находит по теме, не тащит лишнего, не тормозит.

Ищет по настоящим файлам проекта: проверять на выдуманных данных
значило бы проверить не то, чем бот пользуется в работе.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOTS = ROOT / "bots" / "код"
if str(BOTS) not in sys.path:
    sys.path.insert(0, str(BOTS))

import knowledge  # noqa: E402

CHAT = 515_151


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Кэш чист: иначе тест зависит от порядка запуска."""
    knowledge._CACHE.clear()
    knowledge._ИНдекс.clear()
    knowledge._ИНдекс_время = 0.0


# ==================================================== находит по теме


def test_база_не_пустая() -> None:
    """Файлы на месте: пустая база — это тихо, бот просто ничего
    не знает, и выглядит как «модель плохая»."""
    assert knowledge.KNOWLEDGE_ROOT.is_dir(), "нет папки с базой знаний"
    files = list(knowledge.KNOWLEDGE_ROOT.glob("*.md"))
    assert files, "в папке базы знаний нет ни одного файла"


def test_индекс_строится() -> None:
    index = knowledge.построить_индекс()
    assert index, "индекс пуст: ни один файл не проиндексирован"
    for path, (title, words, _) in index.items():
        assert title, f"у {Path(path).name} не нашёлся заголовок"
        assert words, f"у {Path(path).name} не нашлось ключевых слов"


def test_находит_определение_слова() -> None:
    """Вопрос про термин приводит его определение, а не заголовок.

    Резать файл надо по заголовкам: разрез по пустым строкам
    отделял «## термин» от определения под ним, и в запрос уезжал
    один заголовок — модель получала вопрос без ответа.
    """
    found = knowledge.найти("ada", CHAT, "что такое токен")
    assert "токен" in found.lower(), "словарь не найден"
    assert len(found) > 80, (
        f"пришло {len(found)} символов — похоже, только заголовок")


def test_находит_команды() -> None:
    """Вопрос про команды приводит файл про ботов."""
    found = knowledge.найти("ada", CHAT, "какие у вас команды")
    assert "/start" in found or "/help" in found, (
        "команды не нашлись в базе")


def test_находит_объяснение_решения() -> None:
    """На «почему так» приходит файл решений, а не догадка."""
    found = knowledge.найти("ada", CHAT, "почему пауза растёт")
    assert "пауза" in found.lower(), "объяснение не найдено"


# ==================================================== не тащит лишнего


@pytest.mark.parametrize("вопрос", [
    "привет", "спасибо", "ок", "как дела", "пока",
])
def test_на_междометия_база_молчит(вопрос: str) -> None:
    """На «привет» из базы не должно уезжать ничего.

    Иначе каждое приветствие сопровождалось бы простынёй из
    файлов, и бот перестал бы отвечать на простые вещи.
    """
    assert knowledge.найти("ada", CHAT, вопрос) == "", (
        f"на «{вопрос}» из базы уехало лишнее")


def test_заметка_пустая_когда_нечего() -> None:
    """Пометка в подсказку не добавляется без причины."""
    assert knowledge.заметка_знаний("ada", CHAT, "привет") == ""


# ==================================================== размер и скорость


def test_заметка_не_превышает_лимит() -> None:
    """Заметка не должна раздувать запрос.

    Ровно это и было бы «огромным контекстом», которого мы
    избегали: память и база вместе занимают считанные проценты
    подсказки.
    """
    found = knowledge.найти("ada", CHAT, "расскажи всё о проекте и о себе")
    assert len(found) <= knowledge.MAX_TOTAL, (
        f"заметка {len(found)} симв., лимит {knowledge.MAX_TOTAL}")


def test_поиск_быстрее_запроса_к_модели() -> None:
    """Поиск должен быть несопоставимо дешевле обращения к модели.

    Один запрос к модели — от 200 до 1700 мс. Если поиск станет
    дороже, базу надо переставать искать на каждый вопрос.
    """
    started = time.perf_counter()
    for number in range(100):
        knowledge.найти("ada", CHAT, f"вопрос {number} про контекст")
    per_query = (time.perf_counter() - started) / 100 * 1000
    assert per_query < 20, (
        f"поиск {per_query:.1f} мс на вопрос — слишком медленно")


def test_повторный_вопрос_берётся_из_кэша() -> None:
    """Один и тот же вопрос второй раз не ищется заново."""
    knowledge.найти("ada", CHAT, "что такое контекст")
    started = time.perf_counter()
    for _ in range(200):
        knowledge.найти("ada", CHAT, "что такое контекст")
    per_query = (time.perf_counter() - started) / 200 * 1000
    assert per_query < 1.0, (
        f"повторный вопрос {per_query:.2f} мс: кэш не работает")


def test_кэш_не_растёт_без_предела() -> None:
    """Кэш ограничен: иначе на сервере он съел бы память."""
    for number in range(knowledge.CACHE_MAX + 40):
        knowledge.найти("ada", CHAT, f"уникальный вопрос {number}")
    assert len(knowledge._CACHE) <= knowledge.CACHE_MAX, (
        f"в кэше {len(knowledge._CACHE)} записей при лимите "
        f"{knowledge.CACHE_MAX}")

def test_файлы_не_переросли_лимит() -> None:
    """Файл базы не должен быть огромным.

    Файл читается целиком при первом обращении, и предыдущий
    прогоне читался как «всё подряд». Крупные файлы замедляют
    первый запрос, хотя дальше работает кэш.
    """
    for path in knowledge.KNOWLEDGE_ROOT.glob("*.md"):
        size = path.stat().st_size
        assert size < 40_000, (
            f"{path.name} весит {size} Б: файл читается целиком, "
            "и крупный замедлит первый запрос")
