#!/bin/sh
# Готовый ZIP для Windows: панель + портативный Python (python.org, embeddable).
# adb и по желанию ffmpeg start.bat скачает сам при первом запуске (platform-tools Google распространять нельзя).
#   .github/build-windows-zip.sh <версия> [amd64|arm64] [папка для результата]
set -eu
VER="${1:?версия, например 1.0.0}"
ARCH="${2:-amd64}"
OUT="${3:-dist}"
PYVER="3.14.8"   # 3.14+: в стандартной библиотеке есть zstd (анимации часов, заставка)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="yandex-station-web-${VER}-windows-$( [ "$ARCH" = amd64 ] && echo x64 || echo arm64 )"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/$NAME/runtime/python" "$OUT"
cd "$ROOT"
cp -r server.py access.py glagol.py profiles.py managed_yam.py multi.py speakers.example.json VERSION static device start.bat LICENSE README.md CONTRIBUTION.md "$WORK/$NAME/"
cp pyadb.py alarm_intent.py alarm_music.py audio_stream.py light_profiles.py music_artwork.py music_library.py music_lights.py music_metadata.py music_spectrum.py native_alarms.py playback_order.py voice_bridge.py voice_catalog.py voice_events.py yandex_library.py panel_journal.py panel_scenes.py graph_scenes.py native_scenes.py animation_library.py ring_animations.py sound_overrides.py requirements-local.txt "$WORK/$NAME/"
python -m pip download --only-binary=:all: --no-deps -r requirements-local.txt -d "$WORK/wheels"
mkdir -p "$WORK/$NAME/runtime/packages"
for wheel in "$WORK"/wheels/*.whl; do unzip -qo "$wheel" -d "$WORK/$NAME/runtime/packages"; done
curl -fsSL -o "$WORK/python.zip" "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-$ARCH.zip"
unzip -q "$WORK/python.zip" -d "$WORK/$NAME/runtime/python"
# портативный Python не видит папку скрипта — добавляем корень панели (путь относительно ._pth)
for pth in "$WORK/$NAME/runtime/python"/python3*._pth; do printf '..\\..\r\n' >> "$pth"; done
cat > "$WORK/$NAME/runtime/README.txt" <<TXT
runtime — всё, что нужно панели, без установки в систему.
python\  — Python $PYVER (embeddable) с python.org, лицензия PSF: python\LICENSE.txt
platform-tools\, ffmpeg\ — start.bat скачает при первом запуске, если их нет в системе.
TXT
find "$WORK/$NAME" -name '__pycache__' -type d -prune -exec rm -rf {} +
(cd "$WORK" && zip -qr9 "$NAME.zip" "$NAME")
mv "$WORK/$NAME.zip" "$OUT/"
echo "$OUT/$NAME.zip"
