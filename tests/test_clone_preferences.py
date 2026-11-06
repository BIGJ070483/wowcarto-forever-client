import json
from pathlib import Path
import tempfile
import unittest

from tools.clone_app import Application


class Status:
    def set(self, value):
        self.value = value


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application.__new__(Application)
        self.app.preferences_path = Path(self.temp.name) / 'client-preferences.json'
        self.app.status = Status()

    def test_round_trip_preserves_unrelated_options(self):
        self.app.preferences = {'theme': 'dark', 'database': 'US', 'future': 'preserved'}
        self.app.save_preferences()
        self.assertEqual(self.app.load_preferences(), self.app.preferences)

    def test_invalid_file_is_not_overwritten(self):
        self.app.preferences_path.write_text('{broken', encoding='utf-8')
        self.app.preferences = self.app.load_preferences()
        self.app.preferences['theme'] = 'dark'
        self.app.save_preferences()
        self.assertEqual(self.app.preferences_path.read_text(encoding='utf-8'), '{broken')
        self.assertIn('conservé', self.app.status.value)


if __name__ == '__main__':
    unittest.main()
