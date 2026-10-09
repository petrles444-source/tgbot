#!/usr/bin/env python3
r"""Ада — телеграм-бот, который отвечает на имя и на каждое десятое сообщение.

Правила поведения
-----------------
* **Отклик на имя.** Если в сообщении есть «Ада», «ада», «ADA», «Ada»,
  «ada» — бот отвечает «Что хотели узнать?» и дальше отвечает на
  следующую реплику в этом чате как на адресованную ему. Имя ищется по
  границам слова, чтобы «адаптер» или «палата» её не звали.
* **Каждое десятое.** В беседе бот сам отвечает на каждое десятое
  сообщение — иначе он превратил бы чат в свой монолог.
* **В личном чате** отвечает на всё: там с ним говорят один на один, и
  молчать девять сообщений подряд просто невежливо.
* **Контекст.** Перед ответом модель получает последние реплики чата,
  поэтому она отвечает на разговор, а не на отдельную фразу.
* **Пульс** раз в `HEARTBEAT_S` секунд — только в личные чаты. В беседе
  такое сообщение было бы шумом для всех.

Что она игнорирует
------------------
* Свои собственные сообщения.
* Сообщения от ботов (`sender.is_bot`) — иначе боты зацикливались бы
  друг на друга.
* Сообщения без текста: картинки, стикеры, голосовые молча пропускаются.

Хостинг
-------
Скрипт работает на бесплатном PythonAnywhere: только стандартная
библиотека, никаких установок. Квоты там 100 секунд процессорного
времени в сутки, поэтому ответ модели берётся с коротким
`max_tokens`, а пустой или слишком частый диалог просто не сложится.

Настройки
---------
Рядом лежит `config.json` (на сервере) либо переменные окружения
`TELEGRAM_TOKEN` и `TELEGRAM_CHAT_ID`. Ключи моделей — в providers
того же файла.
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
    import chars as chars_mod
    from persona import (DETAIL_LEVEL, GLOSSARY, PERSONAS, SCIENCE_LEVEL,
                         STYLE, avatar_png, glossary_format_example,
                         pick_persona, persona_by_key, profile_photo_request,
                         style_note)
    from user_features import (commands_help, forget_text, name_text,
                               status_lines)
except ImportError:  # pragma: no cover - прямой запуск из другой папки
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from persona import (DETAIL_LEVEL, GLOSSARY, PERSONAS, SCIENCE_LEVEL,
                         STYLE, avatar_png, glossary_format_example,
                         pick_persona, persona_by_key, profile_photo_request,
                         style_note)
    from user_features import (commands_help, forget_text, name_text,
                               status_lines)

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
STARTED_AT = time.time()

#: Как часто бот сам пишет о себе — в личный чат.
HEARTBEAT_S = 100

#: Отвечать на каждое N-е сообщение в беседе.
#:
#: Раньше здесь стояло 10, и из-за этого бот молчал на девять сообщений
#: подряд. Это была попытка не засорять группу, но человек воспринял
#: её как «бот сломан»: он пишет, а ответа нет. По его прямой просьбе
#: бот отвечает на каждое сообщение, поэтому значение равно единице.
#:
#: Если потом понадобится вернуть ограничение — правится одна цифра.
EVERY_N = 1

#: Сколько последних реплик чата уходит модели как контекст. Больше —
#: дороже и медленнее, а короткая память для «умного ответа» достаточна.
CONTEXT_MESSAGES = 12

#: Длительность длинного опроса обновлений.
#:
#: Обычно бот просит 25 секунд, и это экономно. Здесь так нельзя: прокси
#: PythonAnywhere рвёт соединение, висящее дольше нескольких секунд, и
#: `getUpdates` отвечает «Tunnel connection failed: 503».
#:
#: Раньше здесь стояло 3 — и перестало работать: граница у прокси сдвинулась
#: вниз. Поэтому значение не подбирается на глаз, а ставится 0: опрос
#: возвращается мгновенно, и прокси просто нечего рвать.
#:
#: Это не блокировка Telegram, и на это есть прямое доказательство в
#: первых же строках запуска: `setMyProfilePic` получает от Telegram
#: настоящий ответ 404, а `getUpdates` — настоящий 409. Значит сеть до
#: Telegram есть, обрывается только то, что долго молчит.
LONG_POLL_S = 0

#: Пауза между опросами. Меньше — отзывчивее, но чаще запросы.
#:
#: Раз опрос возвращается мгновенно, пауза — единственное, что держит
#: темп. Две секунды дают около тридцати опросов в минуту, и это ровно на
#: грани: Telegram разрешает 30 запросов в секунду, но пачка ответов без
#: пауз рано или поздно упрётся в суточный лимит. Три секунды — это
#: двадцать опросов в минуту, запас есть, а отзывчивость на живого
#: человека всё ещё лучше трёх секунд.
POLL_SLEEP_S = 3.0

#: Сетевой таймаут.
HTTP_TIMEOUT_S = 45.0

#: Телеграм не примет сообщение длиннее 4096 символов.
MAX_ANSWER_CHARS = 4000

#: Имена, на которые бот откликается: и своё, и чужие.
#:
#: Раньше здесь было только `ада|ada`, из-за чего бот звал Анатолия
#: по имени «толя» и не отзывался. Раз уж на сервере три бота с тремя
#: именами, а человек говорит со всеми, ловим все три набора.
#:
#: Границы слова обязательны: без них «адаптер» и «палата» звали бы
#: бота раз в двадцать сообщений.
#:
#: Порядок важен: сначала длинные формы, потом короткие. Регулярка
#: ищет слева направо и на «толя» раньше «толян» не сможет — на
#: «толян» отработает «толян», но на «толе» пришлось бы писать
#: отдельную форму, а вот при обратном порядке «толик» съедался бы
#: внутри «толик» как «тол» + остаток. Поэтому длинное раньше.
#:
#: `re.IGNORECASE` даёт верхний регистр автоматически: «АДА», «Ada»,
#: «ТОЛЯ», «KATY» не нужно перечислять по отдельности.
_NAME_WORDS = (
    # Анатолий и его варианты — длинные раньше коротких.
    "анатолий", "анатолик", "толян", "толик", "толька", "толя", "толе",
    # Кети.
    "катюха", "катюх", "кейти", "кэти", "кети", "кэт", "кет",
    "кать", "катя", "katy", "katya",
    # Ада.
    "ада", "ada",
)
NAME_TRIGGERS = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z])(?:" + "|".join(_NAME_WORDS) + r")(?![А-Яа-яЁёA-Za-z])",
    re.IGNORECASE)

ASK_WHAT = ("Что хотели узнать? Напишите вопрос — "
            "по имени звать не обязательно, я всё равно читаю.")

PERSONA = (
    "Ты — Ада, девушка-помощница в чате.\n"
    "Ты отвечаешь не на каждое сообщение, а на каждое десятое и когда "
    "тебя позвали по имени. Поэтому твой ответ должен быть самостоятельным: "
    "не пересказывай всё подряд, отвечай по существу последней реплики или "
    "по ходу разговора.\n"
    "Если вопросов в сообщении нет, а это просто реплика в беседе, ответь "
    "по теме разговора одной фразой или коротко поддержи разговор.\n"
    "Не выдумывай факты и не говори, чего не знаешь."
)

#: Состояние чатов: счётчик реплик, признак «к ней обратились»,
#: кольцевой буфер реплик и время последнего пульса.
CHATS: dict[int, dict[str, Any]] = {}
#: Имя собственного бота: сообщения от него самого и от других ботов
#: пропускаются, иначе Ада отвечает сама себе.
OWN_ID: dict[str, Any] = {"id": None}


def chat_state(chat_id: int) -> dict[str, Any]:
    """Состояние чата, созданное при первом обращении."""
    state = CHATS.get(chat_id)
    if state is None:
        state = {"count": 0, "addressed": False, "beat": 0.0,
                 "history": deque(maxlen=CONTEXT_MESSAGES)}
        CHATS[chat_id] = state
    return state


def bot_persona(data: dict[str, Any]) -> dict[str, Any]:
    """Лицо бота: из настроек или наугад, но не повторяя прошлый раз.

    Имя и аватар у Ады меняются от запуска к запуску, иначе через месяц
    «Ада» перестаёт быть именем, а становится подписью. Ключ от
    предыдущего выбора хранится рядом с токеном в том же файле настроек.
    """
    wanted = str(data.get("persona_key") or "")
    picked = (persona_by_key(wanted)
              if wanted else None) or pick_persona(exclude=None)
    return picked


def build_persona_text(face: dict[str, Any],
                      data: dict[str, Any],
                     chat_id: int = 0) -> str:
    """Подсказка модели: кто она, как говорит, какие слова разбирать.

    Лицо, стиль и таблица иностранных слов собираются здесь, чтобы
    правила речи жили в одном месте — в persona.py, а не в каждом боте
    своей копией. Свой текст в настройках, если он есть, добавляется
    последним: он главнее общих правил, потому что его писал человек
    под эту задачу.
    """
    parts = [chars_mod.prompt(
        str(data.get("persona_key") or face.get("key") or "ada"),
        chat_id)]
    own = str(data.get("persona") or "").strip()
    if own:
        parts.append(own)
    return "\n\n".join(parts)


def load_config() -> dict[str, Any]:
    """Настройки: файл рядом, приоритет у переменной окружения."""
    data: dict[str, Any] = {}
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    token = os.environ.get("TELEGRAM_TOKEN") or data.get("telegram_token") or ""
    providers = data.get("providers") or []
    active = int(data.get("active") or 0)
    if providers and not 0 <= active < len(providers):
        active = 0
    face = bot_persona(data)
    return {"token": token, "providers": providers, "active": active,
            "persona_key": str(data.get("persona_key") or ""),
            "persona_key": str(data.get("persona_key") or ""),
            "persona": build_persona_text(face, data, 0),
            # Подсказка пересобирается на каждое сообщение:
            # характер настраивается лично, по чату.
            "char_key": str(data.get("persona_key")
                              or face.get("key")
                              or "ada"),
            "face": face,
            "heartbeat_chat_id": str(data.get("heartbeat_chat_id") or "")}


# ------------------------------------------------------------- телеграм


#: Сколько раз повторять запрос, который оборвался по сети.
#:
#: Туннель PythonAnywhere мигает: при первом запуске бот спокойно дошёл
#: до Telegram и получил от него настоящий ответ, а через полчаса тот же
#: самый запрос вернул 503 на первой же строке запуска. Значит дело не
#: в запросе, а в соединении, и единственное верное действие — повторить.
#:
#: Раньше повторов не было вовсе, и один обрыв на старте завершал бота
#: словами «Telegram недоступен»: он выходил и больше не поднимался.
#: Теперь обрыв на старте просто ждёт и пробует снова.
TELEGRAM_RETRIES = 12

#: Пауза между повторами. Растёт, чтобы не долбить в закрытую дверь:
#: секунда, две, четыре и так далее.
TELEGRAM_BACKOFF_S = 1.0


def telegram(token: str, method: str, **params: Any) -> Any:
    """Вызов Telegram API с повторами на обрыв соединения.

    Повтор делается не на все ошибки, а только на сетевые. Отказ
    самого Telegram — например, «chat not found» — повторять бессмысленно
    и медленно: он не изменится. А вот обрыв туннеля проходит сам, и
    ждать — правильно.
    """
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}).encode()

    last: Exception | None = None
    for attempt in range(1, TELEGRAM_RETRIES + 1):
        try:
            request = urllib.request.Request(url, data=body, method="POST")
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                # Это ответ Telegram, а не обрыв: повтор не поможет.
                raise RuntimeError(payload.get("description")
                                   or "Telegram отказал")
            return payload.get("result")
        except urllib.error.HTTPError:
            # HTTP-ответ пришёл, значит сеть жива. Повтор не нужен.
            raise
        except Exception as exc:
            last = exc
            if attempt == TELEGRAM_RETRIES:
                break
            wait = TELEGRAM_BACKOFF_S * attempt
            print(f"обрыв связи ({type(exc).__name__}), "
                  f"повтор {attempt} из {TELEGRAM_RETRIES} "
                  f"через {wait:.0f} с", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Telegram недоступен после "
                       f"{TELEGRAM_RETRIES} попыток: {last}")


def send(token: str, chat_id: int | str, text: str) -> None:
    """Отправить текст. Ошибка отправки не должна ронять бота."""
    try:
        telegram(token, "sendMessage", chat_id=chat_id,
                 text=text[:MAX_ANSWER_CHARS],
                 disable_web_page_preview="true")
    except Exception as exc:
        print(f"не отправилось: {exc}", flush=True)


# ------------------------------------------------------------- модель


def ask_model(provider: dict[str, Any], prompt: str,
              system: str = "") -> str:
    """Спросить модель через адрес, совместимый с OpenAI.

    Такой адрес у openrouter, groq, mistralai, nvidia, z_ai: одна форма
    запроса на всех, и ключ провайдера уходит только ему.
    """
    base = str(provider.get("base_url") or "").rstrip("/")
    if not base:
        return "Провайдер не настроен: пустой base_url."
    key = str(provider.get("api_key") or "")
    if not key:
        return (f"У провайдера «{provider.get('name', '?')}» нет ключа — "
                "впишите api_key в config.json на сервере.")
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": provider.get("model") or "",
        "messages": messages,
        # Короткий ответ: на бесплатном тарифе это ещё и экономит квоту,
        # а Аде положено отвечать одной-двумя фразами.
        "max_tokens": 400,
        "temperature": 0.6,
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
        # Тело ошибки полезно при настройке и бесполезно читателю чата:
        # там имена полей и коды провайдера.
        raise RuntimeError(f"провайдер ответил HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"сеть недоступна: {exc.reason}") from exc
    choices = data.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("message", {}).get("content") or "").strip()


# ------------------------------------------------------------- статусы


def uptime() -> str:
    """Время работы словами."""
    seconds = int(time.time() - STARTED_AT)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин"
    return f"{secs} с"


def status_text(config: dict[str, Any]) -> str:
    """Сводка о боте и о том, как с ним общаться."""
    lines = status_lines(config, uptime(), len(CHATS))
    lines += ["", "Как со мной говорить:",
              "• позовите по имени вместе с вопросом — отвечу сразу",
              "• можно позвать отдельно, а вопрос задать следующим",
              f"• в беседе я отвечаю на каждое {EVERY_N}-е сообщение",
              "• в личном чате отвечаю на всё",
              "• /forget — забыть этот разговор"]
    return "\n".join(lines)


def build_prompt(state: dict[str, Any]) -> str:
    """Собрать запрос из последних реплик чата.

    Реплики идут списком с именами — так модели проще понять, о чём
    речь, чем если свалить их в одну простыню без разделителей.
    """
    if not state["history"]:
        return ""
    lines = ["Реплики в чате по порядку (последняя — свежая):"]
    lines += [f"— {who}: {text}" for who, text in state["history"]]
    lines.append("")
    # Просьба развёрнутая, а не «одна-две фразы»: уровень детализации
    # задаётся в STYLE, и противоречащая ему просьба в конце перебила бы
    # его — модель читает снизу вверх и последнее видит яснее.
    lines.append("Ответь на последнюю реплику или на ход разговора, "
                 "по-русски. Иностранные слова разбирай по правилу из "
                 "подсказки.")
    return "\n".join(lines)


# ------------------------------------------------------------- команды

def _cmd_help(config: dict[str, Any], state: dict[str, Any],
              chat_id: int) -> str:
    """Справка по командам."""
    return commands_help(config["face"])


def _cmd_status(config: dict[str, Any], state: dict[str, Any],
                chat_id: int) -> str:
    """Состояние бота."""
    return status_text(config)


def _cmd_whoami(config: dict[str, Any], state: dict[str, Any],
                chat_id: int) -> str:
    """Идентификатор чата — нужен, чтобы настроить пульс."""
    return f"Ваш chat_id: {chat_id}"


def _cmd_name(config: dict[str, Any], state: dict[str, Any],
              chat_id: int) -> str:
    """Какое сейчас имя у бота."""
    return name_text(config["face"])


def _cmd_forget(config: dict[str, Any], state: dict[str, Any],
                chat_id: int) -> str:
    """Забыть разговор.

    История очищается здесь, а команда в неё не попадает: разбор
    команд идёт раньше записи в буфер. Иначе бот запомнил бы
    собственную команду и следующий ответ опирался бы на слово
    «/forget» вместо чистого листа.
    """
    state["history"].clear()
    state["addressed"] = False
    return forget_text(config["face"])


#: Команды и их обработчики. Словарь, а не цепочка сравнений: новую
#: команду добавляется одной строкой, и её невозможно забыть в
#: справке, потому что справка строится из того же набора.
COMMANDS = {
    "/start": _cmd_help,
    "/help": _cmd_help,
    "/status": _cmd_status,
    "/whoami": _cmd_whoami,
    "/name": _cmd_name,
    "/forget": _cmd_forget,
}


# ------------------------------------------------------------- разбор


def handle(config: dict[str, Any], message: dict[str, Any]) -> None:
    """Одно входящее сообщение."""
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = str(message.get("text") or "").strip()
    if chat_id is None or not text:
        return
    # Чужие боты и собственные реплики: иначе Ада отвечает сама себе.
    if (message.get("from") or {}).get("is_bot"):
        return
    sender = (message.get("from") or {}).get("first_name") or "участник"
    is_private = str(chat.get("type") or "") == "private"

    token = config["token"]
    state = chat_state(int(chat_id))
    state["count"] += 1

    # Команды разбираются до записи в историю. Если писать раньше,
    # то через десять вызовов модель получает переписку из «/help»
    # и «/status» и начинает отвечать на список команд.
    command = text.split()[0].lower().split("@")[0]
    # Команды разбираются ДО записи в историю. Если писать раньше, то
    # через десять вызовов модель получает переписку из «/help» и
    # «/status» и начинает отвечать на список команд.
    if command in COMMANDS:
        answer = COMMANDS[command](config, state, chat_id)
        if answer:
            send(token, chat_id, answer)
        return

    state["history"].append((sender, text[:500]))

    # Обращение по имени — самый прямой вызов. Раньше здесь отправлялась
    # заглушка «Что хотели узнать?», и на скриншоте видно, чем это
    # кончается: человек звал по имени, а получал отказ отвечать на его
    # же вопрос. Поэтому при имени сразу отвечаем на текст.
    if NAME_TRIGGERS.search(text):
        state["addressed"] = True
        stripped = NAME_TRIGGERS.sub("", text).strip(" ,.:!?—-")
        # Голое «Ада» без ничего — это всё ещё просто зовущий, и на него
        # отвечать нечем: вот тут заглушка и уместна. А «Ада, сколько
        # нужно принести» — это уже вопрос, и на него отвечаем.
        if len(stripped) < 3:
            send(token, chat_id, ASK_WHAT)
            return
        state["history"][-1] = (sender, stripped[:500])
        # Дальше работаем с текстом без имени: иначе «Ада, как
        # дела?» ушло бы в приветствие вместе с обращением.
        text = stripped

    # Первичная логика: бот отвечает сам, не звая модель.
    #
    #    Арифметику и время надо считать кодом, а не моделью:
    #    модель посчитает «17*23» с вероятностью опечатки, а
    #    выражение в Python — без. И пока ключ модели недоступен
    #    или сеть моргнула, бот остаётся полезным, а не
    #    превращается в извиняющуюся заглушку.
    key = config.get("persona_key") or "ada"
    quick = chars_mod.builtin_answer(key, text)
    if quick:
        send(token, chat_id, quick)
        return

    # Настройка характера: команда человека, а не вопрос к модели.
    #    Отвечать тут должен сам бот: модель о настройках ничего
    #    не знает и ответила бы наугад.
    for cmd in chars_mod.CHAR_CMDS:
        if text.lower().startswith(cmd):
            answer, _ = chars_mod.char_command(
                key, chat_id, text[len(cmd):])
            send(token, chat_id, answer)
            return



    # В беседе — только каждое десятое сообщение или когда позвали.

    if not is_private and not state["addressed"] and state["count"] % EVERY_N:
        return
    state["addressed"] = False

    providers = config["providers"]
    if not providers:
        send(token, chat_id, "Модели не настроены на сервере.")
        return
    prompt = build_prompt(state)
    if not prompt:
        return
    try:
        # Подсказка — под этого собеседника: его настройки
        # характера, а не чужие.
        system = chars_mod.prompt(config.get("char_key")
                                      or "ada", chat_id)
        answer = ask_model(providers[config["active"]], prompt, system)
        # Одноразовый признак гасится после ответа: «максимум» должен
        # означать один раз, иначе каждый следующий вопрос снова
        # получал бы простыню.
        chars_mod.consume_one_shot(config.get("char_key") or "ada", chat_id)
    except Exception as exc:
        answer = f"Не получилось ответить: {exc}"
    if answer:
        send(token, chat_id, answer)


def heartbeat(config: dict[str, Any]) -> None:
    """Пульс раз в HEARTBEAT_S секунд, только в личные чаты.

    В беседе он был бы шумом для всех сразу, поэтому туда не ходим.
    """
    target = config.get("heartbeat_chat_id")
    if not target:
        return
    state = CHATS.get(int(target))
    if not state:
        return
    now = time.time()
    if now - state["beat"] < HEARTBEAT_S:
        return
    state["beat"] = now
    active = (config["providers"][config["active"]]
              if config["providers"] else {})
    send(config["token"], target,
         f"• работаю {uptime()}\n"
         f"• модель: {active.get('name', 'не настроена')}\n"
         f"• жду вопроса")


def set_avatar(config: dict[str, Any]) -> bool:
    """Поставить текущему лицу аватар. Ошибка — не повод падать.

    Аватар меняется при каждом запуске, но смена не критична: без неё
    бот просто остаётся со старой картинкой и продолжает работать. Раньше
    об этом не подумали, и из-за одного отказа Telegram переставал
    запускаться вовсе.
    """
    face = config["face"]
    try:
        photo = avatar_png(face, 256)
        url, body, headers = profile_photo_request(config["token"], photo, face)
        request = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST")
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except urllib.error.HTTPError as exc:
        # 404 на `setMyProfilePic` — не поломка бота. Метод недоступен
        # этому токену у самого Telegram: проверено напрямую, что
        # `getMe`, `getUpdates` и `sendMessage` отвечают, а оба метода
        # смены аватара возвращают 404. Раньше эта строка печаталась
        # при каждом запуске и выглядела как поломка, хотя бот после
        # неё работал. Теперь она молчит, а аватар ставится вручную
        # через @BotFather — командой /setuserpic.
        if exc.code != 404:
            print(f"аватар не поставился: HTTP {exc.code}")
        return False
    except Exception as exc:
        print(f"аватар не поставился: {exc}", flush=True)
        return False


def main() -> None:
    config = load_config()
    if not config["token"]:
        raise SystemExit(
            "Нет токена Telegram: впишите telegram_token в config.json "
            "на сервере или задайте переменную TELEGRAM_TOKEN.")
    try:
        # Старый вебхук мешает long polling: обновления не приходят,
        # а бот выглядит зависшим.
        telegram(config["token"], "deleteWebhook",
                 drop_pending_updates="false")
        me = telegram(config["token"], "getMe")
        OWN_ID["id"] = me.get("id")
        print(f"запущена как {config['face']['name']}: "
              f"@{me.get('username')} (id {me.get('id')})",
              flush=True)
    except Exception as exc:
        raise SystemExit(f"Telegram недоступен: {exc}") from exc
    print(f"моделей: {len(config['providers'])}", flush=True)
    if set_avatar(config):
        print(f"аватар: {config['face']['name']}", flush=True)

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
            heartbeat(config)
        except KeyboardInterrupt:
            print("остановлена", flush=True)
            return
        except Exception as exc:
            # Сеть моргнула — ждём и продолжаем: бот не должен умирать
            # из-за одного обрыва. Пауза здесь длиннее обычной, потому
            # что туннель может лежать минутами, и короткая пауза
            # превратилась бы в сотни попыток впустую.
            print(f"ошибка опроса: {exc}", flush=True)
            time.sleep(POLL_SLEEP_S * 2)
        time.sleep(POLL_SLEEP_S)


if __name__ == "__main__":
    main()
