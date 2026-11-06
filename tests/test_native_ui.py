"""Bounded DFM parsing with synthetic forms; no proprietary resources."""
import struct
import unittest
from unittest.mock import patch

from tools.inspect_native_ui import parse_dfm, summarize_dfm


def short(text):
    data = text.encode('cp1252')
    return bytes([len(data)]) + data


def prop(name, value):
    if type(value) is int:
        payload = b'\x04' + struct.pack('<i', value)
    else:
        payload = b'\x07' + short(value)
    return short(name) + payload


def component(kind, name, props=b'', children=b''):
    return short(kind) + short(name) + props + b'\0' + children + b'\0'


def fixture():
    button = component('TToolButton', 'SearchButton', prop('ImageIndex', 19) + prop('OnClick', 'SearchClick'))
    toolbar = component('TToolBar', 'Toolbar', prop('Images', 'Normal') + prop('HotImages', 'Hover'), button)
    images = component('TImageList', 'Normal', prop('Width', 16) + prop('Height', 16))
    return b'TPF0' + component('TMainForm', 'MainForm', prop('PixelsPerInch', 96), toolbar + images)


class NativeUiTests(unittest.TestCase):
    def test_reports_design_time_links_without_asserting_runtime_or_semantics(self):
        result = summarize_dfm(fixture())
        self.assertEqual(result['pixels_per_inch'], 96)
        self.assertEqual(result['commands'], [{'component': 'SearchButton', 'class': 'TToolButton',
            'owner': 'Toolbar', 'images': 'Normal', 'hot_images': 'Hover',
            'image_index_zero_based': 19, 'event': 'SearchClick', 'tag': None,
            'separator': False, 'explicit_visible': None, 'explicit_enabled': None}])
        self.assertFalse(result['runtime_loading_verified'])
        self.assertFalse(result['visual_meaning_verified'])
        self.assertEqual(result['image_lists'][0]['explicit_width'], 16)

    def test_truncated_unknown_and_trailing_data_are_rejected(self):
        for value in (fixture()[:-1], fixture() + b'\0', b'TPF0\x20', b'not a form',
                      b'TPF0' + component('TMainForm', 'MainForm', short('Unknown') + b'\xff')):
            with self.subTest(value=value[:16]), self.assertRaises(ValueError):
                parse_dfm(value)

    def test_binary_payload_is_omitted_and_length_is_checked(self):
        payload = b'private-bitmap-pixels'
        data = b'TPF0' + component('TForm', 'Form', short('Glyph.Data') + b'\x0a' + struct.pack('<I', len(payload)) + payload)
        result = parse_dfm(data)
        self.assertEqual(result['properties']['Glyph.Data'], {'binary_bytes': len(payload)})
        self.assertNotIn(payload.decode(), str(result))
        corrupted = data.replace(struct.pack('<I', len(payload)), struct.pack('<I', 0xffffffff), 1)
        with self.assertRaises(ValueError):
            parse_dfm(corrupted)

    def test_component_depth_and_property_budget(self):
        nested = component('TPanel', 'Inner')
        for i in range(12):
            nested = component('TPanel', 'Panel' + str(i), children=nested)
        with patch('tools.inspect_native_ui.MAX_DEPTH', 8), self.assertRaises(ValueError):
            parse_dfm(b'TPF0' + nested)
        with patch('tools.inspect_native_ui.MAX_VALUES', 2), self.assertRaises(ValueError):
            parse_dfm(fixture())

    def test_collection_and_set_preserve_structure(self):
        collection = b'\x0e\x01' + prop('ImageIndex', 2) + b'\0\0'
        options = b'\x0b' + short('OptionOne') + short('OptionTwo') + b'\0'
        result = parse_dfm(b'TPF0' + component('TForm', 'Form', short('Items') + collection + short('Options') + options))
        self.assertEqual(result['properties']['Items'], [{'ImageIndex': 2}])
        self.assertEqual(result['properties']['Options'], ['OptionOne', 'OptionTwo'])


if __name__ == '__main__':
    unittest.main()
