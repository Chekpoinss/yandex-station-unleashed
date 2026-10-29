from datetime import datetime
from zoneinfo import ZoneInfo
import unittest
from alarm_intent import parse
from alarm_music import target_time,native_overlap


class MusicAlarmIntentTests(unittest.TestCase):
    def test_ordinary_alarms_timers_and_negation_belong_to_alice(self):
        for text in ('поставь будильник на семь','поставь таймер на две минуты',
                     'не ставь будильник на семь с этой песней'):
            self.assertIsNone(parse(text))

    def test_explicit_relative_minutes_in_words_and_digits(self):
        for value,count in (('две минуты',2),('21 минуту',21),('минуту',1)):
            self.assertEqual(parse('поставь будильник через '+value+' с этой песней'),
                             {'action':'set_alarm','delay_minutes':count})

    def test_absolute_words_digits_and_period(self):
        for text,clock in (('семь тридцать','07:30'),('7 30','07:30'),
                           ('двадцать один сорок пять','21:45'),('семь вечера','19:00'),('двенадцать ночи','00:00')):
            self.assertEqual(parse('поставь будильник на '+text+' с этой песней')['time'],clock)
        self.assertIsNone(parse('поставь будильник на двадцать четыре с этой песней'))

    def test_next_occurrence_and_relative_rounding_never_schedule_in_past(self):
        now=datetime(2026,10,28,21,30,25,tzinfo=ZoneInfo('Europe/Moscow'))
        snapshot={'device_time':{'epoch':now.timestamp()},'tz':'Europe/Moscow'}
        self.assertEqual(target_time(snapshot,{'time':'07:30'}).day,29)
        relative=target_time(snapshot,{'delay_minutes':2})
        self.assertEqual(relative.strftime('%H:%M'),'21:33')
        self.assertGreaterEqual((relative-now).total_seconds(),120)

    def test_native_collision_uses_date_and_weekday(self):
        target=datetime(2026,10,29,7,30,tzinfo=ZoneInfo('Europe/Moscow'))
        record={'time':'07:30','timezone':'Europe/Moscow','schedule':'dated','date':'2026-10-29'}
        self.assertTrue(native_overlap([record],target))
        self.assertFalse(native_overlap([{**record,'date':'2026-10-30'}],target))
        self.assertFalse(native_overlap([{**record,'enabled':False}],target))
        self.assertTrue(native_overlap([{'time':'07:30','timezone':'Europe/Moscow','schedule':'weekly','days':[4]}],target))
