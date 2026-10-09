r"""Кнопки в чате: отправка с меню и разбор нажатий.

Зачем этот файл
---------------
Человек попросил управлять ботами кнопками `1 2 3`, а не командами
в тексте. Пока такой возможности не было вовсе: `reply_markup` не
упоминался ни в одном файле проекта, и `send()` умел отправить
только текст.

Здесь три вещи:

1. **`send_buttons()`** — отправить сообщение с кнопками под ним.
2. **`main_menu()`** — само меню: по одной кнопке на каждое
   действие, с цифрами, потому что человек описал именно цифры.
3. **`action_of()`** — разобрать нажатие и вернуть действие.

Почему действия, а не текст кнопки
---------------------------------
Текст кнопки человек видит, а Telegram передаёт боту его
`callback_data`. Если писать в кнопку «Мой характер», то в коде
придётся сравнивать с этим текстом, и любая правка надписи
ломает кнопку молча. Поэтому в кнопке короткое имя действия, а
человеку показывается понятная надпись.

Почему цифры
-----------
Три цифры нажимаются вслепую и не занимают место. В беседе, где
ботов трое, человек не смотрит в чат перед нажатием.
"""

from __future__ import annotations

import json
from typing import Any

#: Сколько символов в тексте кнопки. Telegram не любит длинные,
#: а телефон показывает обрезанное слово, и нажимать неудобно.
LABEL_LIMIT = 24

#: Что означает каждая кнопка: действие -> надпись для человека.
#:
#: Порядок важен: он задаёт и порядок кнопок, и цифры рядом с
#: ними. Человек описал «1 2 3 или чет такое», поэтому действия
#: идут от самого частого к самому редкому.
ACTIONS: dict[str, str] = {
    "char": "Характер",
    "model": "Модель",
    "forget": "Забыть разговор",
    "name": "Кто я",
    "status": "Состояние",
    "help": "Все команды",
}


def main_menu(per_row: int = 2) -> dict[str, Any]:
    """Клавиатура главного меню.

    `per_row` — сколько кнопок в ряду. При двух на телефоне
    кнопки не сжимаются в нечитаемые полоски.
    """
    rows: list[list[dict[str, str]]] = []
    buttons: list[tuple[str, str]] = list(ACTIONS.items())
    for start in range(0, len(buttons), per_row):
        rows.append([
            {"text": f"{start + offset + 1}. {label}"[:LABEL_LIMIT],
             "callback_data": action}
            for offset, (action, label) in
            enumerate(buttons[start:start + per_row])
        ])
    return {"inline_keyboard": rows}


def action_of(callback: dict[str, Any] | None) -> str:
    """Что человек нажал. Пустая строка — нажатия не было.

    Поле может прийти и как `str`, и как `dict`: первый формат —
    от нашего бота, второй приходит, если callback кто-то подделал
    через чужой код. Обрабатываем оба, иначе подделка роняет бота.
    """
    if not isinstance(callback, dict):
        return ""
    data = callback.get("data")
    if isinstance(data, dict):
        data = data.get("action")
    if not isinstance(data, str):
        return ""
    data = data.strip()
    return data if data in ACTIONS else ""


def parse(text: str) -> tuple[str, str]:
    """Разобрать цифру или команду в тексте.

    Возвращает пару: что нажато и хвост команды. Нужна потому, что
    человек может и не нажать кнопку, а написать «2» руками — а
    может и наоборот, отправить команду текстом, не нажимая
    ничего. Оба пути должны вести в одно место.
    """
    text = str(text or "").strip()
    if not text:
        return "", ""
    head, _, tail = text.partition(" ")
    head = head.lower()
    # Цифра: и с командой, и без. «2» — это модель, «2 qwen» тоже
    # модель, только с аргументом.
    if head.isdigit():
        index = int(head) - 1
        actions = list(ACTIONS)
        if 0 <= index < len(actions):
            return actions[index], tail.strip()
        return "", ""
    if head.startswith("/"):
        return head, tail.strip()
    return "", ""