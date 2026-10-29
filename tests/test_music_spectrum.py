import array
import math
import unittest

from music_spectrum import fft_bands


def tone(frequencies, count=800, amplitude=.25):
    return array.array('h', [round(32767 * amplitude * sum(math.sin(2 * math.pi * f * i / 8000) for f in frequencies))
                             for i in range(count)]).tobytes()


class SpectrumTests(unittest.TestCase):
    def test_bass_mid_and_high_tones_fall_into_the_correct_band(self):
        for expected, frequency in enumerate((80, 700, 2500)):
            with self.subTest(frequency=frequency):
                levels = fft_bands(tone([frequency]))
                self.assertEqual(levels.index(max(levels)), expected)
                self.assertAlmostEqual(levels[expected], .25 / math.sqrt(2), delta=.005)
                self.assertLess(sum(v for i, v in enumerate(levels) if i != expected), .005)

    def test_mix_retains_all_three_bands(self):
        levels = fft_bands(tone([80, 700, 2500], amplitude=.1))
        for value in levels:
            self.assertAlmostEqual(value, .1 / math.sqrt(2), delta=.005)

    def test_silence_short_input_and_dc(self):
        for raw in (b'', b'\0\0', bytes(1600), array.array('h', [1000] * 800).tobytes()):
            self.assertEqual(fft_bands(raw), [0, 0, 0])

    def test_non_power_of_two_size_is_rejected(self):
        with self.assertRaises(ValueError):
            fft_bands(tone([80]), size=800)


if __name__ == '__main__':
    unittest.main()
