r"""Точка входа веб-приложения PythonAnywhere: поднимает бота и отвечает на пинг.

Зачем веб-приложение, если бот — это polling
-------------------------------------------
На бесплатном тарифе PythonAnywhere постоянные задачи недоступны: бот,
который сам ходит в Telegram за обновлениями, работать негде. Веб-приложение
же работает постоянно — и это единственное место, где может жить процесс.

Поэтому здесь два дела сразу:

* в фоновом потоке запускается `ada_bot.main()` — тот самый цикл
  long polling, который при нормальном запуске живёт вечно;
* сам WSGI-обработчик отвечает быстро и по делу: `/` показывает статус,
  `/healthz` отдаёт «ok». Это нужно проверке: PythonAnywhere считает
  приложение упавшим, если ответ не пришёл быстро.

Почему именно поток, а не просто вызов
-------------------------------------
WSGI-об��ботчик должен вернуться, иначе веб-приложение решит, что он
завис. `main()` же крутится бесконечно, поэтому он уезжает в поток, а
поток — демон: перезапуск веб-приложения тогда не ждёт его завершения.

Ограничение, о котором важно помнить
------------------------------------
`LONG_POLL_S` в боте равен трём секундам, и это не случайно: прокси
PythonAnywhere рвёт соединение, висящее дольше. Поэтому опрос короткий,
а пауза между опросами задаёт сам бот.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

#: Когда процесс поднялся. Для /healthz и страницы статуса.
STARTED_AT = time.time()

#: Поток с ботом: нужно держать ссылку, иначе сборщик мусора решит,
#: что он больше не нужен, и уберёт его — вместе с работающим ботом.
_bot_thread: threading.Thread | None = None
_bot_error: str = ""


def start_bot() -> None:
    """Запустить `ada_bot.main()` в фоновом потоке.

    Ошибка не должна ронять веб-приложение: без бота страница статуса
    всё равно должна открываться, иначе нечем понять, что сломалось.
    """
    global _bot_thread, _bot_error

    if _bot_thread is not None:
        return

    def runner() -> None:
        global _bot_error
        try:
            import ada_bot
            ada_bot.main()
        except SystemExit as exc:
            # Ожидаемый выход: нет токена или Telegram недоступен.
            _bot_error = str(exc) or "вышел с кодом 0"
        except Exception as exc:  # pragma: no cover - серверная ветка
            _bot_error = f"{type(exc).__name__}: {exc}"

    _bot_thread = threading.Thread(target=runner, name="ada-bot", daemon=True)
    _bot_thread.start()


def bot_alive() -> bool:
    """Жив ли поток с ботом."""
    return _bot_thread is not None and _bot_thread.is_alive()


def _page(code: int, body: str) -> tuple[int, list[tuple[str, str]], bytes]:
    """Собрать ответ WSGI."""
    data = body.encode("utf-8")
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(data))),
    ]
    return code, headers, data


def _status_page() -> str:
    """Страница статуса: видно, запущен ли бот и какая ошибка, если нет."""
    import ada_bot  # noqa: F401  — только ради CONFIG_PATH и импортов

    config_path = HERE / "config.json"
    have_config = config_path.is_file()
    have_token = False
    if have_config:
        try:
            import json

            have_token = bool(
                json.loads(config_path.read_text(encoding="utf-8"))
                .get("telegram_token"))
        except Exception:
            have_token = False

    uptime = int(time.time() - STARTED_AT)
    rows = [
        f"<li><b>время работы:</b> {uptime} с</li>",
        f"<li><b>бот:</b> {'работает' if bot_alive() else 'не запущен'}</li>",
        f"<li><b>config.json:</b> {'есть' if have_config else 'НЕТ'}</li>",
        f"<li><b>токен:</b> {'есть' if have_token else 'НЕТ'}</li>",
    ]
    if _bot_error:
        # Ошибка целиком в разметку не идёт: текст из config.json может
        # содержать символы, которые её сломают.
        safe = (_bot_error.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))
        rows.append(f"<li><b>ошибка бота:</b> <pre>{safe}</pre></li>")
    if not have_token:
        rows.append("<li>Чтобы бот заработал: перевыпустить токен у "
                    "@BotFather, вписать его в config.json на сервере и "
                    "перезапустить приложение.</li>")
    return ("<html><head><meta charset='utf-8'>"
            "<title>Ада — статус</title></head>"
            "<body style='font-family:sans-serif;max-width:40em;padding:2em'>"
            "<h1>Ада</h1><ul>" + "".join(rows) + "</ul></body></html>")


def application(environ, start_response):
    """Точка входа WSGI."""
    # Бот поднимается при первом же запросе, а не на импорте: импорт
    # модуля на сервере PythonAnywhere происходит и при проверке
    # конфигурации, и лишний polling там не нужен.
    if environ.get("PATH_INFO", "") in ("/", "") and not bot_alive():
        start_bot()

    path = environ.get("PATH_INFO", "") or "/"
    if path == "/healthz":
        code, headers, data = _page(200, "ok")
    else:
        try:
            code, headers, data = _page(200, _status_page())
        except Exception as exc:  # pragma: no cover - серверная ветка
            code, headers, data = _page(
                500, f"<pre>{type(exc).__name__}: {exc}</pre>")
    start_response(f"{code} OK", headers)
    return [data]


#: Имя, которое ищет сервер: wsgi.py → переменная `application`.
app = application

# Переменная окружения на случай, если имя привычнее другое.
wsgi_app = application


if __name__ == "__main__":  # pragma: no cover - локальный запуск
    start_bot()
    print(f"бот поднят: {bot_alive()}", flush=True)
    while True:
        time.sleep(3600)