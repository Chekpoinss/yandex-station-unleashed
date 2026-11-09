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
if [ -n "$1" ] && [ "${1#-}" = "$1" ]; then
    dev="$1"; shift
    exec python3 server.py --open --device "$dev" "$@"
fi
exec python3 server.py --open "$@"
