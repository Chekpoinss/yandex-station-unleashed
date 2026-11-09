#!/bin/sh
# Запуск панели на Linux / macOS: ./start.sh [IP колонки]  или  ./start.sh --find  (найти колонку в сети)
cd "$(dirname "$0")" || exit 1

# Finder/Terminal могут запускать .command без Homebrew в PATH.
# Используем установку для текущей архитектуры, сохраняя уже настроенный PATH.
platform=$(uname -s)
if [ "$platform" = Darwin ]; then
    case "$(uname -m)" in
        arm64) brew_prefix=/opt/homebrew ;;
        *) brew_prefix=/usr/local ;;
    esac
    if [ -d "$brew_prefix/bin" ]; then
        case ":$PATH:" in
            *":$brew_prefix/bin:"*) ;;
            *) PATH="$brew_prefix/bin:$PATH" ;;
        esac
    fi
    export PATH
fi

if ! command -v python3 >/dev/null 2>&1 ||
   ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1; then
    echo "Нужен Python 3.9 или новее: https://www.python.org/downloads/"
    if [ "$platform" = Darwin ]; then
        echo "С Homebrew: brew install python"
    fi
    exit 1
fi
# Изолированные зависимости: запуск работает и без активированного venv.
if [ ! -x .venv/bin/python ]; then
    echo "Создаю Python-окружение .venv…"
    python3 -m venv .venv || {
        echo "Не удалось создать .venv. Установите поддержку venv для Python."
        echo "Debian/Ubuntu: sudo apt install python3-venv"
        echo "Fedora: sudo dnf install python3-pip"
        exit 1
    }
fi
if ! cmp -s requirements-local.txt .venv/panel-requirements.txt ||
   ! .venv/bin/python -c 'import mutagen, yt_dlp, tzdata' >/dev/null 2>&1; then
    echo "Устанавливаю зависимости панели…"
    .venv/bin/python -m pip install -r requirements-local.txt || exit 1
    cp requirements-local.txt .venv/panel-requirements.txt || exit 1
fi

if [ -n "$1" ] && [ "${1#-}" = "$1" ]; then
    dev="$1"; shift
    exec .venv/bin/python server.py --open --device "$dev" "$@"
fi
exec .venv/bin/python server.py --open "$@"
