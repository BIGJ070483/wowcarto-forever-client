import struct
import unittest

from tools.inspect_native_icons import compare, inspect


def bitmap(changed_slot=None, palette_change=False):
    width, height = 464, 16
    palette = bytearray(1024)
    if palette_change:
        palette[4] = 1
    pixels = bytearray(width * height)
    if changed_slot is not None:
        pixels[changed_slot * 16 + 1] = 1
    size = 54 + len(palette) + len(pixels)
    return (struct.pack('<2sIHHI', b'BM', size, 0, 0, 1078) +
            struct.pack('<IiiHHIIiiII', 40, width, height, 1, 8, 0, len(pixels), 0, 0, 256, 256) +
            palette + pixels)


class NativeIconInspectorTests(unittest.TestCase):
    def test_identifies_changed_cell_without_claiming_gui_compatibility(self):
        original, replacement = bitmap(), bitmap(changed_slot=3)
        self.assertEqual(inspect(original)['slots'], 29)
        result = compare(original, replacement)
        self.assertEqual(result['changed_slots_zero_based'], [3])
        self.assertFalse(result['native_ui_tested'])
        self.assertFalse(result['meaning_verified'])

    def test_rejects_palette_geometry_and_truncation(self):
        reference = bitmap()
        with self.assertRaises(ValueError):
            compare(reference, bitmap(palette_change=True))
        with self.assertRaises(ValueError):
            inspect(reference[:-1])
        altered = bytearray(reference)
        struct.pack_into('<i', altered, 18, 928)
        with self.assertRaises(ValueError):
            inspect(bytes(altered))


if __name__ == '__main__':
    unittest.main()
