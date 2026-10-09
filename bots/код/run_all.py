r"""Запуск всех ботов сразу: Ада, Анатолий, Кети.

Зачем этот файл
---------------
Раньше ботов приходилось поднимать по одному, в отдельной консоли,
и каждый раз вписывать токен руками. Три бота — это три копии
одного и того же кода, и держать их по отдельности невозможно: на
бесплатном тарифе PythonAnywhere разрешена одна консоль.

Поэтому запускается один процесс, а внутри — по потоку на бота.
Каждый поток получает свой токен и свою персону, но ту же логику
ответов: имена и частота ответов заданы в `ada_bot.py` и общие
для всех, чтобы Ада, Анатолий и Кети вели себя одинаково.

Почему потоки, а не три процесса
--------------------------------
Три процесса съели бы по интерпретатору, а на бесплатном тарифе
PythonAnywhere память ограничена. Поток дешевле, а состояние чатов
и так общее — оно и раньше было одним словарём на процесс.

Запуск:
    cd /home/HostMoon6/zstatus
    export ADA_TOKEN=...
    export ANATOLY_TOKEN=...
    export Social_TOKEN=...
    python3 -u run_all.py

Чтобы добавить бота, достаточно дописать объект в `bots.json`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ada_bot  # noqa: E402
import persona as persona_mod  # noqa: E402

BOTS_PATH = HERE / "bots.json"
CONFIG_PATH = HERE / "config.json"

#: Пауза перед перезапуском упавшего бота.
#:
#: Небольшая: падать тут нечему, а если сеть моргнула, повтор внутри
#: `telegram()` уже отработал двенадцать раз. Пауза тут только для
#: случая, когда упало что-то совсем неожиданное.
RESTART_SLEEP_S = 5.0


def load_bots() -> list[dict[str, str]]:
    """Список ботов из bots.json."""
    if not BOTS_PATH.is_file():
        raise SystemExit(f"Нет файла {BOTS_PATH.name} — положите рядом с ботом.")
    try:
        data = json.loads(BOTS_PATH.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"{BOTS_PATH.name} не читается: {exc}") from exc
    bots = data.get("bots") or []
    if not bots:
        raise SystemExit(f"В {BOTS_PATH.name} пустой список ботов.")
    return bots


def base_config() -> dict:
    """Общая часть настроек: модель и всё, что не зависит от бота."""
    if not CONFIG_PATH.is_file():
        raise SystemExit(f"Нет файла {CONFIG_PATH.name} — положите рядом.")
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    return data


def run_one(entry: dict[str, str], shared: dict) -> None:
    """Один бот в своём потоке, с перезапуском при падении."""
    key = str(entry.get("key") or "?")
    label = str(entry.get("name") or key)
    env = str(entry.get("token_env") or "")
    token = os.environ.get(env, "").strip()

    if not token:
        # Молча пропускаем: токена может не быть вовсе, и это не повод
        # ронять остальных ботов. Наверху печатается, кого не хватает.
        return

    while True:
        try:
            config = dict(shared)
            config["telegram_token"] = token
            config["persona_key"] = str(entry.get("persona_key") or key)
            os.environ["TELEGRAM_TOKEN"] = token
            ada_bot.OWN_ID["id"] = 0
            ada_bot.CHATS.clear()
            ada_bot.main()
            return  # main() выходит только по Ctrl+C
        except KeyboardInterrupt:
            print(f"[{label}] остановлен", flush=True)
            return
        except SystemExit as exc:
            # Ошибка настроек: повтор не поможет, но и молча уходить
            # нельзя — иначе человек увидит пустую консоль.
            print(f"[{label}] не запускается: {exc}", flush=True)
            time.sleep(RESTART_SLEEP_S)
        except Exception as exc:
            print(f"[{label}] упал: {type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            time.sleep(RESTART_SLEEP_S)


def main() -> int:
    bots = load_bots()
    shared = base_config()

    print(f"ботов в списке: {len(bots)}", flush=True)

    # Проверяем токены заранее, до запуска потоков: так сразу видно,
    # кто пришёл, а кто нет, и не надо гадать по молчанию.
    ready: list[dict[str, str]] = []
    for entry in bots:
        label = str(entry.get("name") or entry.get("key"))
        env = str(entry.get("token_env") or "")
        token = os.environ.get(env, "").strip()
        if not token:
            print(f"  {label:<10} нет переменной {env} — пропускаю", flush=True)
            continue
        try:
            me = ada_bot.telegram(token, "getMe")
            print(f"  {label:<10} @{me.get('username'):<20} "
                  f"персона {entry.get('persona_key')}", flush=True)
            ready.append(entry)
        except Exception as exc:
            print(f"  {label:<10} Telegram недоступен: {exc}", flush=True)

    if not ready:
        raise SystemExit(
            "Ни один бот не запустился. Проверьте токены и сеть: "
            "переменные окружения заданы?")

    print()
    print(f"запускаю {len(ready)} ботов", flush=True)
    threads = []
    for entry in ready:
        thread = threading.Thread(target=run_one, args=(entry, shared),
                                  name=str(entry.get("key")), daemon=True)
        thread.start()
        threads.append(thread)
        # Небольшая пауза между запусками: так туннель успевает
        # подняться для следующего бота, и все три не стартуют
        # одновременно в один неудачный момент.
        time.sleep(2.0)

    print("боты работают. Остановить — Ctrl+C", flush=True)
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("остановлено", flush=True)
        sys.exit(0)