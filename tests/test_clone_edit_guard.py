import unittest

from tools.clone_app import Application


class Value:
    def __init__(self, value): self.value = value
    def get(self): return self.value


class Status:
    def set(self, value): self.value = value


class EditGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = Application.__new__(Application)
        self.app.mode = Value('Référence 1.20')
        self.app.database = Value('Francais')
        self.app.edit_database = Value('Francais')
        self.app.edit_enabled = Value(False)
        self.app.ui_language = Value('fr')
        self.app.status = Status()

    def test_edit_requires_both_mode_and_matching_language(self):
        self.assertFalse(self.app.can_edit())
        self.app.edit_enabled.value = True
        self.assertTrue(self.app.can_edit())
        self.app.database.value = 'US'
        self.assertFalse(self.app.can_edit())
        self.app.edit_database.value = 'US'
        self.assertTrue(self.app.can_edit())
        self.app.mode.value = 'Modern'
        self.assertFalse(self.app.can_edit())
        self.assertTrue(self.app.can_edit(allow_modern=True))
        self.app.edit_enabled.value = False
        self.assertFalse(self.app.can_edit(allow_modern=True))
        self.app.edit_enabled.value = True
        self.app.edit_database.value = 'Francais'
        self.assertFalse(self.app.can_edit(allow_modern=True))


if __name__ == '__main__':
    unittest.main()
