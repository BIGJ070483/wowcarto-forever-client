from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from tools.inspect_native_loc import inspect_bytes

from tools.native_database import decode_annotations, decode_categories, decode_flags, decode_topology


def annotation_stream(name=b'Test \xe9', count=1):
    entry = (b'\x01' + struct.pack('<H', len(name)) + name + b'\x02\x11\x00\x03\x0c'
             b'\x04\xff\xff\x05\x00\x80\x0b\x07\x00\x00\x00'
             b'\x0c\x0b\x00Ligne|suite\x15\x00\x00\x00\x0a\xc7')
    return b'WOWCV101' + entry + b'\xc8' + struct.pack('<I', 7) + b'\xc9' + struct.pack('<I', count) + b'\xca' + struct.pack('<d', 40000.) + b'\xff'


class NativeDatabaseTests(unittest.TestCase):
    def test_legacy_header_requires_explicit_version_and_retains_stream_guards(self):
        streams = [zlib.compress(b'fixture') + (b'\0'*8 if i < 9 else b'') for i in range(10)]
        offsets = []; position = 72
        for stream in streams:
            offsets.append(position); position += len(stream)
        data = struct.pack('<18I', 1062, *offsets, 11,12,13,14,15,0,0) + b''.join(streams)
        with self.assertRaises(ValueError): inspect_bytes(data)
        self.assertEqual(inspect_bytes(data, expected_version=1062)['layout'], 'observed_1062_header_ten_zlib_streams')
        with self.assertRaises(ValueError): inspect_bytes(data[:-2], expected_version=1062)
    def test_tlv_accent_comment_properties_and_final_count(self):
        data = annotation_stream()
        flags = {57: {'name': 'Type'}, 59: {'name': 'Bestiole'}}
        rows, trailer = decode_annotations(data, flags)
        self.assertEqual(rows[0]['name'], 'Test é')
        self.assertEqual(rows[0]['comment'], 'Ligne\nsuite')
        self.assertEqual((rows[0]['map_id'], rows[0]['x_raw'], rows[0]['y_raw']), (17, 65535, 32768))
        self.assertEqual(rows[0]['property_names'], ['Type', 'Bestiole'])
        self.assertEqual(trailer['record_count'], 1)
        with self.assertRaisesRegex(ValueError, 'Compteur'):
            decode_annotations(annotation_stream(count=2))
        with self.assertRaisesRegex(ValueError, 'inconnu'):
            decode_annotations(data[:8] + b'\x09' + data[8:])
        with self.assertRaises(ValueError):
            decode_annotations(data[:-1])

    def test_category_uses_native_id_not_order(self):
        block = bytearray(188)
        block[:7] = b'\x06Banque'
        block[21:24] = bytes((12, 15, 0))
        struct.pack_into('<I', block, 24, 0x003AFC83)
        categories = decode_categories(bytes(block))
        self.assertEqual(categories[12]['name'], 'Banque')
        self.assertEqual(categories[12]['icon_index'], 15)
        self.assertEqual(categories[12]['color_rgb'], '#83fc3a')

    def test_native_signed_origins_keep_island_offsets(self):
        continent = bytearray(64)
        continent[:9] = b'\x08Kalimdor'
        struct.pack_into('<II', continent, 44, 2, 2)
        group = bytearray(80)
        struct.pack_into('<HBBII', group, 0, 1000, 2, 2, 1041, 0)
        group[12:21] = b'\x08Kalimdor'
        tile = bytearray(92)
        tile[:7] = b'\x06drae00'
        tile[31:38] = b'\x06drae00'
        struct.pack_into('<HHBB', tile, 48, 1100, 1000, 2, 2)
        struct.pack_into('<ii', tile, 56, -262140, 140000)
        data = struct.pack('<I', 1) + continent + struct.pack('<I', 1) + group + tile
        result = decode_topology(data)
        self.assertEqual(result['tiles'][0]['filename'], 'drae00.jpg')
        self.assertEqual((result['tiles'][0]['x'], result['tiles'][0]['y']), (-1023.984375, -546.875))
        self.assertEqual(result['groups'][0]['default_tile_id'], 1041)
        with self.assertRaises(ValueError):
            decode_topology(data[:-1])

    def test_property_table_accepts_historical_special_separator(self):
        flags = decode_flags(b'015|000|AD04003Alliance\r\n017|000|078ADD|Neutre/Contest\xe9\r\n@eof')
        self.assertEqual(flags[15]['name'], 'Alliance')
        self.assertEqual(flags[17]['name'], 'Neutre/Contesté')


if __name__ == '__main__':
    unittest.main()
