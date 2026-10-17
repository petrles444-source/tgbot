#!/usr/bin/env bash
# Кладёт токен Telegram в правильное место и с правильными правами.
#
# Зачем отдельный скрипт
# ----------------------
# Токен — это пароль от бота. Положить его «как попало» значит либо
# отдать его всем, кто умеет читать файлы на сервере, либо не дать
# никому, включая сам сервис. Ошибка в одном символе обнаружилась бы
# позже, в журнале службы, и выглядела бы как «бот молчит» без причины.
#
# ПОЧЕМУ ИМЕНА ЛАТИНИЦЕЙ, А КОММЕНТАРИИ РУССКИМИ
# ---------------------------------------------
# Первая версия называла переменные по-русски: ПАПКА, показать,
# проверить_токен. Bash такие имена не признаёт — `ПАПКА=/etc/sora/bots`
# он выполнил как команду и ответил «No such file or directory».
# Python кириллицу в именах держит, bash — нет. Поэтому здесь латиница,
# а по-русски остаётся всё, что читает человек.

set -euo pipefail

DIR=/etc/sora/bots

show_help() {
    echo "Куда класть токены:"
    echo "  папка: $DIR   (права 700)"
    echo "  имена: ada.token, anatoly.token"
    echo
    echo "Как положить — одна команда, токен подставляете вы:"
    echo
    echo "  ./бот-токен ada 1234567890:AA..."
    echo "  ./бот-токен anatoly 1234567890:BB..."
    echo
    echo "Что уже положено:"
    if [ -d "$DIR" ]; then
        ls -la "$DIR" | sed 's/^/  /'
    else
        echo "  папки пока нет"
    fi
}

check_name() {
    case "$1" in
        ada) ;;
        anatoly) ;;
        *) echo "имя должно быть ada или anatoly" >&2; exit 1 ;;
    esac
}

check_token() {
    # Токен Telegram: 8–12 цифр, двоеточие, затем около 35 символов.
    # Длина проверяется нестрого: пропущенный символ означает отказ
    # Telegram, а лишний обрезать — не наша забота.
    if ! printf '%s' "$1" | grep -qE '^[0-9]{8,12}:[A-Za-z0-9_-]{30,40}$'; then
        echo "токен не похож на настоящий" >&2
        echo "ожидается: цифры, двоеточие, потом около 35 символов" >&2
        echo "ничего не записано" >&2
        exit 1
    fi
}

save_token() {
    local name=$1 token=$2
    mkdir -p "$DIR"
    chmod 700 "$DIR"
    umask 077
    printf '%s' "$token" > "$DIR/$name.token"
    chmod 600 "$DIR/$name.token"

    local fp id
    fp=$(printf '%s' "$token" | sha256sum | cut -c1-16)
    id=${token%%:*}
    echo "записано: $DIR/$name.token"
    echo "  id бота:   $id"
    echo "  отпечаток: sha256:$fp"
    echo "  права:     $(stat -c '%a' "$DIR/$name.token")"
}

main() {
    if [ $# -eq 0 ]; then
        show_help
        return 0
    fi
    check_name "$1"
    if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        show_help
        exit 1
    fi
    check_token "$2"
    save_token "$1" "$2"
}

main "$@"