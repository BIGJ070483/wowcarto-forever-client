"""Synthetic updater-wire checks; these do not execute WoWCartographe."""
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib

from tools.native_update_manifest import (
    FileRecord, Header, MAX_FILES, MAX_LINE, MAX_MANIFEST, NativeManifest,
    SYNTHETIC_PAYLOAD, build_manifest, make_fixture_server, parse_manifest,
    synthetic_fixture, verify_payload,
)


class NativeUpdateManifestTests(unittest.TestCase):
    def test_eight_field_fixture_raw_and_zlib_checksums(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                data, payload = synthetic_fixture(8766, compressed)
                manifest = parse_manifest(data)
                self.assertEqual(manifest.raw, data)
                self.assertEqual(len(manifest.files), 1)
                self.assertEqual(build_manifest(manifest), data)
                self.assertEqual(verify_payload(manifest.files[0], payload), SYNTHETIC_PAYLOAD)

    def test_legacy_text_bytes_are_retained_without_decoding(self):
        data, _ = synthetic_fixture(8766)
        data = data.replace(b'Essai fictif local', b'Essai \xe9\x80')
        manifest = parse_manifest(data)
        self.assertEqual(build_manifest(manifest), data)
        self.assertEqual(manifest.raw, data)

    def test_native_line_endings_but_not_bare_cr(self):
        data, _ = synthetic_fixture(8766)
        self.assertEqual(parse_manifest(data.replace(b'\r\n', b'\n')).files,
                         parse_manifest(data).files)
        self.assertEqual(parse_manifest(data[:-2]).files, parse_manifest(data).files)
        with self.assertRaises(ValueError):
            parse_manifest(data.replace(b'\r\n', b'\r'))

    def test_missing_eof_extra_eof_unknown_and_duplicate_headers_refused(self):
        data, _ = synthetic_fixture(8766)
        for bad in (data.replace(b'EOF\r\n', b''), data + b'EOF\r\n',
                    data.replace(b'IVRS=1204', b'XXXX=1204'),
                    b'VERS=second\r\n' + data, data.replace(b'VERS=', b'VERS=\0'),
                    data.replace(b'IVRS=1204', b'IVRS=-1')):
            with self.subTest(data=bad), self.assertRaises(ValueError):
                parse_manifest(bad)

    def test_unsafe_paths_executable_and_duplicate_targets_refused(self):
        data, _ = synthetic_fixture(8766)
        manifest = parse_manifest(data)
        good = manifest.files[0]
        for index, bad_value in ((0, b'..\\other.txt'), (0, b'CON.txt'),
                                 (0, b'app.exe'), (0, b'app.dll'), (0, b'probe.txt '),
                                 (1, b'\\res\\..\\'), (1, b'C:\\res\\'),
                                 (1, b'\\res\\\\'), (1, b'\\Windows\\'),
                                 (3, b'../secret'), (3, b'https://external.invalid/a'),
                                 (3, b'/absolute'), (3, b'a/./b'), (3, b'a//b')):
            fields = list(good.fields)
            fields[index] = bad_value
            with self.subTest(index=index, value=bad_value), self.assertRaises(ValueError):
                FileRecord(tuple(fields)).validate()
        duplicate = replace(good, fields=(b'WCF_PROBE.TXT',) + good.fields[1:])
        with self.assertRaises(ValueError):
            build_manifest(NativeManifest(manifest.entries + (duplicate,)))

    def test_field_count_bools_and_digest_are_conservative(self):
        data, _ = synthetic_fixture(8766)
        good = parse_manifest(data).files[0]
        for fields in (good.fields[:-1], good.fields + (b'extra',),
                       good.fields[:2] + (b'A' * 32,) + good.fields[3:],
                       good.fields[:5] + (b'2',) + good.fields[6:]):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                FileRecord(fields).validate()

    def test_manifest_and_line_budgets(self):
        data, _ = synthetic_fixture(8766)
        for bad in (b'x' * (MAX_MANIFEST + 1),
                    data.replace(b'WCF synthetic probe', b'x' * MAX_LINE)):
            with self.assertRaises(ValueError):
                parse_manifest(bad)
        parsed = parse_manifest(data)
        files = tuple(replace(parsed.files[0], fields=(f'probe_{n}.txt'.encode(),) +
                      parsed.files[0].fields[1:]) for n in range(MAX_FILES + 1))
        with self.assertRaises(ValueError):
            build_manifest(NativeManifest(parsed.entries[:3] + files))

    def test_base_url_is_inspected_offline_and_credentials_rejected(self):
        data, _ = synthetic_fixture(8766)
        # Existing external configuration can be inspected without network access.
        external = data.replace(b'http://127.0.0.1:8766/', b'https://example.invalid/update/')
        self.assertEqual(len(parse_manifest(external).files), 1)
        for url in (b'file:///tmp/', b'http://user:password@example.invalid/',
                    b'http://example.invalid/?x=1', b'http://example.invalid/#x',
                    b'http://example.invalid:99999/', b'http://[bad/',
                    b'http://example.invalid\\other/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_manifest(data.replace(b'http://127.0.0.1:8766/', url))

    def test_corruption_truncation_trailing_gzip_and_zlib_bomb_refused(self):
        data, payload = synthetic_fixture(8766, True)
        record = parse_manifest(data).files[0]
        for bad in (payload[:-1], payload + b'extra', b'garbage', gzip.compress(SYNTHETIC_PAYLOAD),
                    zlib.compress(b'x' * 1025)):
            with self.subTest(payload=bad[:10]), self.assertRaises(ValueError):
                verify_payload(record, bad, limit=1024)
        plain, _ = synthetic_fixture(8766)
        with self.assertRaises(ValueError):
            verify_payload(parse_manifest(plain).files[0], b'corrupted')

    def test_server_is_loopback_only_serves_immutable_synthetic_data(self):
        with make_fixture_server(compressed=True) as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            worker = threading.Thread(target=lambda: [server.handle_request() for _ in range(4)])
            worker.start()
            base = f'http://127.0.0.1:{server.server_port}'
            with urllib.request.urlopen(base + '/manifest?db=fixture&r=fixture&i=1&vs=1204', timeout=3) as response:
                manifest = parse_manifest(response.read())
            with urllib.request.urlopen(base + '/payload', timeout=3) as response:
                self.assertEqual(verify_payload(manifest.files[0], response.read()), SYNTHETIC_PAYLOAD)
            for path in ('/../../private/config.php', '/payload?file=secret'):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + path, timeout=3)
                self.assertEqual(caught.exception.code, 404)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(server.observations, ['manifest', 'payload'])

    def test_inspection_cli_redacts_contents(self):
        data, _ = synthetic_fixture(8766)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.txt'
            path.write_bytes(data)
            result = subprocess.run([sys.executable, '-m', 'tools.native_update_manifest',
                                     'inspect', str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads(result.stdout)
        self.assertTrue(info['contents_redacted'])
        self.assertFalse(info['native_runtime_verified'])
        self.assertEqual(info['sha256'], hashlib.sha256(data).hexdigest())
        self.assertNotIn('127.0.0.1', result.stdout)
        self.assertNotIn('wcf_probe', result.stdout)


if __name__ == '__main__':
    unittest.main()
