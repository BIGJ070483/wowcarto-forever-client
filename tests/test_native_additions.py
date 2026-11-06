import hashlib
import unittest

from tools.inspect_native_additions import MAX_BYTES, MAX_LINE, inspect


class NativeAdditionsTests(unittest.TestCase):
    def test_structure_and_redaction(self):
        data = (b'#v001-id=42-private-name\r\n#1204-abcdef123456\r\n'
                b'PRIVATE||10|7|11|22|0|0|||||00000000060000000000000000000000|||PRIVATE\r\n')
        result = inspect(data)
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['field_counts'], {16: 1})
        self.assertEqual(result['record_lines'], 1)
        self.assertEqual(result['crlf_count'], 3)
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotIn('private-name', str(result))

    def test_multiple_records_and_empty_fields_preserved(self):
        data = b'#v001-id=1-x\n#1204-a\nA||B\nC|D\n'
        result = inspect(data)
        self.assertEqual(result['field_counts'], {2: 1, 3: 1})
        self.assertEqual(result['other_lf_count'], 4)

    def test_legacy_accent_bytes_are_inspected_without_decoding_or_disclosure(self):
        data = b'#v001-id=1-x\r\n#1204-a\r\nnom \xe9\xe0\xe7||texte \xe9\xe0\xe7\r\n'
        result = inspect(data)
        self.assertEqual(result['field_counts'], {3: 1})
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertNotIn('nom', str(result))

    def test_extra_separator_in_record_is_reported_without_truncation(self):
        prefix = b'#v001-id=1-x\r\n#1204-a\r\n'
        record = b'|'.join([b'x'] * 16) + b'|second line\r\n'
        result = inspect(prefix + record)
        self.assertEqual(result['record_lines'], 1)
        self.assertEqual(result['field_counts'], {17: 1})
        self.assertNotIn('second line', str(result))

    def test_reject_bad_inputs(self):
        values = [b'', b'#v001-id=1-x\n#1204-a\nno-delimiter\n',
                  b'#v001-id=1-x\n#1204-a\na|\x00b\n',
                  b'#v001-id=1-x\n#1204-a\n' + b'a' * (MAX_LINE + 1),
                  b'x' * (MAX_BYTES + 1)]
        for value in values:
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                inspect(value)


if __name__ == '__main__':
    unittest.main()
