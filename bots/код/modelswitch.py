r"""Выбор модели: пять вариантов, запасные ключи, тихий переход.

Зачем этот файл
---------------
Боты сидели на одной модели — `llama-3.1-8b-instant` от Groq, —
и когда её выключили, всё разом сломалось: человек писал, а бот
отвечал «Не получилось ответить: провайдер ответил HTTP 403».
При этом в хранилище zagent лежало двадцать четыре живых ключа и
ещё несколько моделей, о которых бот не знал.

Здесь три вещи, которых раньше не было:

1. **Пять моделей на выбор.** Не одна настройка, а список с
   переключением прямо в чате.
2. **Запасные ключи.** У Groq восемь ключей: при лимите на
   аккаунт бот берёт следующий, а не жмёт «провайдер ответил
   429» человеку в чат.
3. **Тихий переход.** Если модель не ответила, бот пробует
   следующую и отвечает один раз — отвечающим текстом, а не
   техническим сообщением об ошибке.

Почему список моделей в `config.json`, а не здесь
----------------------------------------------
Чтобы смена модели не требовала правки кода: список лежит в
настройках, этот файл только умеет по нему ходить.
"""

from __future__ import annotations

import json
import re
import os
import time
from pathlib import Path
from typing import Any

#: Где искать ключи моделей, по порядку.
#:
#: Список, а не один путь, потому что на сервере файл называется иначе:
#: там он ` bots/.secret/ключи-моделей.json`, потому что заливается
#: скриптом запуска, а на своём компьютере —
#: `bots/секреты/ключи-моделей.local.json`.
#:
#: Раньше был один путь — «секреты» на компьютере. На сервере такого
#: каталога нет, файл читался пустым, ключей не находилось, и бот
#: отвечал «модель промолчала», хотя ключи рядом лежали.
_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "секреты"
    / "ключи-моделей.local.json",
    Path(__file__).resolve().parent.parent / ".secret"
    / "ключи-моделей.json",
    Path(__file__).resolve().parent / ".secret" / "ключи-моделей.json",
    Path(__file__).resolve().parent / "config.local.json",
)

#: Первый найденный файл. Оставлено для совместимости: кое-где в коде
#: на путь ссылались напрямую.
SECRETS_PATH = _CANDIDATES[0]

#: Как долго помнить, что модель только что не ответила.
#:
#: Цифра не случайная: при 429 провайдер просит подождать, и без
#: паузы бот долбит его тем же запросом — и получает 429 снова.
#: Минута — примерно то, что просит Groq в заголовке Retry-After.
COOLDOWN_S = 60.0

#: Куда именно не показывать человекам технические подробности.
#: Раньше в чат улетало «провайдер ответил HTTP 403» — человеку
#: это ничего не объясняет, только пугает.
TECH_WORDS = ("HTTP", "Traceback", "Connection", "Timeout", "urlopen")


def secret_file() -> Path | None:
    """Первый файл с ключами, который действительно есть.

    Проверяем наличие, а не предполагаем путь: на сервере имена и
    папки другие, и молча читать несуществующий файл — это как раз
    тот случай, когда бот «молчит», ничего не объясняя.
    """
    for path in _CANDIDATES:
        if path.is_file():
            return path
    return None


def load_secret_keys() -> dict[str, list[str]]:
    """Прочитать ключи из файла, который вне git.

    Возвращает словарь `имя провайдера -> список ключей`. Пустой
    словарь — не поломка: значит, ключи не положены, и вызывающий
    код должен честно сказать об этом при старте.

    Понимаются оба вида файла: и `{"providers": {...}}`, как на
    своём компьютере, и одиночные ключи верхнего уровня — так
    выглядит старый формат на сервере.
    """
    path = secret_file()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}

    providers = data.get("providers") or {}
    out: dict[str, list[str]] = {}
    for name, keys in providers.items():
        if isinstance(keys, list):
            out[str(name)] = [str(k) for k in keys if k]
        elif isinstance(keys, str) and keys:
            out[str(name)] = [keys]

    # Старый формат: ключи лежат прямо в корне файла, по одному на
    # провайдера. Без этого сервер остался бы без ключей: он читает
    # файл, созданный до появления `providers`.
    for name in ("groq", "openrouter", "mistralai", "nvidia", "z_ai"):
        if name in out:
            continue
        value = data.get(name)
        if isinstance(value, list) and value:
            out[name] = [str(k) for k in value if k]
        elif isinstance(value, str) and value:
            out[name] = [value]
    return out


#: Ключи, заданные прямо в настройках. Пусто в работе: ключи лежат
#: в файле вне git и читаются оттуда. Нужно для проверок — тестам
#: незачем читать настоящие ключи с диска.
KEYS: dict[str, list[str]] = {}


#: Когда в следующий раз можно пробовать модель снова.
#: Ключ — `провайдер/модель`, значение — время в секундах.
_BLOCKED_UNTIL: dict[str, float] = {}

#: Какой ключ какого провайдера пробовали последним. Нужен, чтобы
#: при лимите переходить на следующий по кругу, а не долбить один.
_KEY_ROTATION: dict[str, int] = {}


def _block(provider: dict[str, Any]) -> None:
    """Запомнить: эта модель сейчас не работает."""
    name = f"{provider.get('name')}/{provider.get('model')}"
    _BLOCKED_UNTIL[name] = time.time() + COOLDOWN_S


def _is_cool(name: str) -> bool:
    return _BLOCKED_UNTIL.get(name, 0.0) > time.time()


def _key_for(provider: dict[str, Any]) -> str:
    """Взять ключ провайдера, чередуя их.

    Зачем чередовать
    ----------------
    У Groq лимит считается на ключ, и восемь ключей — это восемь
    отдельных квот. Если бить одним ключом, остальные простаивают,
    а при его лимите бот встаёт, хотя семь ключей свободны.

    Что приходит из переменной окружения
    ------------------------------------
    На сервере ключи кладутся в переменную окружения, и туда
    положили только **первый** ключ из списка. Из-за этого
    чередование там не работало вовсе: остальные семь лежали в
    файле без дела, а когда первый исчерпывал лимит, бот молчал.
    Поэтому значение переменной разбирается как список — по
    запятой или пробелу, — и чередование работает на сервере так
    же, как на своём компьютере.
    """
    env = str(provider.get("api_key") or "")
    direct = os.environ.get(env, "").strip()
    if direct:
        # Несколько ключей в одной переменной: выбираем по очереди.
        if "," in direct or " " in direct:
            parts = [p for p in re.split(r"[,\s]+", direct) if p]
            index = _KEY_ROTATION.get(env, 0) % len(parts)
            _KEY_ROTATION[env] = index + 1
            return parts[index]
        return direct

    store = load_secret_keys()
    # Имя переменной вида `GROQ_API_KEY` -> `groq`.
    name = env.split("_")[0].lower() if env else ""
    keys = (KEYS.get(str(provider.get("name")))
            or KEYS.get(name)
            or store.get(str(provider.get("name")))
            or store.get(name) or [])
    if not keys:
        return ""
    index = _KEY_ROTATION.get(env, 0) % len(keys)
    _KEY_ROTATION[env] = index + 1
    return keys[index]


def _human(exc: Exception) -> str:
    """Ошибку провайдера — меткой причины, а не готовым текстом.

    Почему метка, а не фраза
    ----------------------
    Здесь ничего не известно про характер бота: есть только объект
    ошибки. Текст отказа зависит от персонажа и рода, поэтому
    собирать его здесь означало бы тянуть `char_key` через четыре
    слоя вызовов. Отсюда уходит короткая причина, а человек читает
    её в `fallback`, где про него уже всё известно.

    Технические слова по-прежнему наружу не идут: человек ничего не
    может с ними сделать, а место в чате они займут.
    """
    text = str(exc)
    if any(word in text for word in TECH_WORDS):
        return "техническая ошибка"
    return "техническая ошибка"


def provider_pool(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Список моделей, которые попробовать по очереди.

    Первым идёт выбранная человеком, дальше — запасные из
    `fallback_models`. Модели, на которые повешен бан, пропускаются:
    если Groq вернул 429, второй раз стучаться в ту же дверь
    бессмысленно, за минуту всё равно не отпустит.
    """
    providers = config.get("providers") or []
    if not providers:
        return []

    by_model = {str(p.get("model")): p for p in providers}
    order: list[dict[str, Any]] = []

    active = int(config.get("active") or 0)
    if 0 <= active < len(providers):
        order.append(providers[active])

    for name in config.get("fallback_models") or []:
        provider = by_model.get(str(name))
        if provider and provider not in order:
            order.append(provider)

    for provider in providers:
        if provider not in order:
            order.append(provider)

    return [p for p in order
            if not _is_cool(f"{p.get('name')}/{p.get('model')}")]


def ask(config: dict[str, Any], prompt: str, system: str,
         asker: Any = None) -> str:
    """Спросить модель, молча перебирая запасные.

    Возвращает текст ответа либо МЕТКУ ПРИЧИНЫ отказа: «молчала»,
    «нет ключа», «техническая ошибка». Текст собирает вызывающий
    через `fallback`, потому что здесь не известны ни характер бота,
    ни его род, а отказ обязан звучать как речь конкретного человека.

    Больше не бросает исключений в чат: раньше `handle()` ловил их
    и подставлял «провайдер ответил HTTP 403», и человек видел
    технический текст вместо ответа.

    Параметр `asker` — функция запроса того бота, который спросил.
    Раньше она бралась из `ada_bot` жёстко, и `linda_bot` звал
    модели через чужую функцию: подмена в тестах попадала не туда,
    а у двух ботов на одном сервере одна ломала другую. Теперь каждый
    передаёт свою, и подмена работает там, где её ждут.
    """
    if asker is None:
        from ada_bot import ask_model as asker  # локальный: цикл импорта

    pool = provider_pool(config)
    if not pool:
        return "Модели не настроены."

    # Здесь накапливается ПРИЧИНА, а не текст. Текст собирается в
    # `handle()` через `fallback`: там известен бот, его характер и
    # род, а здесь — нет.
    last = "неизвестно"
    for provider in pool:
        key = _key_for(provider)
        if not key:
            last = "нет ключа"
            continue
        try:
            answer = asker(provider, prompt, system, key=key)
        except Exception as exc:  # noqa: BLE001
            _block(provider)
            last = _human(exc)
            continue
        if answer:
            return answer
        last = "молчала"

    return last


#: Когда в прошлый раз объясняли, что всё заблокировано. Чтобы не
#: повторять одно и то же объяснение на каждое сообщение.
_LAST_NOTICE: float = 0.0


def _blocked_notice() -> None:
    global _LAST_NOTICE
    _LAST_NOTICE = time.time()


def models_list(config: dict[str, Any]) -> str:
    """Что сейчас выбрано и что можно переключить."""
    providers = config.get("providers") or []
    if not providers:
        return "Модели не настроены."
    active = int(config.get("active") or 0)
    lines = ["Модели, между которыми можно переключаться:", ""]
    for index, provider in enumerate(providers):
        mark = "сейчас эта" if index == active else "  "
        lines.append(f"{mark} {index + 1}. {provider.get('label', '')}"
                     f" — {provider.get('model', '')}")
        note = str(provider.get("note") or "").strip()
        if note:
            lines.append(f"       {note}")
    lines += ["", "Переключить: /модель 2. Показать: /модели"]
    return "\n".join(lines)


def switch(config: dict[str, Any], arg: str) -> str:
    """Переключить модель по номеру или по имени."""
    providers = config.get("providers") or []
    if not providers:
        return "Модели не настроены."
    arg = arg.strip()
    if not arg:
        return models_list(config)

    index = -1
    if arg.isdigit():
        candidate = int(arg) - 1
        if 0 <= candidate < len(providers):
            index = candidate
    if index < 0:
        low = arg.lower()
        for position, provider in enumerate(providers):
            if low in str(provider.get("label", "")).lower() or \
                    low == str(provider.get("model", "")).lower():
                index = position
                break
    if index < 0:
        return (f"Нет такой модели. Их {len(providers)} — "
                f"номер от 1 до {len(providers)}.")

    config["active"] = index
    # Переключились — бан с прошлой модели снимаем: человек мог
    # выбрать её специально, после её починки.
    _BLOCKED_UNTIL.clear()
    _LAST_NOTICE = 0.0
    chosen = providers[index]
    return (f"Теперь: {chosen.get('label', '')} "
            f"({chosen.get('model', '')}).")