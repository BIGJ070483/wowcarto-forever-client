import unittest

from tools.native_additions import parse_additions


def record(name=b'Nom', category=b'12', comments=(b'premier', b'deuxieme')):
    fields = [name, b'', b'7009', category, b'123', b'456', b'0', b'0',
              b'', b'', b'', b'', b'00000000000000000000000000000000', b'', b'']
    return b'|'.join(fields + list(comments))


class NativeAdditionsReaderTests(unittest.TestCase):
    def test_preserves_bytes_empty_fields_and_multiline_comments(self):
        row = record(name=b'essai \xe9\xe0\xe7', comments=(b'ligne \xe9', b'', b'fin'))
        data = b'#v001-id=1-fictif\r\n#1204-ab12\r\n' + row + b'\r\n'
        result = parse_additions(data)
        self.assertEqual(len(result.records), 1)
        item = result.records[0]
        self.assertEqual(item.name, b'essai \xe9\xe0\xe7')
        self.assertEqual(item.category_code, 12)
        self.assertEqual(item.comment_lines, (b'ligne \xe9', b'', b'fin'))
        self.assertEqual(b'|'.join(item.fields), row)

    def test_category_variation_and_separate_records(self):
        data = b'#v001-id=2-fictif\n#1204-ab12\n' + record(category=b'12') + b'\n' + record(category=b'22') + b'\n'
        self.assertEqual([item.category_code for item in parse_additions(data).records], [12, 22])

    def test_rejects_unknown_version_and_truncated_records(self):
        header = b'#v001-id=1-fictif\n#1204-ab12\n'
        for data in (header + b'name|12\n', header + record(category=b'abc'),
                     b'#v002-id=1-fictif\n#1204-ab12\n' + record(), header + record() + b'\x00'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_additions(data)


if __name__ == '__main__':
    unittest.main()
