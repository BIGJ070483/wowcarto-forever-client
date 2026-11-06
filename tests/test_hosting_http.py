"""Real PHP HTTP tests on a disposable SQLite fixture; not Free/MySQL proof."""
from contextlib import closing
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PHP = os.environ.get('WCF_PHP_BIN') or shutil.which('php')
if PHP:
    probe = [PHP]
    if os.environ.get('WCF_PHP_EXT_DIR'):
        probe += ['-d', 'extension_dir=' + os.environ['WCF_PHP_EXT_DIR'], '-d', 'extension=pdo_sqlite']
    probe += ['-r', 'exit(PHP_VERSION_ID >= 80100 && class_exists("PDO") && in_array("sqlite",PDO::getAvailableDrivers(),true) ? 0 : 1);']
    ready = subprocess.run(probe, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0).returncode == 0
    if not ready:
        if os.environ.get('WCF_PHP_BIN') or os.environ.get('WCF_REQUIRE_PHP_TESTS'):
            raise RuntimeError('Configured PHP requires PHP 8.1+ and pdo_sqlite')
        PHP = None
if not PHP and os.environ.get('WCF_REQUIRE_PHP_TESTS'):
    raise RuntimeError('PHP tests are required in this job, but PHP is unavailable')
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE wcf_schema_versions(version INTEGER PRIMARY KEY);
INSERT INTO wcf_schema_versions VALUES(1),(2);
CREATE TABLE wcf_members(id INTEGER PRIMARY KEY AUTOINCREMENT,
 username TEXT COLLATE NOCASE NOT NULL UNIQUE,password_hash BLOB NOT NULL,
 role TEXT NOT NULL DEFAULT 'member',enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE wcf_imports(id INTEGER PRIMARY KEY AUTOINCREMENT,
 member_id INTEGER NOT NULL REFERENCES wcf_members(id), sha256 BLOB NOT NULL,
 raw_file BLOB NOT NULL,record_count INTEGER NOT NULL,native_format TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(member_id,sha256));
CREATE TABLE wcf_annotations(id INTEGER PRIMARY KEY AUTOINCREMENT,
 import_id INTEGER NOT NULL REFERENCES wcf_imports(id),ordinal INTEGER NOT NULL,
 raw_record BLOB NOT NULL, review_state TEXT DEFAULT 'pending',UNIQUE(import_id,ordinal));
CREATE TABLE wcf_moderation_events(id INTEGER PRIMARY KEY AUTOINCREMENT,
 annotation_id INTEGER NOT NULL REFERENCES wcf_annotations(id),
 moderator_id INTEGER NOT NULL REFERENCES wcf_members(id),
 decision TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE wcf_rate_limits(bucket TEXT PRIMARY KEY,hits INTEGER,expires_at INTEGER);
'''


def native_fixture():
    fields = [b'TEST \xe9', b'', b'17', b'37', b'123', b'456', b'0', b'0',
              b'', b'', b'', b'', b'0' * 32, b'', b'', b'commentaire', b'', b'fin']
    row = b'|'.join(fields)
    return b'#v001-id=1-fixture\r\n#1204-ab12\r\n' + row + b'\r\n' + row + b'\r\n', row


@unittest.skipUnless(PHP, 'Set WCF_PHP_BIN to a PHP CLI with pdo_sqlite')
class HostingHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wcf-php-test-')
        cls.base = Path(cls.temp.name)
        cls.web = cls.base / 'web'
        shutil.copytree(ROOT / 'hosting' / 'web', cls.web)
        cls.database = cls.base / 'fixture.sqlite'
        cls.config = cls.web / 'private' / 'config.php'
        cls.write_config(True)
        router = cls.base / 'router.php'
        router.write_text("<?php $p=rawurldecode(parse_url($_SERVER['REQUEST_URI'],PHP_URL_PATH)); if(str_contains($p,'/private/') || str_contains($p,'/.')) {http_response_code(403);echo 'Forbidden';return true;}return false;", encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.url = f'http://127.0.0.1:{cls.port}/'
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        cls.log = (cls.base / 'server.log').open('w+', encoding='utf-8')
        command = [PHP]
        if os.environ.get('WCF_PHP_EXT_DIR'):
            command += ['-d', 'extension_dir=' + os.environ['WCF_PHP_EXT_DIR'], '-d', 'extension=pdo_sqlite']
        command += ['-d', 'display_errors=0', '-d', 'opcache.enable=0',
                    '-S', f'127.0.0.1:{cls.port}', '-t', str(cls.web), str(router)]
        cls.process = subprocess.Popen(command, stdout=cls.log, stderr=cls.log, creationflags=flags)
        for _ in range(100):
            if cls.process.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError(cls.log.read())
            try:
                with urllib.request.urlopen(cls.url, timeout=.2):
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(.05)
        else:
            raise RuntimeError('PHP HTTP server did not start')

    @classmethod
    def write_config(cls, opened):
        # JSON string literals without escaped Unicode are valid PHP strings here.
        cls.config.write_text("<?php return ['dsn'=>" + json.dumps('sqlite:' + cls.database.as_posix()) +
                              ",'username'=>'','password'=>'','public_open'=>" + ('true' if opened else 'false') +
                              ",'registration_open'=>true,'require_https'=>false,'rate_secret'=>'" + 'a' * 64 +
                              "','max_upload_bytes'=>1048576,'max_records'=>2000];", encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.log.close()
        cls.temp.cleanup()

    def setUp(self):
        self.write_config(True)
        if self.database.exists():
            self.database.unlink()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript(SCHEMA)
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = self.call('bootstrap')[1]['csrf']

    def call(self, action, fields=None, upload=None, csrf=True, method=None):
        headers = {}
        body = None
        if fields is not None or upload is not None:
            fields = dict(fields or {})
            if csrf:
                fields['csrf'] = self.csrf
            if upload is None:
                body = urllib.parse.urlencode(fields).encode()
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            else:
                boundary = 'WCFtestBoundary42'
                parts = []
                for key, value in fields.items():
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.txt"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + upload + b'\r\n')
                parts.append(f'--{boundary}--\r\n'.encode())
                body = b''.join(parts)
                headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        request = urllib.request.Request(self.url + 'api.php?action=' + action, data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = json.load(response)
            if 'csrf' in result:
                self.csrf = result['csrf']
            return response.status, result

    def register(self, username='fixture_user'):
        return self.call('register', {'username': username, 'password': 'Test-only-password-42'})

    def counts(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            return [db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                    for table in ('wcf_members', 'wcf_imports', 'wcf_annotations')]

    def test_closed_service_health_and_no_writes(self):
        self.write_config(False)
        self.assertEqual(self.call('health')[1], {'ok': True, 'schema': 2, 'open': False})
        self.assertEqual(self.register()[0], 503)
        self.assertEqual(self.call('upload', upload=native_fixture()[0])[0], 503)
        self.assertEqual(self.call('review_queue')[0], 503)
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 503)
        self.assertEqual(self.call('review_export', {'after': '0'})[0], 503)
        self.assertEqual(self.counts(), [0, 0, 0])

    def test_csrf_method_and_anonymous_rejections(self):
        self.assertEqual(self.register()[0], 200)
        self.assertEqual(self.call('logout', {}, csrf=False)[0], 403)
        self.assertEqual(self.call('logout', {})[0], 200)
        self.assertEqual(self.call('upload', upload=native_fixture()[0])[0], 401)
        self.assertEqual(self.call('imports')[0], 401)
        self.assertEqual(self.call('register', method='GET')[0], 405)

    def test_login_logout_disabled_and_password_storage(self):
        old_csrf = self.csrf
        self.assertEqual(self.register()[0], 200)
        self.assertNotEqual(old_csrf, self.csrf)
        with closing(sqlite3.connect(self.database)) as db, db:
            stored = db.execute('SELECT password_hash,role FROM wcf_members').fetchone()
        self.assertTrue(stored[0].startswith('$2y$12$'))
        self.assertEqual(stored[1], 'member')
        self.call('logout', {})
        self.assertEqual(self.call('login', {'username': 'fixture_user', 'password': 'Wrong-password-42'})[0], 401)
        self.assertEqual(self.call('login', {'username': 'fixture_user', 'password': 'Test-only-password-42'})[0], 200)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE wcf_members SET enabled=0')
        self.assertEqual(self.call('imports')[0], 401)

    def test_disabled_session_can_recover_without_access_or_stale_csrf(self):
        self.register()
        old_csrf = self.csrf
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE wcf_members SET enabled=0')
        code, body = self.call('bootstrap')
        self.assertEqual(code, 200)
        self.assertIsNone(body['member'])
        self.assertNotEqual(old_csrf, body['csrf'])
        self.assertEqual(self.call('imports')[0], 401)
        self.assertEqual(self.call('logout', {})[0], 200)
        self.assertEqual(self.call('login', {'username': 'fixture_user', 'password': 'Test-only-password-42'})[0], 401)

    def test_registration_closed_while_existing_members_can_login(self):
        self.register()
        self.call('logout', {})
        current = self.config.read_text(encoding='utf-8')
        self.config.write_text(current.replace("'registration_open'=>true", "'registration_open'=>false"), encoding='utf-8')
        self.assertEqual(self.register('another_user')[0], 403)
        self.assertEqual(self.call('login', {'username': 'fixture_user', 'password': 'Test-only-password-42'})[0], 200)

    def test_fail_closed_on_string_configuration_flags_and_https(self):
        current = self.config.read_text(encoding='utf-8')
        self.config.write_text(current.replace("'public_open'=>true", "'public_open'=>'false'"), encoding='utf-8')
        self.assertEqual(self.call('health')[0], 503)
        self.assertEqual(self.register()[0], 503)
        self.config.write_text(current.replace("'require_https'=>false", "'require_https'=>true"), encoding='utf-8')
        self.assertEqual(self.call('health')[0], 403)
        # A caller-supplied forwarding header cannot create a trusted HTTPS origin.
        request = urllib.request.Request(self.url + 'api.php?action=health', headers={'X-Forwarded-Proto': 'https'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(request)
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_session_idle_and_absolute_expiry_require_new_login(self):
        import re
        for key, age in [('seen', 1801), ('started', 28801)]:
            self.assertEqual(self.register('expiry_' + key)[0], 200)
            old_csrf = self.csrf
            # Edit only the disposable server's stored session to simulate elapsed time.
            session_id = next(c.value for c in self.cookies if c.name == 'WCFSESSION')
            active = self.web / 'private' / 'sessions' / ('sess_' + session_id)
            data = active.read_bytes()
            data, count = re.subn(key.encode() + rb'\|i:[0-9]+;', key.encode() + b'|i:' + str(int(time.time()) - age).encode() + b';', data)
            self.assertEqual(count, 1)
            active.write_bytes(data)
            code, body = self.call('bootstrap')
            self.assertEqual(code, 200)
            self.assertIsNone(body['member'])
            self.assertNotEqual(old_csrf, body['csrf'])
            self.assertEqual(self.call('imports')[0], 401)

    def test_idempotent_retry_at_quota_and_revoked_reviewer(self):
        self.register()
        data, row = native_fixture()
        self.assertEqual(self.call('upload', upload=data)[0], 201)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.executemany('INSERT INTO wcf_imports(member_id,sha256,raw_file,record_count,native_format) VALUES (1,?,?,1,?)',
                           [(hashlib.sha256(str(n).encode()).digest(), b'fixture', 'v001') for n in range(99)])
        self.assertEqual(self.call('upload', upload=data)[0], 200)
        self.assertEqual(self.call('upload', upload=data.replace(b'TEST', b'NEW'))[0], 413)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='moderator'")
        self.assertEqual(self.call('review_queue')[0], 200)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='member'")
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 403)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wcf_moderation_events').fetchone()[0], 0)

    def test_file_bytes_duplicate_rows_idempotency_and_ownership(self):
        self.register()
        data, row = native_fixture()
        code, first = self.call('upload', upload=data)
        self.assertEqual(code, 201)
        self.assertEqual(first['import']['records'], 2)
        self.assertTrue(first['import']['inserted'])
        code, second = self.call('upload', upload=data)
        self.assertEqual(code, 200)
        self.assertFalse(second['import']['inserted'])
        self.assertEqual(first['import']['id'], second['import']['id'])
        with closing(sqlite3.connect(self.database)) as db, db:
            db.text_factory = bytes
            self.assertEqual(db.execute('SELECT raw_file FROM wcf_imports').fetchone()[0], data)
            self.assertEqual(db.execute('SELECT sha256 FROM wcf_imports').fetchone()[0], hashlib.sha256(data).digest())
            self.assertEqual(db.execute('SELECT raw_record,ordinal,review_state FROM wcf_annotations ORDER BY ordinal').fetchall(), [(row, 1, b'pending'), (row, 2, b'pending')])
        self.call('logout', {})
        self.register('second_user')
        self.assertEqual(self.call('imports')[1]['imports'], [])
        self.assertEqual(self.call('upload', upload=data)[0], 201)
        self.assertEqual(self.counts(), [2, 2, 4])

    def test_invalid_files_and_size_leave_no_import(self):
        self.register()
        data, _ = native_fixture()
        for bad in (b'<?php echo 1;', data.replace(b'v001', b'v002'), data + b'\x00', data + b'\n'):
            self.assertEqual(self.call('upload', upload=bad)[0], 400)
        self.assertEqual(self.call('upload', upload=b'x' * 1048577)[0], 400)
        self.assertEqual(self.counts(), [1, 0, 0])

    def test_injected_record_failure_rolls_back_entire_import(self):
        self.register()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON wcf_annotations WHEN NEW.ordinal=2 BEGIN SELECT RAISE(ABORT,'injected fixture failure'); END")
        code, result = self.call('upload', upload=native_fixture()[0])
        self.assertEqual(code, 503)
        self.assertNotIn('injected', result['error'])
        self.assertEqual(self.counts(), [1, 0, 0])

    def test_review_permissions_atomic_decisions_and_stale_conflict(self):
        self.register()
        data, row = native_fixture()
        self.assertEqual(self.call('upload', upload=data)[0], 201)
        self.assertEqual(self.call('review_queue')[0], 403)
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 403)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='moderator'")
        code, queue = self.call('review_queue')
        self.assertEqual(code, 200)
        import base64
        self.assertEqual([base64.b64decode(item['record_base64']) for item in queue['items']], [row, row])
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'}, csrf=False)[0], 403)
        self.assertEqual(self.call('review_decide', {'id': '2', 'decision': 'rejected', 'reason': ''})[0], 400)
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 200)
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'rejected', 'reason': 'autre'})[0], 409)
        self.assertEqual(self.call('review_decide', {'id': '2', 'decision': 'rejected', 'reason': 'inexact'})[0], 200)
        self.assertEqual(self.call('review_queue')[1]['items'], [])
        history = self.call('imports')[1]['imports']
        self.assertEqual(history[0]['review'], {'pending': 0, 'accepted': 1, 'rejected': 1})
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT review_state FROM wcf_annotations ORDER BY id').fetchall(), [('accepted',), ('rejected',)])
            self.assertEqual(db.execute('SELECT annotation_id,moderator_id,decision,reason FROM wcf_moderation_events ORDER BY id').fetchall(), [(1, 1, 'accepted', ''), (2, 1, 'rejected', 'inexact')])

    def test_review_queue_paginates_and_preserves_order(self):
        self.register()
        data, row = native_fixture()
        self.call('upload', upload=data)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='moderator'")
            db.executemany('INSERT INTO wcf_annotations(import_id,ordinal,raw_record) VALUES (1,?,?)', [(n, row) for n in range(3, 9)])
        code, first = self.call('review_queue')
        self.assertEqual(code, 200)
        self.assertEqual([item['id'] for item in first['items']], [1, 2, 3, 4, 5])
        self.assertTrue(first['has_more'])
        code, second = self.call('review_queue&after=5')
        self.assertEqual(code, 200)
        self.assertEqual([item['id'] for item in second['items']], [6, 7, 8])
        self.assertFalse(second['has_more'])
        self.assertEqual(self.call('review_queue&after=-1')[0], 400)

    def test_closing_service_blocks_existing_reviewer(self):
        self.register()
        self.call('upload', upload=native_fixture()[0])
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='admin'")
        self.assertEqual(self.call('review_queue')[0], 200)
        self.write_config(False)
        self.assertEqual(self.call('review_queue')[0], 503)
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 503)

    def test_review_event_failure_rolls_back(self):
        self.register()
        self.call('upload', upload=native_fixture()[0])
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='admin'")
            db.execute("CREATE TRIGGER fail_review BEFORE INSERT ON wcf_moderation_events BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 503)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT review_state FROM wcf_annotations WHERE id=1').fetchone()[0], 'pending')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wcf_moderation_events').fetchone()[0], 0)

    def test_private_review_export_checks_rights_bytes_provenance_and_integrity(self):
        import base64
        self.register()
        data, row = native_fixture()
        self.call('upload', upload=data)
        self.assertEqual(self.call('review_export', {'after': '0'})[0], 403)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='moderator'")
        self.assertEqual(self.call('review_export', {'after': '0'}, csrf=False)[0], 403)
        self.assertEqual(self.call('review_export', method='GET')[0], 405)
        self.assertEqual(self.call('review_export', {'after': '-1'})[0], 400)
        self.assertEqual(self.call('review_export', {'after': '0'})[1]['records'], [])
        self.assertEqual(self.call('review_decide', {'id': '1', 'decision': 'accepted'})[0], 200)
        self.assertEqual(self.call('review_decide', {'id': '2', 'decision': 'rejected', 'reason': 'fixture'})[0], 200)
        code, package = self.call('review_export', {'after': '0'})
        self.assertEqual(code, 200)
        self.assertFalse(package['native_update'])
        self.assertFalse(package['has_more'])
        self.assertEqual(package['next_after'], 1)
        record, = package['records']
        self.assertEqual(base64.b64decode(record['raw_base64']), row)
        self.assertEqual(record['source_sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(record['sha256'], hashlib.sha256(row).hexdigest())
        self.assertEqual((record['ordinal'], record['event_id']), (1, 1))
        self.assertNotIn('#v001', json.dumps(package))
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_imports SET raw_file=? WHERE id=1", [b'corrupted source'])
        self.assertEqual(self.call('review_export', {'after': '0'})[0], 503)

    def test_private_review_export_pages_duplicate_rows_without_loss(self):
        self.register()
        data, row = native_fixture()
        data = b'\r\n'.join(data.splitlines()[:2] + [row] * 23) + b'\r\n'
        self.call('upload', upload=data)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE wcf_members SET role='admin'")
            db.execute("UPDATE wcf_annotations SET review_state='accepted'")
            db.execute("INSERT INTO wcf_moderation_events(annotation_id,moderator_id,decision,reason) SELECT id,1,'accepted','fixture' FROM wcf_annotations")
        first = self.call('review_export', {'after': '0'})[1]
        self.assertEqual(len(first['records']), 20)
        self.assertTrue(first['has_more'])
        second = self.call('review_export', {'after': str(first['next_after'])})[1]
        self.assertEqual(len(second['records']), 3)
        self.assertFalse(second['has_more'])
        self.assertEqual([r['ordinal'] for r in first['records'] + second['records']], list(range(1, 24)))
        self.assertEqual(len({r['sha256'] for r in first['records'] + second['records']}), 1)

    def test_login_rate_limit_and_no_password_echo(self):
        for _ in range(10):
            self.assertEqual(self.call('login', {'username': 'missing_user', 'password': 'Wrong-password-42'})[0], 401)
        code, data = self.call('login', {'username': 'missing_user', 'password': 'Wrong-password-42'})
        self.assertEqual(code, 429)
        self.assertNotIn('Wrong-password', json.dumps(data))


if __name__ == '__main__':
    unittest.main()
