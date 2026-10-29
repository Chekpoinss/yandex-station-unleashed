import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from light_profiles import DEFAULTS, change, settings
from music_lights import MusicLights, frame_for


class ProfileSettingsTests(unittest.TestCase):
    def test_create_apply_rename_delete_roundtrip(self):
        custom = settings(DEFAULTS, {'enabled': True, 'analysis': 'fft', 'sensitivity': 2,
                                    'palette': ['#FF0000', '#0000ff'], 'weights': [2, 1, .5]})
        current, profiles, selected = change(custom, {}, None, {'profile_action': 'save', 'name': 'Bass'})
        current, profiles, selected = change(DEFAULTS, profiles, None, {'profile_action': 'apply', 'name': 'Bass'})
        self.assertEqual(current['analysis'], 'fft')
        self.assertEqual(current['palette'], ['#ff0000', '#0000ff'])
        self.assertFalse(current['enabled'])
        current, profiles, selected = change(current, profiles, selected, {'profile_action': 'rename', 'old': 'Bass', 'name': 'Night'})
        self.assertEqual(selected, 'Night')
        self.assertNotIn('Bass', profiles)
        current, profiles, selected = change(current, profiles, selected, {'profile_action': 'delete', 'name': 'Night'})
        self.assertEqual(profiles, {})
        self.assertIsNone(selected)
        self.assertEqual(current['analysis'], 'fft')

    def test_invalid_update_is_transactional(self):
        original = copy.deepcopy(DEFAULTS)
        for update in ({'sensitivity': float('nan')}, {'analysis': 'unknown'}, {'palette': ['red']},
                       {'weights': [1, 2]}, {'smoothing': 9}, {'enabled': 'false'}):
            with self.subTest(update=update), self.assertRaises((ValueError, TypeError)):
                change(original, {}, None, {'brightness': 100, **update})
            self.assertEqual(original, DEFAULTS)

    def test_rename_does_not_overwrite_another_profile(self):
        with self.assertRaises(ValueError):
            change(DEFAULTS, {'one': {}, 'two': {}}, 'one', {'profile_action': 'rename', 'old': 'one', 'name': 'two'})

    def test_profile_limit_and_missing_profile(self):
        with self.assertRaises(ValueError):
            change(DEFAULTS, {str(i): {} for i in range(32)}, None, {'profile_action': 'save', 'name': '33'})
        with self.assertRaises(ValueError):
            change(DEFAULTS, {}, None, {'profile_action': 'apply', 'name': 'missing'})

    def test_configuration_persists_and_restores_without_creating_extra_tasks(self):
        data = {}
        library = SimpleNamespace(data=data, lock=threading.RLock(), save=Mock())
        with patch('music_lights.threading.Thread.start') as start:
            first = MusicLights(library, True)
            first.configure({'analysis': 'fft', 'sensitivity': 1.5, 'profile_action': 'save', 'name': 'Saved'})
            first.configure({'enabled': True})
            self.assertEqual(start.call_count, 1)
            restored = MusicLights(library, True)
        self.assertTrue(restored.settings['enabled'])
        self.assertEqual(restored.settings['analysis'], 'fft')
        self.assertEqual(restored.selected_profile, 'Saved')
        self.assertIn('Saved', restored.status()['profiles'])

    def test_custom_palette_obeys_channels_and_brightness(self):
        frame = frame_for([.4, .4, .4], 90, palette=['#ff0000'])
        self.assertTrue(all(g == b == 0 and 0 < r <= 90 for r, g, b in frame))
        flash = frame_for([.4, .4, .4], 90, bass_pulse=1, palette=['#ff0000'])
        self.assertTrue(all(0 <= value <= 90 for row in flash for value in row))


if __name__ == '__main__':
    unittest.main()
