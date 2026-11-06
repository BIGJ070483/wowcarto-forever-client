"""Synthetic first-admin preparation; no real account or credential."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.prepare_first_admin import admin_sql, prepare

HASH = b'$2y$12$' + b'a' * 53


class FirstAdminTests(unittest.TestCase):
    def test_private_sql_has_guard_no_password_or_default_account(self):
        sql = admin_sql('fixture_admin', HASH)
        self.assertIn("GET_LOCK('wcf_first_admin'", sql)
        self.assertIn('START TRANSACTION', sql)
        self.assertIn("role='admin' OR username='fixture_admin'", sql)
        self.assertIn('accounts_created', sql)
        self.assertIn(HASH.hex(), sql)
        for bad in ("bad'user", '../admin', 'ab', 'é_admin'):
            with self.assertRaises(ValueError):
                admin_sql(bad, HASH)
        with self.assertRaises(ValueError):
            admin_sql('valid_admin', b'not-a-hash')

    def test_no_overwrite_and_password_only_on_stdin(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'private.sql'
            with patch('tools.prepare_first_admin.subprocess.run') as run:
                run.return_value.stdout = HASH
                prepare('php', 'fixture_admin', target, 'Fixture-password-42')
                args, kwargs = run.call_args
                self.assertNotIn('Fixture-password-42', repr(args))
                self.assertEqual(kwargs['input'], b'Fixture-password-42')
                self.assertNotIn('Fixture-password-42', target.read_text())
                with self.assertRaises(ValueError):
                    prepare('php', 'fixture_admin', target, 'Fixture-password-42')
                self.assertEqual(run.call_count, 1)

    def test_refuses_repo_output_and_bcrypt_truncation(self):
        with self.assertRaises(ValueError):
            prepare('php', 'fixture_admin', Path(__file__).with_suffix('.sql'), 'Fixture-password-42')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                prepare('php', 'fixture_admin', Path(temp) / 'private.sql', 'é' * 37)
