r"""Кнопки: что делает каждая и как на них отвечать.

Зачем этот файл
---------------
`handle()` должен оставаться читаемым: он разбирает сообщение и
уходит. Всё, что нужно кнопкам, живёт здесь — иначе он разросся
бы на сотню строк, и в нём было бы невозможно найти, где
обрабатывается обычный текст.

Как связано с `buttons.py`
--------------------------
`buttons.py` знает, **какие** бывают действия и как их нарисовать.
Здесь — **что каждое делает**. Список действий один: он же
используется для проверки, что нажатие вообще известно, иначе
нажатие чужой кнопки ушло бы в модель.

Почему не в `user_features.py`
------------------------------
Там лежат тексты ответов, и они зависят только от лица бота.
Здесь часть действий зависит ещё и от настроек: список моделей и
состояние — из `config`. Разные зависимости — разные места.
"""

from __future__ import annotations

from typing import Any

import buttons as buttons_mod
import chars as chars_mod
import greetings
import memory as memory_mod
import modelswitch
import user_features

#: Какие действия бывают. Тот же набор, что рисуется в меню, и по
#: нему же проверяется нажатие: неизвестное действие отбрасывается,
#: а не уходит в модель пустым текстом.
BUTTON_ACTIONS = frozenset(buttons_mod.ACTIONS)


def answer_callback(token: str, callback_id: str,
                    notice: str = "") -> None:
    """Снять анимацию с нажатой кнопки.

    Telegram ждёт этого ответа, и без него на кнопке до минуты
    крутится часик — выглядит так, будто бот завис. Ошибка здесь
    ничего не должна ронять: это косметика.
    """
    if not callback_id:
        return
    params: dict[str, Any] = {"callback_query_id": callback_id}
    if notice:
        params["text"] = notice[:180]
    try:
        # Импорт внутри функции: `telegram()` живёт в `ada_bot`,
        # а он импортирует этот модуль. Наверху был бы круг.
        from ada_bot import telegram
        telegram(token, "answerCallbackQuery", **params)
    except Exception as exc:
        print(f"часики не сняты: {exc}", flush=True)


def start_text(config: dict[str, Any]) -> str:
    """Текст `/start`: кто я и что можно нажать.

    Короткое, но достаточное: после этой команды человек должен
    понять, что кнопки есть, и не читать простыню. Раньше здесь
    был абзац и «Все команды — /help», после чего человек не знал,
    что делать дальше.
    """
    face = config.get("face") or {}
    char_key = str(config.get("char_key") or "ada")
    name = str(face.get("name") or "бот")
    username = str(config.get("username") or "")

    lines = [
        greetings.short_greeting(char_key),
        "",
        f"Зовут {name}. Зови так: «{name}» или «{name.lower()}».",
    ]
    # Подпись в Telegram может отличаться от имени в коде: так
    # вышло с двумя ботами, оба подписаны «Анатолий». Показываем
    # настоящее имя учётной записи, иначе человек зовёт не того.
    if username:
        lines.append(f"В Telegram я: @{username}.")
    lines += [
        "",
        "Кнопки ниже — то же, что команды, только нажимаются. "
        "Можно и написать цифру руками.",
    ]
    return "\n".join(lines)


def do_action(config: dict[str, Any], state: dict[str, Any],
              chat_id: int, action: str, arg: str = "") -> str:
    """Что ответить на нажатие кнопки или на цифру.

    С аргументом — переключение, без него — показать список.
    Пустая строка означает «ответа нет», и тогда в чат не уходит
    ничего.
    """
    face = config.get("face") or {}
    char_key = str(config.get("char_key") or "ada")

    if action == "char":
        answer, _ = chars_mod.char_command(char_key, chat_id, "")
        return answer or "Характер настроен по умолчанию."

    if action == "model":
        # С числом после цифры — переключить на этот номер.
        if arg:
            return modelswitch.switch(config, arg)
        return modelswitch.models_list(config)

    if action == "forget":
        state["history"].clear()
        state["addressed"] = False
        # Память на диске тоже: забыть разговор и забыть
        # человека — разные вещи, но кнопка одна.
        memory_mod.forget(char_key, chat_id)
        return user_features.forget_text(face)

    if action == "name":
        return user_features.name_text(face)

    if action == "status":
        from ada_bot import status_text
        return status_text(config)

    if action == "help":
        return user_features.commands_help(face)

    return "Не понимаю, что нажато."


#: Короткие имена для вызова из `handle()`. Совпадают с тем, что
#: там написано, и меняются только вместе с ним.
_answer_callback = answer_callback
_start_text = start_text
_do_action = do_action