import sqlite3
from contextlib import closing
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.stage_native_additions import open_staging, stage

HEADER = b'#v001-id=1-fictif\r\n#1204-ab12\r\n'
ROW = b'Nom\xe9||17|25|123|456|0|0|||||0|||ligne|suite\r\n'


class StagingTests(unittest.TestCase):
    def test_cli_refuses_repo_destination_and_accepts_local_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'fictif.txt'
            database = Path(directory) / 'local.sqlite'
            source.write_bytes(HEADER + ROW)
            command = [sys.executable, '-m', 'tools.stage_native_additions', str(source)]
            denied = subprocess.run(command + ['blocked.sqlite'], capture_output=True, text=True)
            self.assertNotEqual(denied.returncode, 0)
            self.assertFalse(Path('blocked.sqlite').exists())
            first = subprocess.run(command + [str(database)], capture_output=True, text=True, check=True)
            second = subprocess.run(command + [str(database)], capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(first.stdout)['inserted'])
            self.assertFalse(json.loads(second.stdout)['inserted'])

    def test_exact_bytes_order_duplicates_and_idempotence(self):
        data = HEADER + ROW + ROW
        with tempfile.TemporaryDirectory() as directory:
            with closing(open_staging(Path(directory) / 'stage.sqlite')) as db:
                self.assertEqual(stage(db, data), {'inserted': True, 'record_count': 2})
                self.assertFalse(stage(db, data)['inserted'])
                self.assertEqual(db.execute('SELECT raw_file FROM snapshots').fetchone()[0], data)
                rows = db.execute('SELECT ordinal, raw_record FROM records ORDER BY ordinal').fetchall()
                self.assertEqual(rows, [(1, ROW[:-2]), (2, ROW[:-2])])
                self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)

    def test_existing_unrelated_database_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'other.sqlite'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE important(value TEXT)')
                db.execute("INSERT INTO important VALUES ('kept')")
                db.commit()
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                open_staging(path)
            self.assertEqual(path.read_bytes(), before)

    def test_invalid_data_never_changes_existing_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            db = open_staging(Path(directory) / 'stage.sqlite')
            try:
                with self.assertRaises(ValueError):
                    stage(db, HEADER + b'truncated|record')
                self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 0)
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
