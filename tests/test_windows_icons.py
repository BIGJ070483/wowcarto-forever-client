import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from test_native_icons import bitmap
from tools import windows_icons as icons


class WindowsIconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'target'
        self.assets = self.root / 'assets'
        data = self.target / 'client' / 'data'
        data.mkdir(parents=True)
        self.assets.mkdir()
        self.original = [bitmap(), bitmap(changed_slot=1)]
        self.candidate = [bitmap(changed_slot=2), bitmap(changed_slot=3)]
        for name, old, new in zip(icons.NAMES, self.original, self.candidate):
            (data / name).write_bytes(old)
            (self.assets / name).write_bytes(new)
        sha = lambda value: hashlib.sha256(value).hexdigest()
        self.old_patch = mock.patch.object(icons, 'ORIGINAL', tuple(map(sha, self.original)))
        self.new_patch = mock.patch.object(icons, 'CANDIDATE', tuple(map(sha, self.candidate)))
        self.old_patch.start(); self.new_patch.start()
        self.addCleanup(self.old_patch.stop); self.addCleanup(self.new_patch.stop)
        self.running = lambda exe: False

    def read_pair(self):
        return [(self.target / 'client' / 'data' / n).read_bytes() for n in icons.NAMES]

    def test_install_and_restore_back_up_each_strip(self):
        self.assertEqual(icons.change_icons(self.target, self.assets, running=self.running), 'applied')
        self.assertEqual(self.read_pair(), self.candidate)
        self.assertEqual(icons.change_icons(self.target, self.assets, running=self.running), 'already')
        self.assertEqual(icons.change_icons(self.target, self.assets, restore=True, running=self.running), 'restored')
        self.assertEqual(self.read_pair(), self.original)
        for name, data in zip(icons.NAMES, self.original):
            self.assertEqual((self.target / 'wcf-icones-originales' / name).read_bytes(), data)

    def test_running_client_and_custom_icon_are_never_overwritten(self):
        with self.assertRaises(ValueError):
            icons.change_icons(self.target, self.assets, running=lambda exe: True)
        (self.target / 'client' / 'data' / icons.NAMES[0]).write_bytes(b'personal icon')
        with self.assertRaises(ValueError):
            icons.change_icons(self.target, self.assets, running=self.running)
        self.assertEqual(self.read_pair()[0], b'personal icon')

    def test_interrupted_transaction_recovers_originals(self):
        icons.change_icons(self.target, self.assets, running=self.running)
        marker = self.target / 'wcf-icones-en-cours.json'
        marker.write_text(json.dumps({'format': 'wcf-icons-transaction-v1'}))
        (self.target / 'client' / 'data' / icons.NAMES[0]).write_bytes(self.original[0])
        self.assertTrue(icons.recover(self.target, running=self.running))
        self.assertEqual(self.read_pair(), self.original)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
