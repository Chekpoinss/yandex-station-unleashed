import unittest
from unittest.mock import Mock, patch

from native_alarms import NativeAlarms, decode, fields


def vi(n):
    result = bytearray()
    while n > 127:
        result.append((n & 127) | 128)
        n >>= 7
    return bytes(result) + bytes([n])


def field(n, v):
    if isinstance(v, int):
        return vi(n << 3) + vi(v)
    if isinstance(v, str):
        v = v.encode()
    return vi(n << 3 | 2) + vi(len(v)) + v


def alarm(ident='test-alarm', hour=12, minute=34):
    calendar = field(1, 2026) + field(2, 10) + field(3, 29)
    clock = field(1, hour) + field(2, minute)
    record = (field(1, ident) + field(2, clock) + field(5, field(2, calendar))
              + field(7, field(1, 'Europe/Moscow') + field(2, 10800)))
    return field(3, record)


class NativeAlarmTests(unittest.TestCase):
    def test_arbitrary_day_subset_and_disabled_flag(self):
        for days,label in (([1,3,7],'Пн, Ср, Вс'),(list(range(1,8)),'Каждый день')):
            record=(field(1,'subset')+field(2,b'')
                    +b''.join(field(5,field(1,day)) for day in days)
                    +field(7,field(1,'Europe/Moscow'))+field(9,1))
            item=decode(field(3,record))['items'][0]
            self.assertEqual(item['schedule_label'],label)
            self.assertFalse(item['enabled'])

    def test_observed_weekdays_schedule_preserves_repeated_entries(self):
        record = (field(1, 'weekly-test') + field(2, field(1, 12) + field(2, 34))
                  + b''.join(field(5, field(1, day)) for day in range(1, 6))
                  + field(7, field(1, 'Europe/Moscow') + field(2, 10800)))
        result = decode(field(3, record))
        self.assertEqual(result['unrecognized'], 0)
        item = result['items'][0]
        self.assertEqual(item['schedule_label'], 'По будням')
        self.assertEqual(item['days'], [1, 2, 3, 4, 5])
        self.assertNotIn('date', item)
        self.assertTrue(item['read_only'])

    def test_duplicate_out_of_range_or_mixed_recurring_schedule_is_unrecognized(self):
        for schedules in ([1, 1, 2, 3, 4, 5], [0, 1, 2, 3, 4], [1, 2, 3, 4, 8]):
            record = (field(1, 'unknown') + field(2, b'')
                      + b''.join(field(5, field(1, day)) for day in schedules)
                      + field(7, field(1, 'Europe/Moscow')))
            self.assertEqual(decode(field(3, record))['unrecognized'], 1)
        record = (field(1, 'mixed') + field(2, b'') + field(5, field(1, 1))
                  + field(5, field(2, field(1, 2026) + field(2, 10) + field(3, 29)))
                  + field(7, field(1, 'Europe/Moscow')))
        self.assertEqual(decode(field(3, record))['unrecognized'], 1)

    def test_observed_weekends_schedule(self):
        record = (field(1, 'weekend-test') + field(2, field(1, 12) + field(2, 34))
                  + field(5, field(1, 6)) + field(5, field(1, 7))
                  + field(7, field(1, 'Europe/Moscow')))
        result = decode(field(3, record))
        self.assertEqual(result['unrecognized'], 0)
        self.assertEqual(result['items'][0]['schedule_label'], 'По выходным')
        self.assertEqual(result['items'][0]['days'], [6, 7])

    def test_dated_alarm_preserves_time_date_and_origin(self):
        result = decode(alarm())
        self.assertEqual(result['unrecognized'], 0)
        self.assertEqual(result['items'], [{'id': 'test-alarm', 'time': '12:34',
            'date': '2026-10-29', 'timezone': 'Europe/Moscow', 'source': 'alice',
            'read_only': True, 'schedule': 'dated'}])

    def test_multiple_alarms_and_unknown_records_do_not_hide_known_items(self):
        result = decode(alarm('one') + field(3, b'') + alarm('two'))
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['unrecognized'], 1)

    def test_empty_file_is_distinct_from_parse_failure(self):
        self.assertEqual(decode(b'')['items'], [])
        self.assertIsNone(decode(b'')['error'])
        with self.assertRaises(ValueError):
            decode(b'\x1a\x05x')

    def test_invalid_time_is_not_a_valid_alarm(self):
        result = decode(alarm(hour=25))
        self.assertEqual(result['items'], [])
        self.assertEqual(result['unrecognized'], 1)

    def test_unknown_top_level_schedule_is_visible_as_unrecognized(self):
        self.assertEqual(decode(field(4, b'something'))['unrecognized'], 1)

    def test_default_zero_time_fields_allow_midnight(self):
        blob = alarm(hour=0, minute=0)
        # Protocol Buffer scalar zero values may be absent from the wire.
        blob = field(3, next(iter(fields(blob)[3])).replace(field(2, field(1, 0) + field(2, 0)), field(2, b'')))
        self.assertEqual(decode(blob)['items'][0]['time'], '00:00')

    def test_profile_adds_native_snapshot_without_merging_local_schedule(self):
        from test_profiles import fresh_server
        import profiles
        s = fresh_server()
        original = {'alarms': [{'id': 'local-only'}], 'installed': True}
        s.Panel.alarms = Mock(return_value=original)
        profiles.install(s, 'lite1')
        panel = s.Panel(Mock())
        panel.adb.shell.return_value = ('82', '', 0)
        panel.adb.read_bytes.return_value = alarm()
        result = panel.alarms()
        self.assertEqual(result['alarms'], [{'id': 'local-only'}])
        self.assertEqual(result['native']['items'][0]['source'], 'alice')
        self.assertNotIn('native', original)
        panel.adb.push.assert_not_called()

    def test_malformed_varints_and_lengths_are_bounded(self):
        for blob in (b'\x80' * 12, b'\x00', b'\x1a\xff\xff\xff\xff\x7f', b'\x0e'):
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                fields(blob)

    def test_reader_uses_only_read_commands_and_caches(self):
        adb = Mock()
        adb.shell.return_value = ('82\n', '', 0)
        adb.read_bytes.return_value = alarm()
        reader = NativeAlarms(adb)
        first, second = reader.read(), reader.read()
        self.assertIs(first, second)
        self.assertEqual(len(first['items']), 1)
        adb.shell.assert_called_once_with('wc -c < /data/quasar/alarms.dat', timeout=5, check=False)
        adb.read_bytes.assert_called_once_with('/data/quasar/alarms.dat', timeout=10)
        adb.push.assert_not_called()

    def test_read_failure_keeps_stale_snapshot_instead_of_empty_list(self):
        adb = Mock()
        adb.shell.return_value = ('82', '', 0)
        adb.read_bytes.return_value = alarm()
        reader = NativeAlarms(adb)
        with patch('native_alarms.time.monotonic', return_value=10):
            reader.read()
        adb.read_bytes.side_effect = OSError('offline')
        with patch('native_alarms.time.monotonic', return_value=20):
            snapshot = reader.read()
        self.assertTrue(snapshot['stale'])
        self.assertEqual(len(snapshot['items']), 1)
        self.assertTrue(snapshot['error'])


if __name__ == '__main__':
    unittest.main()
