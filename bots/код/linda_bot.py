#!/usr/bin/env python3
r"""Линда — второй бот, который учит английскому.

Чем она отличается от Ады
--------------------------
Ада отвечает на вопрос. Линда делает то же, но в конце каждого ответа
даёт «якорь» — пару английских фраз, которые сама использовала в
разборе. Не случайных слов, а тех, что реально прозвучали: человек
слышит английский в контексте, а не списком.

Зачем в конце именно пара
-------------------------
Одна фраза — это пример. Две — уже выбор: видно, что у похожих вещей
есть разные слова, и сравнение учит больше, чем один образец. Больше
двух превращается в список, который никто не запоминает.

Правила речи те же, что у Ады
------------------------------
Уровни детализации и строгости, формат разбора иностранных слов и набор
лиц взяты из persona.py. Если правила изменятся там, они изменятся и
здесь, и разъехаться боты не смогут.

Хостинг
-------
Как и Ада: только стандартная библиотека, `config.json` рядом, те же
настройки провайдеров. Запуск отдельным процессом, чтобы два бота не
делили один опрос обновлений: у каждого своя пачка сообщений.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

try:
    import modelswitch as modelswitch_mod
    from persona import (DETAIL_LEVEL, GLOSSARY, SCIENCE_LEVEL, STYLE,
                         avatar_png, pick_persona, persona_by_key,
                         profile_photo_request, style_note)
    from user_features import (commands_help, forget_text, name_text,
                               status_lines)
except ImportError:  # pragma: no cover - прямой запуск из другой папки
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import modelswitch as modelswitch_mod
    from persona import (DETAIL_LEVEL, GLOSSARY, SCIENCE_LEVEL, STYLE,
                         avatar_png, pick_persona, persona_by_key,
                         profile_photo_request, style_note)
    from user_features import (commands_help, forget_text, name_text,
                               status_lines)

CONFIG_PATH = Path(__file__).resolve().parent / "config_linda.json"
STARTED_AT = time.time()

#: Имя, на которое бот откликается. Границы слова обязательны: без них
#: «линда» поймала бы «Линда-форма» и «палиндар».
NAME_TRIGGERS = re.compile(r"(?<![А-яA-Za-z])(линда|linda)(?![А-яA-Za-z])",
                           re.IGNORECASE)

#: Сколько фраз даётся в конце. Ровно две: одна — пример, три — список.
PHRASES_AT_END = 2

#: Сколько последних реплик уходит модели как контекст.
CONTEXT_MESSAGES = 12

#: Длительность длинного опроса: подобрано замером на сервере, где
#: прокси рвёт соединение дольше трёх секунд.
LONG_POLL_S = 3
POLL_SLEEP_S = 1.0
HTTP_TIMEOUT_S = 45.0
MAX_ANSWER_CHARS = 4000

#: Слова, которые не считаются английским в ответе: служебные слова
#: самого бота и названия, которые встречаются в проекте.
STOP_WORDS = {"and", "or", "but", "the", "a", "an", "of", "to", "in",
              "is", "it", "you", "i", "we", "they", "that", "this"}


def build_persona(face: dict[str, Any], own: str = "") -> str:
    """Подсказка модели: кто она и что она добавляет к ответу."""
    parts = [
        f"Ты — {face['name']}, {face['role']}. Говоришь {face['voice']}.",
        STYLE,
        style_note(),
        ("Ты учишь английскому. После основного ответа добавь ровно две "
         "английские фразы — те, что сама использовала в разборе, а не "
         "случайные. Обе дай вместе с переводом и разбором: как читается "
         "и почему именно так. Формат строки: фраза — перевод; здесь: "
         "что она заменяет в русском.\n"
         "Если английских фраз в ответе не было — всё равно дай две "
         "подходящие к теме, и скажи прямо, что они добавлены, а не "
         "взяты из разбора."),
    ]
    if own.strip():
        parts.append(own.strip())
    return "\n\n".join(parts)


def load_config() -> dict[str, Any]:
    """Настройки: файл рядом, приоритет у переменной окружения."""
    data: dict[str, Any] = {}
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    # Токен можно задать и через окружение — удобно, когда два бота на
    # одном сервере и токены разные.
    token = (os.environ.get("LINDA_TELEGRAM_TOKEN")
             or data.get("telegram_token") or "")
    providers = data.get("providers") or []
    if not providers and CONFIG_PATH.with_name("config.json").is_file():
        # Настройки провайдеров общие с Адой: заводить второй файл с
        # теми же ключами — значит держать их в двух местах.
        try:
            shared = json.loads(CONFIG_PATH.with_name("config.json")
                                .read_text(encoding="utf-8"))
            providers = shared.get("providers") or []
        except (OSError, ValueError):
            providers = []
    active = int(data.get("active") or 0)
    if providers and not 0 <= active < len(providers):
        active = 0
    face = persona_by_key(str(data.get("persona_key") or "")) or pick_persona()
    return {"token": token, "providers": providers, "active": active,
            "persona": build_persona(face, str(data.get("persona") or "")),
            "face": face,
            "heartbeat_chat_id": str(data.get("heartbeat_chat_id") or "")}


def telegram(token: str, method: str, **params: Any) -> Any:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}).encode()
    request = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description") or "Telegram отказал")
    return payload.get("result")


def send(token: str, chat_id: int | str, text: str) -> None:
    try:
        telegram(token, "sendMessage", chat_id=chat_id,
                 text=text[:MAX_ANSWER_CHARS],
                 disable_web_page_preview="true")
    except Exception as exc:
        print(f"не отправилось: {exc}", flush=True)


def set_avatar(token: str, face: dict[str, Any]) -> bool:
    """Аватар по лицу. Отказ не роняет бота."""
    try:
        photo = avatar_png(face, 256)
        url, body, headers = profile_photo_request(token, photo, face)
        request = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST")
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except Exception as exc:
        print(f"аватар не поставился: {exc}", flush=True)
        return False


def ask_model(provider: dict[str, Any], prompt: str,
              system: str = "", key: str = "") -> str:
    base = str(provider.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("провайдер не настроен: пустой адрес")
    # Ключ приходит параметром, а не берётся из настроек: там лежит
    # имя переменной (`GROQ_API_KEY`), а не сам ключ. Из-за этого в
    # запрос уходила строка с именем переменной и провайдер отвечал
    # 403. Настоящий ключ подставляет `modelswitch`.
    if not key:
        raise RuntimeError(
            f"у провайдера «{provider.get('name', '?')}» нет ключа")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": provider.get("model") or "",
        "messages": messages,
        # Здесь ответ длиннее, чем у Ады: к разбору добавляются две
        # английские фразы с переводом. На бесплатном тарифе это уже
        # заметная часть квоты, поэтому потолок выше, но не вдвое.
        "max_tokens": 700,
        "temperature": 0.5,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/chat/completions", data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"провайдер ответил HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"сеть недоступна: {exc.reason}") from exc
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("message", {}).get("content") or "").strip()


def english_phrases(answer: str) -> list[str]:
    """Английские фразы, которые уже есть в ответе.

    Берутся целые строки и реплики: человек учится на фразах, а не на
    отдельных словах. Перевод и разбор отбрасываются — иначе в
    «якорь» попадёт строка, где английский продублирован русским.

    Что считается фразой
    --------------------
    Нужны два слова и хотя бы одно содержательное. Требование двух
    содержательных было бы слишком строгим: «the payload» и «the
    request» — самые обычные фразы в разборе, и именно они нужнее
    всего. А вот «and of the» фразой быть не может: содержательного
    слова в ней нет вовсе, и учиться тут нечему.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in answer.splitlines():
        text = line.strip().lstrip("-—*•# ").strip()
        if not text:
            continue
        # Убираем хвост после тире: перевод и разбор в «якоре» не нужны.
        text = re.split(r"\s+[—–-]\s+", text)[0].strip()
        text = text.strip("«»\"'`*_ ").strip()
        # Если строка началась с русского текста, а английский кусок
        # стоит в кавычках, берём только его. Без этого в «якорь» уходит
        # «Сначала нужно сделать «deploy the files», и человек видит
        # русскую прозу вместо английской фразы.
        quoted = re.findall(r"[«\"']?\b([A-Za-z][A-Za-z'’\- ]{3,}[A-Za-z])"
                            r"[»\"']?", text)
        if quoted:
            candidates = [item.strip() for item in quoted]
        else:
            candidates = [text]
        for candidate in candidates:
            candidate = candidate.strip("«»\"'`*_ ").strip()
            words = re.findall(r"[A-Za-z][A-Za-z'’-]*", candidate)
            # Фраза — от двух слов: одно слово это слово, а не фраза.
            if len(words) < 2:
                continue
            content = [w for w in words
                       if w.lower() not in STOP_WORDS and len(w) >= 4]
            if not content:
                continue
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)
            if len(found) >= PHRASES_AT_END:
                return found
    return found


def build_prompt(history: deque[tuple[str, str]]) -> str:
    """Запрос из последних реплик."""
    if not history:
        return ""
    lines = ["Реплики в чате по порядку (последняя — свежая):"]
    lines += [f"— {who}: {text}" for who, text in history]
    lines.append("")
    lines.append("Ответь по-русски, разбери вопрос по существу и закончи "
                 "двумя английскими фразами, как сказано в подсказке.")
    return "\n".join(lines)


def greeting(face: dict[str, Any]) -> str:
    """Первая реплика: кто я и что я делаю."""
    return (f"{face['name']} на связи. Я {face['role']}.\n"
            "Спросите что угодно по-русски — отвечу и в конце дам пару "
            "английских фраз, которые сам использую в ответе.\n"
            "Можно позвать меня по имени: «Линда» или «Linda».")


#: Состояние чатов: счётчик, признак «к ней обратились», буфер реплик.
CHATS: dict[int, dict[str, Any]] = {}


def chat_state(chat_id: int) -> dict[str, Any]:
    state = CHATS.get(chat_id)
    if state is None:
        state = {"count": 0, "addressed": False, "beat": 0.0,
                 "history": deque(maxlen=CONTEXT_MESSAGES)}
        CHATS[chat_id] = state
    return state


# ------------------------------------------------------------- команды

def _cmd_help(config: dict[str, Any], state: dict[str, Any],
              chat_id: int) -> str:
    """Справка по командам."""
    return commands_help(config["face"])


def _cmd_whoami(config: dict[str, Any], state: dict[str, Any],
                chat_id: int) -> str:
    """Идентификатор чата."""
    return f"Ваш chat_id: {chat_id}"


def _cmd_name(config: dict[str, Any], state: dict[str, Any],
              chat_id: int) -> str:
    """Какое сейчас имя у бота."""
    return name_text(config["face"])


def _cmd_forget(config: dict[str, Any], state: dict[str, Any],
                chat_id: int) -> str:
    """Забыть разговор."""
    state["history"].clear()
    state["addressed"] = False
    return forget_text(config["face"])


def _cmd_status(config: dict[str, Any], state: dict[str, Any],
                chat_id: int) -> str:
    """Состояние бота. Отдельный текст: у Линды в конце каждого
    ответа английские фразы, и общий статус про это молчит."""
    active = (config["providers"][config["active"]]
              if config["providers"] else {})
    return "\n".join([
        f"{config['face']['name']} работает — {config['face']['role']}.",
        f"модель: {active.get('name', 'не настроена')}",
        f"детализация {DETAIL_LEVEL} из 10, строгость "
        f"{SCIENCE_LEVEL} из 10",
        "в конце каждого ответа даю пару английских фраз с переводом.",
    ])


#: Команды и их обработчики. Тот же набор, что у Ады, кроме пульса:
#: у Линды своих сообщений в личные чаты нет.
COMMANDS = {
    "/start": _cmd_help,
    "/help": _cmd_help,
    "/status": _cmd_status,
    "/whoami": _cmd_whoami,
    "/name": _cmd_name,
    "/forget": _cmd_forget,
}


def handle(config: dict[str, Any], message: dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = str(message.get("text") or "").strip()
    if chat_id is None or not text:
        return
    if (message.get("from") or {}).get("is_bot"):
        return
    sender = (message.get("from") or {}).get("first_name") or "участник"
    is_private = str(chat.get("type") or "") == "private"

    token = config["token"]
    state = chat_state(int(chat_id))
    state["count"] += 1

    command = text.split()[0].lower().split("@")[0]
    # Команды разбираются ДО записи в историю: иначе через десять
    # вызовов модель получает переписку из «/help» и «/status».
    if command in COMMANDS:
        answer = COMMANDS[command](config, state, chat_id)
        if answer:
            send(token, chat_id, answer)
        return

    state["history"].append((sender, text[:500]))

    if NAME_TRIGGERS.search(text):
        state["addressed"] = True
        stripped = NAME_TRIGGERS.sub("", text).strip(" ,.:!?—-")
        if len(stripped) < 3:
            send(token, chat_id, greeting(config["face"]))
            return
        state["history"][-1] = (sender, stripped[:500])

    if not is_private and not state["addressed"] and state["count"] % 10:
        return
    state["addressed"] = False

    providers = config["providers"]
    if not providers:
        send(token, chat_id, "Модели не настроены на сервере.")
        return
    prompt = build_prompt(state["history"])
    if not prompt:
        return
    try:
        # Через modelswitch: он переберёт запасные модели и ключи сам.
        # Раньше здесь стоял прямой вызов одной модели, и человек в чате
        # видел «Не получилось ответить: провайдер ответил HTTP 403».
        answer = modelswitch_mod.ask(config, prompt, config["persona"],
                                     asker=ask_model)
    except Exception:
        answer = "Сейчас не получается ответить, попробуй через минуту."
    if answer:
        send(token, chat_id, answer)


def main() -> None:
    config = load_config()
    if not config["token"]:
        raise SystemExit(
            "Нет токена Telegram: впишите telegram_token в config_linda.json "
            "или задайте переменную LINDA_TELEGRAM_TOKEN.")
    try:
        telegram(config["token"], "deleteWebhook",
                 drop_pending_updates="false")
        me = telegram(config["token"], "getMe")
        print(f"запущена как {config['face']['name']}: "
              f"@{me.get('username')}", flush=True)
    except Exception as exc:
        raise SystemExit(f"Telegram недоступен: {exc}") from exc
    print(f"моделей: {len(config['providers'])}", flush=True)
    set_avatar(config["token"], config["face"])

    offset = 0
    while True:
        try:
            url = ("https://api.telegram.org/bot" + config["token"]
                   + f"/getUpdates?timeout={LONG_POLL_S}&offset={offset}")
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
                updates = json.loads(resp.read().decode("utf-8")).get("result") or []
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                handle(config, update.get("message") or {})
        except KeyboardInterrupt:
            print("остановлена", flush=True)
            return
        except Exception as exc:
            print(f"ошибка опроса: {exc}", flush=True)
            time.sleep(5)
        time.sleep(POLL_SLEEP_S)


if __name__ == "__main__":
    main()