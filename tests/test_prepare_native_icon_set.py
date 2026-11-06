import tempfile
import unittest
from pathlib import Path

from test_prepare_native_icon_pair import artwork, source_bitmap
from tools.prepare_native_icon_set import prepare_set


class NativeIconSetTests(unittest.TestCase):
    def test_complete_set_and_missing_artwork_do_not_mutate_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, drawings, output = root / 'source', root / 'drawings', root / 'output'
            source.mkdir()
            drawings.mkdir()
            original = source_bitmap()
            for name in ('icon_a.bmp', 'icon_b.bmp'):
                (source / name).write_bytes(original)
            for slot in range(29):
                for state in ('normal', 'hover'):
                    artwork().save(drawings / f'{slot:02d}-{state}.png')
            (drawings / '28-hover.png').unlink()
            with self.assertRaises(OSError):
                prepare_set(source, drawings, output)
            self.assertFalse(output.exists())
            artwork().save(drawings / '28-hover.png')
            result = prepare_set(source, drawings, output)
            self.assertEqual(len(result['artwork']), 58)
            self.assertEqual(result['strips'][0]['changed_slots_zero_based'], list(range(29)))
            self.assertEqual(result['strips'][1]['changed_slots_zero_based'], list(range(29)))
            self.assertFalse(result['native_ui_tested'])
            self.assertEqual((source / 'icon_a.bmp').read_bytes(), original)
            with self.assertRaises(ValueError):
                prepare_set(source, drawings, output)


if __name__ == '__main__':
    unittest.main()
