"""Installation protection and retirement with synthetic local files."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zlib

from tools.local_install import archive, prepare, verify, EXE, OFFLINE_CONFIGS
from tools.inspect_native_config import inspect, KEY


class LocalInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.overlay, self.target, self.backup = [self.root / n for n in ('source', 'overlay', 'target', 'backup')]
        self.source.mkdir(); self.overlay.mkdir()
        (self.source / EXE).write_bytes(b'synthetic-not-executable')
        self.hash = hashlib.sha256((self.source / EXE).read_bytes()).hexdigest()
        for base, content in ((self.source, b'installed-private'), (self.overlay, b'personal\xe9')):
            (base / 'ajouts').mkdir()
            (base / 'ajouts' / 'Wow_ajouts_Francais.txt').write_bytes(content)

    def install(self):
        return prepare(self.source, self.target, self.backup, self.overlay, expected_hash=self.hash)

    def test_effective_virtualstore_bytes_and_both_originals_backed_up(self):
        result = self.install()
        name = 'ajouts/Wow_ajouts_Francais.txt'
        self.assertEqual((self.target / 'client' / name).read_bytes(), b'personal\xe9')
        self.assertEqual((self.backup / 'installation' / name).read_bytes(), b'installed-private')
        self.assertEqual((self.backup / 'virtualstore' / name).read_bytes(), b'personal\xe9')
        self.assertFalse(result['auto_launched'])
        self.assertEqual(verify(self.target), {'missing': 0, 'added': 0, 'changed': 0, 'native_update_verified': False})

    def test_wrong_executable_existing_destination_and_nesting_refused(self):
        with self.assertRaises(ValueError):
            prepare(self.source, self.target, self.backup, self.overlay)
        self.target.mkdir()
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(self.backup.exists())
        with self.assertRaises(ValueError):
            prepare(self.source, self.source / 'nested', self.backup, expected_hash=self.hash)

    def test_executable_overlay_and_existing_backup_are_never_overwritten(self):
        (self.overlay / EXE).write_bytes(b'wrong executable')
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(self.backup.exists())
        (self.overlay / EXE).unlink()
        self.backup.mkdir()
        marker = self.backup / 'keep.txt'; marker.write_bytes(b'keep')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_retirement_preserves_user_edits_and_entire_directory(self):
        self.install()
        modified = self.target / 'client' / 'ajouts' / 'Wow_ajouts_Francais.txt'
        modified.write_bytes(b'new user observations')
        self.assertEqual(verify(self.target)['changed'], 1)
        destination = self.root / 'retired'
        result = archive(self.target, destination)
        self.assertFalse(self.target.exists())
        self.assertEqual((destination / 'client' / modified.relative_to(self.target / 'client')).read_bytes(), b'new user observations')
        self.assertEqual(result['deleted_native_files'], 0)

    def test_symlink_source_refused_when_host_allows_symlink(self):
        try:
            (self.overlay / 'link').symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not allow creating fixture symlinks')
        with self.assertRaises(ValueError): self.install()

    def test_offline_copy_preserves_source_and_backup_and_redirects_both_configs(self):
        extra = 'database/fixture-custom/url.bin'
        for relative in (*OFFLINE_CONFIGS, extra):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'original-url-data')
        result = prepare(self.source, self.target, self.backup, self.overlay, expected_hash=self.hash, offline=True)
        self.assertTrue(result['offline_endpoints'])
        self.assertFalse(result['network_isolation_verified'])
        for relative in (*OFFLINE_CONFIGS, extra):
            self.assertEqual((self.source / relative).read_bytes(), b'original-url-data')
            self.assertEqual((self.backup / 'installation' / relative).read_bytes(), b'original-url-data')
            configured = (self.target / 'client' / relative).read_bytes()
            self.assertEqual(inspect(configured)['url_schemes_at_lines_3_6_7'], ['http'] * 3)
            encoded = zlib.decompress(configured)
            lines = bytes(value ^ KEY[i % len(KEY)] for i, value in enumerate(encoded)).splitlines()
            self.assertEqual((lines[2], lines[5], lines[6]), (b'http://127.0.0.1:9/', b'http://127.0.0.1:9/upload', b'http://127.0.0.1:9/'))
        self.assertEqual(verify(self.target)['changed'], 0)

    def test_offline_copy_refuses_missing_configs_before_creating_backup(self):
        with self.assertRaises(ValueError):
            prepare(self.source, self.target, self.backup, self.overlay, expected_hash=self.hash, offline=True)
        self.assertFalse(self.backup.exists())
