import array
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from music_lights import BassPulse, band_levels, frame_for


class MusicLightsTests(unittest.TestCase):
    def tone(self, hz):
        return array.array('h', [round(12000 * math.sin(2 * math.pi * hz * i / 8000))
                                 for i in range(800)]).tobytes()

    def test_silence_stays_dark(self):
        self.assertEqual(frame_for(band_levels(bytes(1600)), 90), [[0, 0, 0]] * 3)

    def test_bass_and_treble_produce_different_dominant_bands(self):
        bass, treble = band_levels(self.tone(80)), band_levels(self.tone(3000))
        self.assertEqual(max(range(3), key=lambda i: bass[i]), 0)
        self.assertEqual(max(range(3), key=lambda i: treble[i]), 2)
        self.assertNotEqual(frame_for(bass, 90), frame_for(treble, 90))

    def test_louder_signal_increases_light_without_exceeding_cap(self):
        quiet = frame_for([.01, .01, .01], 90)
        loud = frame_for([.4, .4, .4], 90)
        self.assertGreater(sum(map(sum, loud)), sum(map(sum, quiet)))
        self.assertTrue(all(0 <= c <= 90 for zone in loud for c in zone))

    def test_bass_hit_flashes_then_decays_and_retriggers(self):
        bass = BassPulse()
        for _ in range(20):
            bass.update(.02)
        hit = bass.update(.3)
        self.assertGreater(hit, .8)
        for _ in range(12):
            bass.update(.02)
        self.assertLess(bass.pulse, .02)
        self.assertGreater(bass.update(.3), .8)

    def test_sustained_bass_does_not_stay_in_flash_mode(self):
        bass = BassPulse()
        for _ in range(60):
            bass.update(.2)
        self.assertLess(bass.pulse, .02)
        self.assertEqual(BassPulse().update(0), 0)

    def test_kick_boost_is_visible_with_same_audio_and_respects_brightness(self):
        normal = frame_for([.2, .1, .05], 110, 3, 0)
        hit = frame_for([.2, .1, .05], 110, 3, 1)
        self.assertGreater(sum(map(sum, hit)), sum(map(sum, normal)) * 1.3)
        self.assertTrue(all(0 <= c <= 110 for zone in hit for c in zone))


if __name__ == '__main__':
    unittest.main()
