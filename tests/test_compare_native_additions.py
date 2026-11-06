import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.compare_native_additions import compare

HEADER = b'#v001-id=1-private\r\n#1204-ab12\r\n'


def row(name=b'secret', region=b'7009', category=b'12', x=b'123', y=b'456', comment=b'private'):
    return b'|'.join([name, b'', region, category, x, y, b'0', b'0', b'', b'', b'', b'', b'0', b'', b'', comment]) + b'\r\n'


class CompareTests(unittest.TestCase):
    def test_each_changed_field_and_comment_segments(self):
        original = HEADER + row()
        for modified, expected in [(row(name=b'other'), 1), (row(region=b'24'), 3),
                                   (row(category=b'22'), 4), (row(x=b'124'), 5),
                                   (row(y=b'457'), 6), (row(comment=b'private|line'), 16)]:
            with self.subTest(expected=expected):
                self.assertEqual(compare(original, HEADER + modified)['changed_records'],
                                 [{'record_position': 1, 'changed_fields': [expected]}])

    def test_count_difference_refused(self):
        with self.assertRaises(ValueError):
            compare(HEADER + row(), HEADER + row() + row())

    def test_headers_and_legacy_bytes_without_disclosure_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / 'a', Path(directory) / 'b'
            a.write_bytes(HEADER + row(name=b'private\xe9'))
            b.write_bytes(HEADER.replace(b'id=1', b'id=2') + row(name=b'private\xe0'))
            originals = (a.read_bytes(), b.read_bytes())
            run = subprocess.run([sys.executable, '-m', 'tools.compare_native_additions', str(a), str(b)],
                                 capture_output=True, text=True, check=True)
            result = json.loads(run.stdout)
            self.assertEqual(result['headers_changed'], [True, False])
            self.assertEqual(result['changed_records'][0]['changed_fields'], [1])
            self.assertNotIn('private', run.stdout)
            self.assertEqual((a.read_bytes(), b.read_bytes()), originals)


if __name__ == '__main__':
    unittest.main()
