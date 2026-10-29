import json
import unittest
from playback_order import PlaybackOrder


class PlaybackOrderTests(unittest.TestCase):
    def make(self, ids=None):
        ids = ids or ['a', 'b', 'c', 'd']
        return PlaybackOrder(ids, ids[0], shuffler=lambda values: values.reverse())

    def test_shuffle_visits_the_entire_queue_once_and_then_stops(self):
        q = self.make()
        q.set_shuffle(True)
        played = [q.current]
        while value := q.next():
            played.append(value)
        self.assertEqual(played, ['a', 'd', 'c', 'b'])
        self.assertEqual(len(played), len(set(played)))

    def test_previous_and_forward_follow_actual_shuffle_history(self):
        q = self.make()
        q.set_shuffle(True)
        self.assertEqual(q.next(), 'd')
        self.assertEqual(q.next(), 'c')
        self.assertEqual(q.previous(), 'd')
        self.assertEqual(q.previous(), 'a')
        self.assertEqual(q.next(), 'd')
        self.assertEqual(q.next(), 'c')
        self.assertEqual(q.next(), 'b')

    def test_toggle_keeps_history_and_does_not_replay_visited_shuffle_tracks(self):
        q = self.make()
        q.set_shuffle(True)
        q.next()
        q.set_shuffle(False)
        self.assertEqual(q.previous(), 'a')
        self.assertEqual(q.next(), 'd')
        q.set_shuffle(True)
        self.assertEqual(q.next(), 'c')
        self.assertEqual(q.next(), 'b')
        self.assertIsNone(q.next())

    def test_repeat_off_all_and_one(self):
        for shuffle in (False, True):
            q = self.make(['a', 'b'])
            q.set_shuffle(shuffle)
            self.assertEqual(q.next(automatic=True), 'b')
            self.assertIsNone(q.next(automatic=True))
            q.repeat = 'All'
            self.assertEqual(q.next(automatic=True), 'a')
            q.repeat = 'One'
            self.assertEqual(q.next(automatic=True), 'a')
            self.assertEqual(q.next(automatic=False), 'b')

    def test_repeat_all_shuffle_cycles_cover_every_track_without_boundary_repeat(self):
        q = self.make()
        q.set_shuffle(True)
        q.repeat = 'All'
        first = [q.current] + [q.next() for _ in range(3)]
        second = [q.next() for _ in range(4)]
        self.assertEqual(set(first), set(q.base))
        self.assertEqual(set(second), set(q.base))
        self.assertNotEqual(first[-1], second[0])

    def test_single_track(self):
        for shuffle in (False, True):
            q = self.make(['a'])
            q.set_shuffle(shuffle)
            self.assertIsNone(q.next())
            for mode in ('All', 'One'):
                q.repeat = mode
                self.assertEqual(q.next(automatic=True), 'a')
            q.repeat = 'None'
            self.assertIsNone(q.next())

    def test_restart_preserves_order_options_and_history(self):
        q = self.make()
        q.set_shuffle(True)
        q.repeat = 'One'
        q.next()
        saved = json.loads(json.dumps(q.dump()))
        restored = PlaybackOrder.restore(saved, q.base, shuffler=lambda values: values.reverse())
        self.assertEqual(restored.dump(), saved)
        self.assertEqual(restored.previous(), 'a')
        self.assertEqual(restored.next(), 'd')
        self.assertEqual(restored.next(automatic=True), 'd')

    def test_enqueue_and_invalid_saved_ids(self):
        q = self.make(['a'])
        q.set_shuffle(True)
        q.append('b')
        self.assertEqual(q.next(), 'b')
        restored = PlaybackOrder.restore(q.dump(), ['a'])
        self.assertNotIn('b', restored.order)
        self.assertNotIn('b', restored.history)


if __name__ == '__main__':
    unittest.main()
