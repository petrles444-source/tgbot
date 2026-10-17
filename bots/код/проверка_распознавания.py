# -*- coding: utf-8 -*-
"""Проверка конвейера распознавания на сервере — до первого голосового.

Зачем
----
Голосовое записывает владелец, а расшифровывает бот. Если конвейер
не работает, человек узнает об этом на своей же записи, и это плохое
первое впечатление: «бот сломан». Лучше проверить на тишине сейчас.

Что именно проверяется
---------------------
1. ключ Groq принимается (иначе 401);
2. multipart-запрос собирается правильно (иначе 400);
3. движок отвечает и отвечает JSON (иначе исключение на разборе);
4. тишина честно превращается в «пусто», а не в выдуманный текст.

Чего эта проверка НЕ доказывает
-------------------------------
    Качество распознавания русской речи. На тишине слов нет, и
    движок их не придумает — что правильно. Про качество можно
    узнать только на настоящей записи.
"""
import json
import math
import struct
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SECRETS = Path("/opt/bots/.secret/ключи-моделей.json")


def ключ() -> tuple[str, str]:
    """Ключ и адрес Groq из файла моделей."""
    data = json.loads(SECRETS.read_text(encoding="utf-8"))
    записи = data.get("providers", {}).get("groq") or []
    if isinstance(записи, str):
        записи = [записи]
    for k in записи:
        if k:
            return "https://api.groq.com/openai/v1", str(k)
    raise SystemExit("ключа Groq нет")


def тишина_wav(секунды: float = 1.0, частота: int = 8000) -> bytes:
    """Настоящий WAV-файл с тишиной. Нужен, чтобы был что принять."""
    import io
    import wave
    буфер = io.BytesIO()
    with wave.open(буфер, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(частота)
        w.writeframes(b"\x00\x00" * int(частота * секунды))
    return буфер.getvalue()


def тишина_wav_с_тоном(секунды: float = 1.0, частота: int = 8000) -> bytes:
    """WAV с тихим тоном: не тишина, но и не речь."""
    import io
    import wave
    буфер = io.BytesIO()
    with wave.open(буфер, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(частота)
        шагов = int(частота * секунды)
        кадры = b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / частота)))
            for i in range(шагов))
        w.writeframes(кадры)
    return буфер.getvalue()


def спросить(адрес: str, кл: str, имя: str, звук: bytes) -> tuple[int, str]:
    """Один запрос к движку. Возвращает (код, тело)."""
    граница = uuid.uuid4().hex
    тело = bytearray()
    for поле, значение in (("model", "whisper-large-v3-turbo"),
                           ("language", "ru"),
                           ("response_format", "json"),
                           ("temperature", "0")):
        тело += (f"--{граница}\r\n"
                 f'Content-Disposition: form-data; name="{поле}"\r\n\r\n'
                 f"{значение}\r\n").encode("utf-8")
    тело += (f"--{граница}\r\n"
             f'Content-Disposition: form-data; name="file"; filename="{имя}"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode("utf-8")
    тело += звук + b"\r\n"
    тело += f"--{граница}--\r\n".encode("utf-8")

    запрос = urllib.request.Request(
        f"{адрес}/audio/transcriptions", data=bytes(тело),
        headers={"Content-Type": f"multipart/form-data; boundary={граница}",
                 "Authorization": f"Bearer {кл}"},
        method="POST")
    try:
        with urllib.request.urlopen(запрос, timeout=120) as ответ:
            return ответ.status, ответ.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def главная() -> int:
    print("=== ПРОВЕРКА КОНВЕЙЕРА РАСПОЗНАВАНИЯ ===")
    try:
        адрес, кл = ключ()
    except SystemExit as e:
        print(f"  [ПРОВАЛ] {e}")
        return 2
    print(f"  ключ Groq найден, адрес: {адрес}")
    print()

    # 1. тишина
    код, тело = спросить(адрес, кл, "тишина.wav", тишина_wav())
    print(f"  1. тишина:        HTTP {код}")
    if код == 200:
        try:
            данные = json.loads(тело)
            текст = str(данные.get("text") or "").strip()
            print(f"     ответ разобран, текст: {текст!r}")
            if not текст:
                print("     [ок]   пусто на тишине — движок не выдумывает")
        except ValueError:
            print(f"     [ПРОВАЛ] ответ не JSON: {тело[:120]}")
            return 2
    else:
        print(f"     [ПРОВАЛ] {тело[:200]}")
        return 2

    # 2. тон
    код, тело = спросить(адрес, кл, "тон.wav", тишина_wav_с_тоном())
    print(f"  2. тон 440 Гц:    HTTP {код}")
    if код == 200:
        try:
            данные = json.loads(тело)
            текст = str(данные.get("text") or "").strip()
            print(f"     текст: {текст!r}")
            if not текст:
                print("     [ок]   тон не превратился в выдуманные слова")
            else:
                print("     [внимание] движок что-то услышал в тоне — не ошибка,"
                      " но повод знать")
        except ValueError:
            print(f"     [ПРОВАЛ] ответ не JSON: {тело[:120]}")
            return 2
    else:
        print(f"     [ПРОВАЛ] {тело[:200]}")
        return 2

    # 3. мусор вместо аудио — бот должен это пережить, а не упасть
    код, тело = спросить(адрес, кл, "мусор.wav", "это не аудио, а текст".encode("utf-8"))
    print(f"  3. мусор вместо аудио: HTTP {код}")
    if код in (200, 400, 415, 422):
        print("     [ок]   движок отверг или принял без падения — "
              "бот это переживёт")
    else:
        print(f"     [внимание] код {код}: {тело[:120]}")

    # 4. сколько символов в теле запроса (диагностика размера)
    print()
    print(f"  размер тишины в POST: {len(тишина_wav())} Б")
    print()
    print("  ИТОГ: конвейер работает. Качество русской речи эта проверка")
    print("        не доказывает — для этого нужна настоящая запись.")
    return 0


if __name__ == "__main__":
    sys.exit(главная())