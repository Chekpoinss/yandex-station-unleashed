# Веб-панель для Яндекс Станций: Python (только стандартная библиотека) + adb + ffmpeg + openssl.
FROM python:3.14-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends adb ffmpeg openssl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py access.py glagol.py profiles.py managed_yam.py multi.py speakers.example.json VERSION ./
COPY pyadb.py alarm_intent.py alarm_music.py audio_stream.py light_profiles.py music_artwork.py music_library.py music_lights.py music_metadata.py music_spectrum.py native_alarms.py playback_order.py voice_bridge.py voice_catalog.py voice_events.py yandex_library.py panel_journal.py panel_scenes.py graph_scenes.py native_scenes.py animation_library.py ring_animations.py sound_overrides.py requirements-local.txt ./
RUN pip install --no-cache-dir -r requirements-local.txt
COPY static ./static
COPY device ./device
COPY LICENSE ./

# /data — токен Яндекса, пароль панели, сертификаты, история графиков и ключ adb (HOME).
# Свой порт adb-сервера, чтобы при network_mode: host не мешать adb на самой машине.
ENV PANEL_HOST=0.0.0.0 \
    PANEL_INSTALL=docker \
    PANEL_DATA_DIR=/data \
    HOME=/data \
    ANDROID_ADB_SERVER_PORT=5038 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765 8766 8767

CMD ["python", "server.py"]
