"""Read-only decoder for Micro's observed alarms.dat records.

This is an observed protobuf layout, not a general Quasar write contract.
Unrecognized schedules remain explicitly unrecognized; never mirror or write
these records into the panel's separate scheduler.
"""
from datetime import date
import threading
import time

MAX_BYTES = 256 * 1024


def fields(blob):
    if len(blob) > MAX_BYTES:
        raise ValueError('Alarm data exceeds limit')
    pos = 0

    def varint():
        nonlocal pos
        result = 0
        for shift in range(0, 70, 7):
            if pos >= len(blob):
                raise ValueError('Truncated varint')
            value = blob[pos]
            pos += 1
            if shift == 63 and value > 1:
                raise ValueError('Oversized varint')
            result |= (value & 127) << shift
            if value < 128:
                return result
        raise ValueError('Invalid varint')

    result = {}
    while pos < len(blob):
        key = varint()
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError('Invalid field number')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if length > len(blob) - pos:
                raise ValueError('Truncated field')
            value = blob[pos:pos + length]
            pos += length
        else:
            raise ValueError('Unsupported wire type')
        result.setdefault(number, []).append(value)
    return result


def one(data, number, kind):
    values = data.get(number, [])
    if len(values) != 1 or not isinstance(values[0], kind):
        raise ValueError('Unrecognized alarm layout')
    return values[0]


def decode(blob):
    top = fields(blob)
    items, unknown = [], 0
    # Field 3 is a record; repeated field 5 is its schedule union.
    unknown += sum(len(v) for k, v in top.items() if k != 3)
    for raw in top.get(3, []):
        try:
            if not isinstance(raw, bytes):
                raise ValueError('Invalid record')
            record = fields(raw)
            ident = one(record, 1, bytes).decode('utf8')
            if not ident or len(ident) > 128:
                raise ValueError('Invalid id')
            clock = fields(one(record, 2, bytes))
            hour = one(clock, 1, int) if 1 in clock else 0
            minute = one(clock, 2, int) if 2 in clock else 0
            if not 0 <= hour < 24 or not 0 <= minute < 60:
                raise ValueError('Invalid time')
            schedules = [fields(value) for value in record.get(5, []) if isinstance(value, bytes)]
            if len(schedules) != len(record.get(5, [])) or not schedules:
                raise ValueError('Unrecognized schedule')
            if len(schedules) == 1 and set(schedules[0]) == {2}:
                calendar = fields(one(schedules[0], 2, bytes))
                day = date(*(one(calendar, n, int) for n in (1, 2, 3)))
                timing = {'date': day.isoformat(), 'schedule': 'dated'}
            elif all(set(schedule) == {1} for schedule in schedules):
                days = [one(schedule, 1, int) for schedule in schedules]
                # Individual enum values were observed in weekday/weekend
                # groups and independently match the published TAlarm schema.
                names = {(1, 2, 3, 4, 5): 'По будням', (6, 7): 'По выходным',
                         (1, 2, 3, 4, 5, 6, 7):'Каждый день'}
                key = tuple(sorted(days))
                if len(set(days))!=len(days) or any(not 1<=day<=7 for day in days):
                    raise ValueError('Unrecognized recurring schedule')
                label=names.get(key,', '.join(('Пн','Вт','Ср','Чт','Пт','Сб','Вс')[day-1] for day in key))
                timing = {'schedule': 'weekly', 'days': list(key), 'schedule_label': label}
            else:
                raise ValueError('Unrecognized schedule')
            zone = fields(one(record, 7, bytes))
            tz = one(zone, 1, bytes).decode('utf8')
            if not tz or len(tz) > 100:
                raise ValueError('Invalid timezone')
            item={'id': ident, 'source': 'alice', 'read_only': True,
                  'time': f'{hour:02}:{minute:02}', 'timezone': tz, **timing}
            if 9 in record:
                disabled=one(record,9,int)
                if disabled not in (0,1):raise ValueError('Invalid disabled flag')
                item['enabled']=not disabled
            items.append(item)
        except (ValueError, UnicodeError, TypeError, OverflowError):
            unknown += 1
    return {'items': items, 'unrecognized': unknown, 'stale': False,
            'error': None, 'supported': True}


class NativeAlarms:
    def __init__(self, adb):
        self.adb = adb
        self.lock = threading.Lock()
        self.at = None
        self.cached = {'items': [], 'unrecognized': 0, 'stale': True,
                       'supported': True, 'error': 'Штатный список ещё не прочитан'}

    def read(self):
        with self.lock:
            if self.at is not None and time.monotonic() - self.at < 5:
                return self.cached
            try:
                size, _, code = self.adb.shell('wc -c < /data/quasar/alarms.dat', timeout=5, check=False)
                if code or not size.strip().isdigit() or int(size.strip()) > MAX_BYTES:
                    raise ValueError('Unavailable or oversized alarm data')
                blob = self.adb.read_bytes('/data/quasar/alarms.dat', timeout=10)
                self.cached = decode(blob)
            except Exception:
                # A read/parse failure is not an empty alarm list. Keep the last
                # known snapshot, marked stale, instead of hiding existing items.
                self.cached = {**self.cached, 'stale': True,
                               'error': 'Не удалось обновить будильники Алисы'}
            self.at = time.monotonic()
            return self.cached
