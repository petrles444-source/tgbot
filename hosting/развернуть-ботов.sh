#!/usr/bin/env bash
# Ставит бота на сервер: код, настройки, токен и службу systemd.
#
# ЧТО ДЕЛАЕТ
# ----------
#   1. копирует код бота в /opt/bots/<имя>
#   2. кладёт ключи моделей в /opt/bots/.secret/ключи-моделей.json
#   3. пишет config.json для этого бота
#   4. создаёт службу systemd
#
# ГДЕ ЖИВЁТ ТОКЕН
# ----------------
# Один-единственный файл: /etc/sora/bots/<имя>.token, права 600.
# Служба НЕ получает токен через EnvironmentFile: это заставило бы
# держать вторую копию секрета, и две копии рано или поздно разойдутся
# — одна обновится при перевыпуске, другая нет, и бот молча перестал бы
# работать. Поэтому запускает его обёртка, которая читает файл и
# подставляет токен в переменную окружения в момент старта.
#
# ПОЧЕМУ ИМЕНА ЛАТИНИЦЕЙ
# ----------------------
# Bash не признаёт кириллицу в именах переменных и функций: `ПАПКА=/x`
# он выполняет как команду. Русскими остаются комментарии и вывод.

set -euo pipefail

BOT=${1:-}
SECRETS=/etc/sora/bots
ROOT=/opt/bots
SOURCE=${2:-/tmp/бот-код}

usage() {
    echo "Как пользоваться:"
    echo "  ./развернуть-ботов.sh ada        код из /tmp/бот-код"
    echo "  ./развернуть-ботов.sh anatoly    код из /tmp/бот-код"
    echo "  ./развернуть-ботов.sh effy       код из /tmp/бот-код"
    echo
    echo "Требуется: ключи моделей в /etc/sora/bots/модели.json"
    echo "           токен бота в /etc/sora/bots/<имя>.token"
}

if [ -z "$BOT" ]; then
    usage
    exit 1
fi

case "$BOT" in
    ada|anatoly|effy) ;;
    *) echo "имя должно быть ada, anatoly или effy" >&2; usage; exit 1 ;;
esac

TOKEN_FILE=$SECRETS/$BOT.token
MODELS=$SECRETS/модели.json

[ -f "$TOKEN_FILE" ] || { echo "нет токена: $TOKEN_FILE" >&2; exit 1; }
[ -f "$MODELS" ]     || { echo "нет ключей моделей: $MODELS" >&2; exit 1; }
[ -d "$SOURCE" ]     || { echo "нет кода: $SOURCE" >&2; exit 1; }

PERSONA_KEY=$BOT
case "$BOT" in
    ada)     PERSONA_KEY=ada ;;
    anatoly) PERSONA_KEY=anatoly ;;
esac

echo "=== 1. КОД ==="
mkdir -p "$ROOT/$BOT"
cp "$SOURCE"/*.py "$ROOT/$BOT/"
# Личный характер бота и его память — свои, иначе Ада и Анатолий
# делили бы один файл памяти и путались бы в разговоре.
mkdir -p "$ROOT/$BOT/знания" "$ROOT/$BOT/память"
cp -n "$SOURCE"/../знания/* "$ROOT/$BOT/знания/" 2>/dev/null || true
echo "  $ROOT/$BOT — $(ls "$ROOT/$BOT"/*.py | wc -l) файлов .py"

echo "=== 2. КЛЮЧИ МОДЕЛЕЙ (общие на всех) ==="
mkdir -p "$ROOT/.secret"
install -m 600 "$MODELS" "$ROOT/.secret/ключи-моделей.json"
echo "  $ROOT/.secret/ключи-моделей.json  права 600"

echo "=== 3. НАСТРОЙКИ ==="
python3 - "$ROOT/$BOT/config.json" "$PERSONA_KEY" "$SOURCE/config.json" <<'PY'
import json, sys, os
out, char_key, sample = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.dirname(sample))
import persona
cfg = {
    "token": "",
    "active": 0,
    "system": "Ты — помощник. Отвечай коротко и по делу, по-русски.",
    "providers": [
        {"name": "groq", "base_url": "https://api.groq.com/openai/v1",
         "model": "llama-3.3-70b-versatile", "api_key": ""},
        {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
         "model": "deepseek/deepseek-chat-v3-0324:free", "api_key": ""},
        {"name": "mistralai", "base_url": "https://api.mistral.ai/v1",
         "model": "mistral-small-latest", "api_key": ""},
    ],
    # persona_key — имя и лицо бота. Без него бот выбирает случайное
    # лицо при каждом запуске: @adaweffeBot «Ада» представлялся то как
    # Лиза, то как Мия, и человек здоровался с незнакомым именем.
    # Раньше здесь стоял один только char_key — он задаёт правила речи,
    # но никак не влияет на то, как бот себя называет.
    "persona_key": char_key,
    # char_key — какие правила речи применять: своя подробность,
    # строгость и отношение к шуткам у каждого бота свои.
    "char_key": char_key,
    "persona": "persona",
    "heartbeat_chat_id": "",
    "face": persona.persona_by_key(char_key) or persona.PERSONAS[0],
    "detail_level": f"{persona.DETAIL_LEVEL} из 10",
    "science_level": f"{persona.SCIENCE_LEVEL} из 10",
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print(f"  {out}  характер: {char_key}")
PY
chmod 600 "$ROOT/$BOT/config.json"

echo "=== 4. СЛУЖБА ==="
cat > "/etc/systemd/system/bot-$BOT.service" <<UNIT
[Unit]
Description=Бот $BOT (Telegram, развлекательный)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$ROOT/$BOT
ExecStart=/usr/local/bin/бот-запуск $BOT
Restart=always
RestartSec=15
User=root
WorkingDirectory=$ROOT/$BOT
StandardOutput=append:/var/log/bots/$BOT.log
StandardError=append:/var/log/bots/$BOT.err

# Бот не должен съесть сервер. Если уйдёт в цикл и станет есть
# больше лимита, ядро убьёт процесс, а служба его поднимет снова —
# и это лучше, чем положить рядом VPN и прокси.
MemoryMax=120M
MemoryHigh=80M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=yes
ReadWritePaths=$ROOT/$BOT /var/log/bots

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable "bot-$BOT" >/dev/null 2>&1
echo "  bot-$BOT — включена при загрузке"
echo "  запустить: systemctl start bot-$BOT"
echo "  журнал:   /var/log/bots/$BOT.log"
echo
echo "=== ГОТОВО ==="