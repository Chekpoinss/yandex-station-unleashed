"""Bounded panel activity journal. Only fixed action identifiers and HTTP status enter storage.

Never accept request bodies, query strings, exception text, speech, URLs or file names.
"""
import collections
import json
import os
from pathlib import Path
import threading
import time

# Identifiers are deliberately enumerated: unknown paths cannot smuggle data into a log.
ACTIONS = {
    '/api/scenes/deploy': 'Развёртывание сценариев',
    '/api/ring/try': 'Проверка анимации логотипа', '/api/anim/my': 'Сохранение анимации',
    '/api/anim/my/delete': 'Удаление анимации', '/api/anim/install': 'Подмена анимации', '/api/anim/reset': 'Восстановление анимации',
    '/api/sounds/play-original': 'Прослушивание оригинального звука',
    '/api/assistant': 'Настройки ассистента', '/api/assistant/install': 'Установка ассистента',
    '/api/assistant/start': 'Запуск ассистента', '/api/assistant/stop': 'Остановка ассистента',
    '/api/assistant/ask': 'Вопрос ассистенту', '/api/assistant/remove': 'Удаление ассистента',
    '/api/radio': 'Настройки радио', '/api/radio/alice': 'Запуск радио через Алису', '/api/radio/stop': 'Остановка радио',
    '/api/scenes': 'Редактирование сценариев', '/api/scenes/run': 'Запуск или завершение сценария',
    '/api/scenes/stop': 'Отмена сценария', '/api/library/animations': 'Редактор анимаций',
    '/api/yam2/install': 'Установка YAM 2', '/api/yam2/update': 'Обновление YAM 2',
    '/api/yam2/pkg': 'Пакет YAM 2', '/api/yam2/doctor': 'Диагностика или ремонт YAM 2',
    '/api/yam2/service': 'Служба YAM 2', '/api/yam2/registry': 'Каталог YAM 2',

    '/api/volume': 'Громкость', '/api/play': 'Встроенный звук', '/api/stop': 'Остановка звука',
    '/api/music/cmd': 'Команда плееру или Алисе', '/api/music/play': 'Выбор музыки',
    '/api/library/control': 'Общий плеер', '/api/library/import': 'Импорт музыки',
    '/api/library/remove': 'Удаление трека', '/api/library/restore': 'Возврат трека',
    '/api/library/edit': 'Метаданные', '/api/library/playlist': 'Плейлист',
    '/api/library/lights': 'Настройки светомузыки', '/api/library/voice': 'Голосовое управление',
    '/api/library/soundcloud': 'Поиск SoundCloud', '/api/library/yandex/favorite': 'Избранное Яндекса',
    '/api/alarms': 'Расписание будильников', '/api/alarms/cmd': 'Управление будильником',
    '/api/alarms/install': 'Установка будильника', '/api/alarms/test': 'Тест будильника',
    '/api/library/alarm-sound': 'Подготовка музыкального будильника',
    '/api/ring': 'Цвет логотипа', '/api/ring/reset': 'Сброс подсветки',
    '/api/pattern': 'Анимация', '/api/pattern/stop': 'Остановка анимации',
    '/api/say': 'Сказать на колонке', '/api/sounds/replace': 'Замена звука', '/api/sounds/reset': 'Восстановление звука',
    '/api/system': 'Системное действие', '/api/bt': 'Bluetooth',
    '/api/fs/write': 'Запись файла', '/api/fs/upload': 'Загрузка файла',
    '/api/fs/delete': 'Удаление файла', '/api/fs/rename': 'Переименование файла',
    '/api/yam/install': 'Установка пакета', '/api/yam/action': 'Управление пакетом',
    '/api/panel/version': 'Ручная проверка обновления',
}

class Journal:
    LIMIT = 1000

    def __init__(self):
        self.lock = threading.RLock()
        self.path = None
        self.items = collections.deque(maxlen=self.LIMIT)
        self.error = False

    def open(self, path):
        with self.lock:
            self.path = Path(path)
            self.items.clear()
            try:
                # Bound reads even if a previous version wrote an unexpectedly large file.
                if self.path.stat().st_size <= 512 * 1024:
                    for row in self.path.read_text(encoding='utf8').splitlines()[-self.LIMIT:]:
                        try:
                            d = json.loads(row)
                            if (d.get('action') in ACTIONS and type(d.get('status')) is int
                                    and 100 <= d['status'] <= 599 and type(d.get('t')) in (int, float)):
                                self.items.append({'t': d['t'], 'action': d['action'], 'status': d['status']})
                        except (ValueError, TypeError, AttributeError):
                            pass
            except OSError:
                pass

    def record(self, path, status):
        if path not in ACTIONS or type(status) is not int or not 100 <= status <= 599:
            return
        with self.lock:
            self.items.append({'t': round(time.time(), 3), 'action': path, 'status': status})
            if self.path:
                tmp = self.path.with_suffix('.tmp')
                try:
                    tmp.write_text(''.join(json.dumps(i, ensure_ascii=False) + '\n' for i in self.items), encoding='utf8')
                    os.replace(tmp, self.path)
                    self.error = False
                except OSError:
                    self.error = True

    def text(self, lines=300):
        with self.lock:
            items = list(self.items)[-max(1, min(self.LIMIT, int(lines))):]
            warning = 'Журнал временно не сохраняется на диск.\n' if self.error else ''
        return warning + '\n'.join(
            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(i['t'])) + ' · ' + ACTIONS[i['action']]
            + ' · HTTP ' + str(i['status']) for i in items)
