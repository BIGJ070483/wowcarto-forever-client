import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.inspect_native_icons import compare
from tools.prepare_native_icon_pair import build_strip, prepare
from test_native_icons import bitmap


def source_bitmap():
    data = bytearray(bitmap())
    # Index 1 is white; index 2 duplicates the transparent mask's black.
    data[58:61] = b'\xff\xff\xff'
    return bytes(data)


def artwork():
    result = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
    result.putpixel((7, 7), (0, 0, 0, 255))
    return result


class NativeIconPairTests(unittest.TestCase):
    def test_only_requested_slot_changes_and_mask_color_is_not_reused(self):
        original = source_bitmap()
        changed = build_strip(original, artwork(), 6)
        self.assertEqual(compare(original, changed)['changed_slots_zero_based'], [6])
        offset = 1078 + (15 - 7) * 464 + 6 * 16 + 7
        self.assertEqual(changed[offset], 1)

    def test_rejects_partial_alpha_invalid_size_and_empty_art(self):
        image = artwork()
        image.putpixel((3, 3), (0, 0, 0, 128))
        for value in (image, Image.new('RGBA', (32, 32)), Image.new('RGBA', (16, 16))):
            with self.assertRaises(ValueError):
                build_strip(source_bitmap(), value, 0)
        with self.assertRaises(ValueError):
            build_strip(source_bitmap(), artwork(), 29)

    def test_private_outputs_keep_inputs_and_refuse_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            original = source_bitmap()
            for name in ('icon_a.bmp', 'icon_b.bmp'):
                (source / name).write_bytes(original)
            for name in ('normal.png', 'hover.png'):
                artwork().save(root / name)
            output = root / 'candidate'
            result = prepare(source, root / 'normal.png', root / 'hover.png', output, 6)
            self.assertFalse(result['native_ui_tested'])
            self.assertEqual((source / 'icon_a.bmp').read_bytes(), original)
            self.assertEqual(compare(original, (output / 'icon_b.bmp').read_bytes())['changed_slots_zero_based'], [6])
            with self.assertRaises(ValueError):
                prepare(source, root / 'normal.png', root / 'hover.png', output, 6)


if __name__ == '__main__':
    unittest.main()
