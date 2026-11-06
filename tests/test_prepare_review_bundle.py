import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tools.prepare_review_bundle import prepare
from tools.stage_native_additions import open_staging, stage


def fixture():
    fields = [b'TEST \xe9', b'', b'17', b'37', b'123', b'456', b'0', b'0',
              b'', b'', b'', b'', b'0' * 32, b'', b'', b'ligne 1', b'ligne 2']
    row = b'|'.join(fields)
    return b'#v001-id=1-fictif\r\n#1204-ab12\r\n' + row + b'\r\n' + row + b'\r\n', row


class PrepareReviewBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.path = self.base / 'staging.sqlite'
        self.data, self.row = fixture()
        self.digest = hashlib.sha256(self.data).hexdigest()
        with closing(open_staging(self.path)) as db:
            stage(db, self.data)
        self.review = {'format': 'wcf-local-review-v1', 'accepted': [
            {'snapshot_sha256': self.digest, 'ordinal': 2},
            {'snapshot_sha256': self.digest, 'ordinal': 1}]}

    def test_selection_is_deterministic_and_keeps_duplicate_bytes(self):
        with closing(sqlite3.connect(self.path)) as db:
            first = prepare(db, self.review)
            other = prepare(db, dict(self.review, accepted=list(reversed(self.review['accepted']))))
        self.assertEqual(first, other)
        self.assertFalse(first['native_update'])
        self.assertEqual([item['ordinal'] for item in first['records']], [1, 2])
        self.assertEqual([base64.b64decode(item['record_base64']) for item in first['records']], [self.row, self.row])
        self.assertEqual(first['records'][0]['record_sha256'], hashlib.sha256(self.row).hexdigest())

    def test_invalid_selection_and_tampered_database_are_rejected(self):
        with closing(sqlite3.connect(self.path)) as db:
            duplicate = dict(self.review, accepted=[self.review['accepted'][0]] * 2)
            for review in (duplicate, dict(self.review, accepted=[]),
                           dict(self.review, accepted=[{'snapshot_sha256': self.digest, 'ordinal': True}]),
                           dict(self.review, accepted=[{'snapshot_sha256': self.digest, 'ordinal': 3}])):
                with self.subTest(review=review), self.assertRaises(ValueError):
                    prepare(db, review)
            db.execute('UPDATE records SET raw_record=? WHERE ordinal=1', (b'tampered',))
            db.commit()
            with self.assertRaises(ValueError):
                prepare(db, self.review)

    def test_cli_never_overwrites_and_missing_database_not_created(self):
        review = self.base / 'review.json'
        review.write_text(json.dumps(self.review), encoding='utf-8')
        output = self.base / 'bundle.json'
        cmd = [sys.executable, '-m', 'tools.prepare_review_bundle', str(self.path), str(review), str(output)]
        first = subprocess.run(cmd, capture_output=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        original = output.read_bytes()
        self.assertEqual(json.loads(original)['format'], 'wcf-offline-reviewed-bundle-v1')
        second = subprocess.run(cmd, capture_output=True)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(output.read_bytes(), original)
        missing = self.base / 'missing.sqlite'
        cmd[3] = str(missing)
        cmd[-1] = str(self.base / 'new.json')
        self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
