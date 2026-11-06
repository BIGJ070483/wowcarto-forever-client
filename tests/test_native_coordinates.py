import unittest
from tools.native_additions import Addition
from tools.native_coordinates import observed_position


def record(identifier=b'17', x=b'25711', y=b'35957'):
    return Addition((b'fictif', b'', identifier, b'37', x, y,
                     b'0', b'0', b'', b'', b'', b'', b'0', b'', b'', b'commentaire'))


class CoordinateReaderTests(unittest.TestCase):
    def test_observed_family_mapping_and_local_fraction_preserved(self):
        a = observed_position(record())
        b = observed_position(record(identifier=b'106'))
        self.assertEqual((a.tile_basename, b.tile_basename), ('azeroth17.jpg', 'elf06.jpg'))
        self.assertEqual((a.pixel_x, a.pixel_y), (100.43359375, 140.45703125))
        self.assertEqual(a.raw_x, b.raw_x)
        self.assertTrue(a.experimental)

    def test_never_extrapolates_unknown_family_or_malformed_coordinates(self):
        for item in (record(identifier=b'7009'), record(identifier=b'999'),
                     record(x=b'-1'), record(x=b'65536'), record(y=b'1.5'),
                     record(x=b'999999999999999999')):
            with self.subTest(item=item), self.assertRaises(ValueError):
                observed_position(item)


if __name__ == '__main__':
    unittest.main()
