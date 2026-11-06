"""Synthetic configuration safety; never a firewall or native GUI proof."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

from tools.inspect_native_config import KEY, inspect
from tools.native_lab import configure, loopback_config


class NativeLabTests(unittest.TestCase):
    def test_every_encoded_destination_is_loopback_and_round_trips(self):
        data = loopback_config(8766)
        self.assertEqual(inspect(data)['line_count'], 9)
        raw = zlib.decompress(data)
        lines = bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(raw)).splitlines()
        self.assertEqual(lines[2:5], [b'http://127.0.0.1:8766/', b'', b'manifest'])
        self.assertTrue(all(lines[i].startswith(b'http://127.0.0.1:8766/') for i in (2, 5, 6)))
        for invalid in (0, 65536, True, '8766'):
            with self.assertRaises(ValueError): loopback_config(invalid)

    def test_configure_restore_and_refusal_to_overwrite_new_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            originals = {}
            for name in ('fr', 'us'):
                path = root / name
                path.write_bytes(loopback_config(1234))
                originals[name] = path, path.read_bytes()
            with patch('tools.native_lab.lab', return_value=(root / 'fake.exe', originals)):
                result = configure(root, root, 8766)
                self.assertFalse(result['native_runtime_verified'])
                self.assertEqual(originals['fr'][0].read_bytes(), loopback_config(8766))
                configure(root, root, 8766, restore=True)
                self.assertEqual(originals['fr'][0].read_bytes(), originals['fr'][1])
                originals['us'][0].write_bytes(b'new private changes')
                with self.assertRaises(ValueError): configure(root, root, 8766)
                self.assertEqual(originals['fr'][0].read_bytes(), originals['fr'][1])

    def test_second_file_failure_rolls_back_first(self):
        import os
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a, b = root / 'a', root / 'b'
            original = loopback_config(1234)
            a.write_bytes(original); b.write_bytes(original)
            replace = os.replace
            def fail_second(source, target):
                if target == b: raise OSError('synthetic disk failure')
                replace(source, target)
            with patch('tools.native_lab.lab', return_value=(root / 'fake.exe', {'a': (a, original), 'b': (b, original)})), \
                 patch('tools.native_lab.os.replace', side_effect=fail_second):
                with self.assertRaises(OSError): configure(root, root, 8766)
            self.assertEqual(a.read_bytes(), original)
            self.assertEqual(b.read_bytes(), original)
