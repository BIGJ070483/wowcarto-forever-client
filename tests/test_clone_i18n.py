import unittest

from tools.clone_i18n import translate


class InterfaceLanguageTests(unittest.TestCase):
    def test_known_labels_and_database_prefix(self):
        self.assertEqual(translate('Thème sombre', 'en'), 'Dark theme')
        self.assertEqual(translate('Base : EN', 'en'), 'Database: EN')
        self.assertEqual(translate('Thème sombre', 'fr'), 'Thème sombre')

    def test_user_and_database_text_is_not_guessed(self):
        self.assertEqual(translate('Féralas', 'en'), 'Féralas')


if __name__ == '__main__':
    unittest.main()
