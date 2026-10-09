#!/usr/bin/env python3
r"""Показывать боту картинки самого себя: при старте и по расписанию.

Зачем это
---------
Бот, который молча отвечает текстом, выглядит как строчка в терминале.
Картинка с его собственным лицом делает его тем, кем он притворяется:
человек видит, кому пишет. Поэтому Ада показывает себя при запуске и
дальше по расписанию.

Почему картинки берутся из папки, а не рисуются на ходу
-------------------------------------------------------
Рисование занимает минуты на процессоре, а Telegram ждать не будет.
Картинки готовятся заранее (`tools/make_images.py`), а бот только
показывает их. Если папка пуста, бот не молчит и не падает: он
говорит, что картинок ещё нет, и продолжает работать.

Кому показывать
---------------
Только в личные чаты. В беседе картинка каждые десять минут была бы
шумом для всех, кто там сидит, и человек ушёл бы из чата.

Расписание
----------
Раз в `SHOW_EVERY_S` секунд и сразу при старте. Раз без счётчика:
десять минут — это и есть тот период, о котором просили, а не «раз в
каждый десятый ответ».

Что отправляется
----------------
Метод `sendPhoto` с файлом на диске. Telegram показывает картинку
свёрткой в чате, и её можно открыть, не выходя из приложения.

Запуск отдельно от бота
-----------------------
    python3 show_self.py --chat 123456
"""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

#: Папка с картинками. Здесь и на сервере, и дома — одно и то же
#: правило: файлы кладёт `make_images.py`, а показывает бот.
PICTURES = Path(__file__).resolve().parent / "pictures"

#: Как часто показывать картинку, секунд.
SHOW_EVERY_S = 600

#: Расширения, которые Telegram примет как фото.
PHOTO_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

#: Подпись под картинкой. Пишется один раз на несколько кадров и
#: меняется вместе с именем бота.
CAPTION = "Я {name} — {role}. Вот так я выгляжу."

#: Что говорить, когда картинок нет. Молчание было бы хуже: человек
#: подумает, что бот сломался.
NO_PICTURES = ("Картинок у меня пока нет — они ещё рисуются. "
               "Зато я уже здесь и отвечаю.")


def token() -> str:
    """Токен из настроек или окружения."""
    from ada_bot import load_config
    config = load_config()
    value = config.get("token") or os.environ.get("TELEGRAM_TOKEN", "")
    if not value:
        raise SystemExit(
            "Нет токена Telegram: впишите telegram_token в config.json "
            "или задайте переменную TELEGRAM_TOKEN.")
    return value


def chat_id() -> int | str:
    """Кому показывать. Берётся из настроек: так настраивается пульс."""
    from ada_bot import load_config
    config = load_config()
    target = config.get("heartbeat_chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not target:
        raise SystemExit(
            "Не указан чат. Впишите heartbeat_chat_id в config.json —\n"
            "это ваш chat_id, его показывает команда /whoami.")
    return target


def face() -> dict[str, object]:
    """Текущее лицо бота: имя и роль для подписи."""
    from ada_bot import load_config
    config = load_config()
    return config.get("face") or {"name": "Ада", "role": "помощница в чате"}


def pictures() -> list[Path]:
    """Картинки, пригодные к отправке.

    Пустая папка — не ошибка, а обычное состояние: бот продолжает
    работать и говорит, что показать пока нечего.
    """
    if not PICTURES.is_dir():
        return []
    return sorted(p for p in PICTURES.iterdir()
                  if p.is_file() and p.suffix.lower() in PHOTO_SUFFIXES)


#: Потолок на одну картинку, килобайт. Telegram отправляет файл как
#: есть, поэтому это ровно тот объём, который уйдёт в сеть.
PHOTO_LIMIT_KB = 1024

#: Качество WebP, с которого начинается подбор. Ниже 80 не спускаемся:
#: иконка смазается, а экономия в двадцать килобайт её не окупит.
WEBP_QUALITY = 85

#: Минимальная сторона. Меньше 320 телефон растянет мыльно.
MIN_SIDE = 320


def to_webp(data: bytes, name: str, limit_kb: int = PHOTO_LIMIT_KB) -> tuple[bytes, str]:
    """Перекодировать картинку в WebP, если это выгодно.

    Возвращает (байты, имя). Если WebP не меньше исходника, отдаётся
    исходник: смысла менять формат ради увеличения веса нет.

    WebP выбран не по вкусу, а по факту: при том же качестве он
    легче JPEG примерно на треть, и Telegram его принимает наравне.
    Файл при этом не трогается — перекодирование идёт в памяти, и
    папка с картинками остаётся той, что нарисована.
    """
    # Pillow на сервере может отсутствовать: тогда отправляется то,
    # что есть. Признавать это ошибкой нельзя — картинка дойдёт.
    try:
        from PIL import Image
    except ImportError:
        return data, name

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return data, name

    # Прозрачность через WebP не пройдёт, поэтому PNG с альфой
    # остаётся PNG: иначе вместо прозрачности будет чёрный фон.
    if image.mode in ("RGBA", "LA", "P"):
        return data, name

    image = image.convert("RGB")
    best: bytes | None = None
    best_name = name
    quality = WEBP_QUALITY
    side = max(image.size)

    while True:
        buffer = io.BytesIO()
        image.save(buffer, "WEBP", quality=quality, method=4)
        data_webp = buffer.getvalue()
        if len(data_webp) <= limit_kb * 1024 and len(data_webp) < len(data):
            best = data_webp
            best_name = Path(name).with_suffix(".webp").name
            break
        # Качество уже на полу — уменьшаем картинку.
        if quality <= 60 or side <= MIN_SIDE:
            break
        quality -= 10
        if quality < 60 and side > MIN_SIDE:
            side = max(MIN_SIDE, side - 64)
            ratio = side / max(image.size)
            image = image.resize((max(1, int(image.width * ratio)),
                                  max(1, int(image.height * ratio))),
                                 Image.LANCZOS)

    if best is None:
        return data, name
    return best, best_name


def post_photo(token_value: str, target: int | str, path: Path,
               caption: str) -> tuple[bool, str]:
    """Отправить одну картинку. Возвращает (получилось, записка).

    Перед отправкой картинка перекодируется в WebP: встроенная
    конвертация нужна, потому что JPEG с камеры или из интернета
    весит больше мегабайта, и Telegram такой файл не примет.

    Тело собирается вручную: библиотеки для Telegram в проекте нет, а
    файл небольшой. Граница выдумывается из случайного числа — так
    она точно не встретится внутри картинки.
    """
    payload = path.read_bytes()
    name = path.name
    if payload > PHOTO_LIMIT_KB * 1024:
        # Тяжёлый файл обязан стать легче, иначе отправка не пройдёт.
        payload, name = to_webp(payload, path.name)
        if payload > PHOTO_LIMIT_KB * 1024:
            return False, (f"не влез в {PHOTO_LIMIT_KB} КБ даже после "
                           f"сжатия: {len(payload) / 1024:.0f} КБ")
    mime = mimetypes.guess_type(name)[0] or "image/png"
    boundary = "----zagent" + uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "image/png"

    fields = {
        "chat_id": str(target),
        "caption": caption,
    }
    parts = bytearray()
    for name, value in fields.items():
        parts += f"--{boundary}\r\n".encode()
        parts += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        parts += str(value).encode("utf-8")
        parts += b"\r\n"
    # Имя файла берётся из перекодированной версии: Telegram
    # показывает его в подписи к загрузке, и если расширение не
    # совпадёт с содержимым, файл откроется не как картинка.
    parts += f"--{boundary}\r\n".encode()
    parts += (f'Content-Disposition: form-data; name="photo"; '
              f'filename="{name}"\r\n').encode()
    parts += f"Content-Type: {mime}\r\n\r\n".encode()
    parts += payload
    parts += f"\r\n--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token_value}/sendPhoto",
        data=bytes(parts),
        headers={"Content-Type":
                 f"multipart/form-data; boundary={boundary}",
                 "Content-Length": str(len(parts))},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Код ошибки важнее текста: 413 значит «файл велик», 400 —
        # «чат не тот», и одно другое.
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"сеть: {exc.reason}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if not payload.get("ok"):
        return False, str(payload.get("description") or "Telegram отказал")
    return True, ""


def show_one(token_value: str, target: int | str,
             caption: str) -> bool:
    """Показать одну случайную картинку."""
    shots = pictures()
    if not shots:
        sent = post_text(token_value, target, NO_PICTURES)
        if not sent:
            print(f"  не отправилось даже сообщение: {sent}", flush=True)
        else:
            print("  картинок нет — отправил пояснение", flush=True)
        return False
    # Случайный выбор: при поочерё��ном показе человек перестаёт
    # смотреть, при случайном — смотрит.
    chosen = random.choice(shots)
    ok, note = post_photo(token_value, target, chosen, caption)
    if ok:
        print(f"  отправил {chosen.name}", flush=True)
    else:
        print(f"  {chosen.name} не отправился: {note}", flush=True)
    return ok


def post_text(token_value: str, target: int | str, text: str) -> str:
    """Обычное сообщение. Нужно для случая «картинок нет»."""
    body = urllib.parse.urlencode({"chat_id": target, "text": text}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token_value}/sendMessage",
        data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    if not payload.get("ok"):
        return str(payload.get("description") or "отказ")
    return ""


def caption_for(face_data: dict[str, object]) -> str:
    """Подпись под картинкой с текущим именем."""
    return CAPTION.format(name=face_data.get("name", "Ада"),
                          role=face_data.get("role", "помощница в чате"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="показывать боту картинки себя")
    parser.add_argument("--chat", help="chat_id, если он не в настройках")
    parser.add_argument("--every", type=int, default=SHOW_EVERY_S,
                        help="секунд между картинками")
    parser.add_argument("--once", action="store_true",
                        help="одна картинка и выход")
    args = parser.parse_args()

    if args.chat:
        os.environ["TELEGRAM_CHAT_ID"] = args.chat

    token_value = token()
    target = chat_id()
    caption = caption_for(face())

    shots = pictures()
    print(f"чат: {target}")
    print(f"картинок в папке: {len(shots)} ({PICTURES})")
    if shots:
        for item in shots[:5]:
            print(f"  {item.name}")
        if len(shots) > 5:
            print(f"  … ещё {len(shots) - 5}")

    # Сразу при старте: человек должен увидеть бота, а не ждать.
    print("показываю первую картинку…", flush=True)
    show_one(token_value, target, caption)

    if args.once:
        return 0

    print(f"дальше раз в {args.every} с. Остановить — Ctrl+C")
    while True:
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            print("\nостановлено")
            return 0
        show_one(token_value, target, caption)


if __name__ == "__main__":
    sys.exit(main())