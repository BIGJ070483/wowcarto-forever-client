"""Safety checks on the Windows launcher's local installation boundary."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

try:
    from tools import windows_app
except ImportError:
    windows_app = None


@unittest.skipUnless(windows_app, 'Tkinter is required')
class WindowsAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / 'target'
        self.client = self.target / 'client'
        self.client.mkdir(parents=True)
        self.exe = self.client / 'WowCartographe.exe'
        self.exe.write_bytes(b'not-a-real-executable')
        self.digest = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        (self.target / 'install-manifest.json').write_text(json.dumps({'format':'wcf-local-install-v1','offline_endpoints':True}))
        for lang in ('Francais', 'US'):
            path = self.client / 'database' / lang / 'url.bin'
            path.parent.mkdir(parents=True)
            path.write_bytes(windows_app.offline_config())

    def test_unmodified_client_and_all_configurations_required(self):
        with mock.patch.object(windows_app, 'EXPECTED_SHA256', self.digest):
            self.assertEqual(windows_app.installed_client(self.target), self.exe.resolve())
            extra = self.client / 'database' / 'other' / 'url.bin'
            extra.parent.mkdir()
            extra.write_bytes(b'unknown configuration')
            with self.assertRaises(ValueError):
                windows_app.installed_client(self.target)
            extra.write_bytes(windows_app.offline_config())
            self.exe.write_bytes(b'modified')
            with self.assertRaises(ValueError):
                windows_app.installed_client(self.target)

    def test_launch_refuses_when_firewall_is_not_verified(self):
        fake_window = mock.Mock()
        fake_window.target = self.target
        with mock.patch.object(windows_app, 'installed_client', return_value=self.exe), \
             mock.patch.object(windows_app, 'firewall_blocked', return_value=False), \
             mock.patch.object(windows_app.subprocess, 'Popen') as start:
            windows_app.Window.launch(fake_window)
            start.assert_not_called()
            self.assertIn('non vérifié', fake_window.status.set.call_args.args[0])

    def test_launch_uses_verified_client_path(self):
        fake_window = mock.Mock()
        fake_window.target = self.target
        with mock.patch.object(windows_app, 'installed_client', return_value=self.exe), \
             mock.patch.object(windows_app, 'firewall_blocked', return_value=True), \
             mock.patch.object(windows_app.subprocess, 'Popen') as start:
            windows_app.Window.launch(fake_window)
            start.assert_called_once_with([str(self.exe)], cwd=str(self.exe.parent))

    def test_install_launcher_preserves_other_versions_and_creates_shortcuts(self):
        source = Path(self.tmp.name) / 'setup.exe'
        source.write_bytes(b'synthetic-launcher')
        destination = Path(self.tmp.name) / 'installed'
        shortcuts = mock.Mock()
        installed = windows_app.install_launcher(source, destination, shortcuts)
        self.assertEqual(installed.read_bytes(), source.read_bytes())
        shortcuts.assert_called_once_with(installed)
        installed.write_bytes(b'other-version')
        with self.assertRaises(ValueError):
            windows_app.install_launcher(source, destination, shortcuts)
        self.assertEqual(installed.read_bytes(), b'other-version')

    def test_installed_launcher_keeps_state_next_to_it_across_environment_changes(self):
        root = Path(self.tmp.name)
        installed = root / 'redirected' / 'WoWCartoForever' / 'WoWCartoForever.exe'
        self.assertEqual(windows_app.application_dir(True, installed, root / 'other-local'), installed.parent)
        setup = root / 'WoWCartoForever-Setup.exe'
        self.assertEqual(windows_app.application_dir(True, setup, root / 'other-local'), root / 'other-local' / 'WoWCartoForever')

    def test_known_launcher_upgrade_preserves_previous_version(self):
        source = Path(self.tmp.name) / 'new.exe'
        source.write_bytes(b'new-launcher')
        destination = Path(self.tmp.name) / 'installed'
        destination.mkdir()
        old = destination / 'WoWCartoForever.exe'
        old.write_bytes(b'old-launcher')
        old_hash = hashlib.sha256(old.read_bytes()).hexdigest()
        with mock.patch.object(windows_app, 'UPGRADEABLE_LAUNCHERS', frozenset({old_hash})):
            windows_app.install_launcher(source, destination, mock.Mock())
        self.assertEqual(old.read_bytes(), b'new-launcher')
        self.assertEqual((destination / ('WoWCartoForever-sauvegarde-' + old_hash[:16] + '.exe')).read_bytes(), b'old-launcher')

    def test_alternate_source_never_imports_default_virtualstore(self):
        root = Path(self.tmp.name)
        default = root / 'default'
        alternate = root / 'alternate'
        default.mkdir(); alternate.mkdir()
        overlay = root / 'local' / 'VirtualStore' / 'Program Files (x86)' / 'WoWCartographe'
        overlay.mkdir(parents=True)
        self.assertEqual(windows_app.source_overlay(default, default, root / 'local'), overlay)
        self.assertIsNone(windows_app.source_overlay(alternate, default, root / 'local'))


if __name__ == '__main__':
    unittest.main()
