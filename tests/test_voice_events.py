import unittest
from test_native_alarms import field
from voice_events import FinalRecognition, pause_candidate, stop_conversation_frame, cancel_request_frame
from native_alarms import fields


def frame(text='пауза', request_id='request-1', final=True, status=3, response=False):
    request = field(1, text) + field(2, int(final))
    state = field(1, status) + field(7, request_id) + field(14 if response else 15, request)
    return field(252, state) + field(253, 3)


class VoiceEventTests(unittest.TestCase):
    def test_busy_or_partial_is_not_final(self):
        parser = FinalRecognition()
        self.assertIsNone(parser.read(frame(final=False)))
        self.assertIsNotNone(parser.read(frame(final=True)))

    def test_final_flag_is_authoritative_not_busy_status(self):
        self.assertIsNotNone(FinalRecognition().read(frame(status=1)))

    def test_duplicate_final_is_delivered_once_and_next_request_is_distinct(self):
        parser = FinalRecognition()
        self.assertIsNotNone(parser.read(frame()))
        self.assertIsNone(parser.read(frame()))
        self.assertIsNotNone(parser.read(frame(request_id='request-2')))

    def test_response_text_is_never_a_command(self):
        self.assertIsNone(FinalRecognition().read(frame(response=True)))

    def test_missing_request_or_invalid_utf8_is_rejected(self):
        self.assertIsNone(FinalRecognition().read(frame(request_id='')))
        self.assertIsNone(FinalRecognition().read(frame(text=b'\xff')))

    def test_dedupe_history_is_bounded(self):
        parser = FinalRecognition(limit=2)
        for i in range(3):parser.read(frame(request_id=str(i)))
        self.assertEqual(list(parser.seen), ['1', '2'])

    def test_pause_allowlist_only_targets_library(self):
        event = FinalRecognition().read(frame(text='Поставь музыку на паузу!'))
        self.assertEqual(pause_candidate(event, 'library'), 'pause')
        self.assertIsNone(pause_candidate(event, 'yandex'))
        event = FinalRecognition().read(frame(text='Не ставь музыку на паузу'))
        self.assertIsNone(pause_candidate(event, 'library'))

    def test_malformed_or_wrong_envelope_is_not_a_command(self):
        for blob in (b'\x80', field(253, 210), field(252, b'\xff') + field(253, 3)):
            self.assertIsNone(FinalRecognition().read(blob))

    def test_experimental_stop_builder_matches_schema_without_sending(self):
        import struct
        wire = stop_conversation_frame('test-request')
        self.assertEqual(struct.unpack('<I', wire[:4])[0], len(wire)-4)
        envelope = fields(wire[4:])
        self.assertEqual(envelope[253], [210])
        call = fields(envelope[252][0])
        self.assertEqual(call[7], [b'AliceCapability'])
        method = fields(call[8][0])
        self.assertEqual(method, {1: [3], 10: [b'test-request']})
        for invalid in ('', 'x'*257, None):
            with self.assertRaises(ValueError):stop_conversation_frame(invalid)

    def test_cancel_builder_includes_silent_and_request_id(self):
        envelope=fields(cancel_request_frame('test-request')[4:])
        call=fields(envelope[252][0])
        self.assertEqual(call[7],[b'AliceCapability'])
        self.assertEqual(fields(call[8][0]),{1:[1],7:[1],10:[b'test-request']})
