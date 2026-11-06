"""Synthetic image generation; no original resources are distributed."""
from pathlib import Path
from io import BytesIO
import tempfile
import unittest

from PIL import Image

from tests.test_native_icons import bitmap
from tools.create_hd_icon_candidates import generate


class HdCandidatesTests(unittest.TestCase):
    def test_preserves_cell_positions_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / 'source', root / 'candidates'
            source.mkdir()
            for name in ('icon_a.bmp', 'icon_b.bmp'):
                (source / name).write_bytes(bitmap(changed_slot=3, palette_change=True))
            report = generate(source, output)
            self.assertFalse(report['native_compatibility_verified'])
            self.assertFalse(report['new_detail_created'])
            self.assertEqual(len(report['files']), 4)
            with Image.open(BytesIO((output / 'icon_a-2x.bmp').read_bytes())) as image:
                self.assertEqual((image.mode, image.size), ('P', (928, 32)))
                self.assertEqual(image.getpixel((3 * 32 + 2, 30)), 1)
                self.assertEqual(image.getpixel((2 * 32 + 2, 30)), 0)
            with self.assertRaises(ValueError):
                generate(source, output)


if __name__ == '__main__':
    unittest.main()
