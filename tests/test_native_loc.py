"""Synthetic containers only; no proprietary databases or resources."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/inspect_native_loc.py'
spec = importlib.util.spec_from_file_location('native_loc', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(payload=b'SYNTHETIC'):
    chunks = [zlib.compress(payload + bytes([index])) for index in range(10)]
    offsets, body = [], b''
    for index, chunk in enumerate(chunks):
        offsets.append(72 + len(body))
        body += chunk + (b'\0' * 8 if index < 9 else b'')
    return struct.pack('<18I', 1200, *offsets, 11, 12, 13, 14, 15, 0, 0) + body


class NativeLocTests(unittest.TestCase):
    def test_ten_streams_hashes_padding_and_read_only(self):
        data = fixture()
        before = hashlib.sha256(data).hexdigest()
        report = module.inspect_bytes(data)
        self.assertEqual(len(report['sections']), 10)
        self.assertEqual(report['sections'][0]['decoded_sha256'], hashlib.sha256(b'SYNTHETIC\0').hexdigest())
        self.assertEqual(report['sections'][0]['padding_bytes'], 8)
        self.assertEqual(report['sections'][-1]['padding_bytes'], 0)
        self.assertEqual(hashlib.sha256(data).hexdigest(), before)
        self.assertFalse(report['modified'])
        self.assertFalse(report['annotation_records_decoded'])

    def test_unknown_header_and_offsets_refused(self):
        for index, value in [(0, 1070), (1, 32), (2, 72), (3, 9999999)]:
            data = bytearray(fixture())
            struct.pack_into('<I', data, index * 4, value)
            with self.assertRaises(ValueError):
                module.inspect_bytes(data)

    def test_truncated_corrupt_and_unknown_padding_refused(self):
        data = fixture()
        for changed in (data[:-1], data[:72] + b'corrupt' + data[79:], data[:]):
            if changed == data:
                changed = bytearray(data)
                end = struct.unpack_from('<I', data, 8)[0]
                changed[end - 1] = 1
            with self.assertRaises(ValueError):
                module.inspect_bytes(changed)

    def test_section_and_total_budgets_refused(self):
        data = fixture(b'0' * 1000)
        with self.assertRaisesRegex(ValueError, 'par section'):
            module.inspect_bytes(data, section_limit=100)
        with self.assertRaisesRegex(ValueError, 'total'):
            module.inspect_bytes(data, total_limit=1500)

    def test_cli_never_overwrites_database_or_prior_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, report = root / 'master.loc', root / 'report.json'
            source.write_bytes(fixture())
            before = source.read_bytes()
            for output, expected in [(source, 2), (report, 0), (report, 2)]:
                result = subprocess.run([sys.executable, str(SCRIPT), str(source), '--output', str(output)], capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr.decode())
                self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
