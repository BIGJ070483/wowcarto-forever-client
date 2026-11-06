from contextlib import closing
import base64
import sqlite3
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.error import HTTPError

from tests import test_hosting_http as http
from tests.test_community_transport import openssl_path
from tools.clone_community import CommunityClient
from tools.clone_contributions import submit_additions, submit_correction, submit_correction_fields, corrected_record, personal_addition
from tools.clone_community import decode_snapshot
from tests.test_clone_community import fixture, snapshot
from tools.clone_reference_community import SOURCES, submit_reference, ReferenceCommunityClient


class ContributionValidationTests(unittest.TestCase):
    def test_personal_note_roundtrip_and_no_silent_loss(self):
        note = dict(source='personal', source_ref='personal:reference120:Francais:abc',
                    name='Mon repère', comment='Première ligne\nDeuxième ligne', map_id=17,
                    category=37, x=25711, y=35957, min_level=1, max_level=20, tags='', flags='')
        data = personal_addition(note)
        from tools.native_additions import parse_additions
        raw = b'|'.join(parse_additions(data).records[0].fields)
        item = fixture(); item['raw_base64'] = base64.b64encode(raw).decode('ascii')
        decoded = decode_snapshot(snapshot(item))[0]
        for key in ('name', 'comment', 'map_id', 'category', 'x', 'y', 'min_level', 'max_level'):
            self.assertEqual(decoded[key], note[key])
        for invalid in (dict(note, tags='important'), dict(note, flags='unknown'),
                        dict(note, source_ref='native:reference120:Francais:1'),
                        dict(note, comment='incompatible | caractere')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                personal_addition(invalid)

    def test_full_correction_preserves_location_and_opaque_fields(self):
        row = decode_snapshot(snapshot(fixture()))[0]
        old = base64.b64decode(row['raw_base64']).split(b'|')
        updated = corrected_record(row, {'name': 'Corrigé', 'category': 20, 'min_level': 10,
                                         'max_level': 45, 'comment': 'Première ligne\nDeuxième ligne'}).split(b'|')
        for index in (1, 2, 4, 5, 8, 9, 10, 11, 12, 13, 14):
            self.assertEqual(old[index], updated[index])
        self.assertEqual(updated[0].decode('cp1252'), 'Corrigé')
        self.assertEqual(updated[15:], [b'Premi\xe8re ligne', b'Deuxi\xe8me ligne'])
        for changes in ({'x': 12}, {'name': 'A|B'}, {'comment': '😀'},
                        {'min_level': 51, 'max_level': 50}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                corrected_record(row, changes)

    def test_requires_https_and_a_native_file(self):
        data, _ = http.native_fixture()
        with self.assertRaises(ValueError):
            submit_additions(data, 'http://example.com/api.php')
        with self.assertRaises(ValueError):
            submit_additions(b'not a native file')

    def test_correction_rejects_noncommunity_and_invalid_names_before_network(self):
        with self.assertRaises(ValueError):
            submit_correction({'source': 'historical'}, 'Nom')
        row = {'source': 'community', 'id': 'community:1', 'revision': 1, 'raw_base64': ''}
        for name in ('', 'Nom|autre', 'ligne\nsuivante', 'emoji 😀'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                submit_correction(row, name)


@unittest.skipUnless(http.PHP, 'PHP with pdo_sqlite required')
class ContributionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        http.HostingHttpTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        http.HostingHttpTests.tearDownClass()

    def setUp(self):
        self.h = http.HostingHttpTests('test_closed_service_health_and_no_writes')
        self.h.setUp()
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.executescript('''
            INSERT INTO wcf_schema_versions VALUES (3);
            CREATE TABLE wcf_community_imports(id INTEGER PRIMARY KEY,sha256 BLOB UNIQUE,raw_file BLOB,record_count INTEGER);
            CREATE TABLE wcf_community_annotations(id INTEGER PRIMARY KEY,import_id INTEGER,ordinal INTEGER,revision INTEGER,raw_record BLOB,UNIQUE(import_id,ordinal));
            CREATE TABLE wcf_community_revisions(id INTEGER PRIMARY KEY,annotation_id INTEGER,revision INTEGER,raw_record BLOB,UNIQUE(annotation_id,revision));
            INSERT INTO wcf_schema_versions VALUES (4);
            CREATE TABLE wcf_reference_generation(id INTEGER PRIMARY KEY,generation INTEGER NOT NULL);
            INSERT INTO wcf_reference_generation VALUES (1,0);
            CREATE TABLE wcf_reference_edits(locale TEXT,native_id INTEGER,revision INTEGER,name TEXT,PRIMARY KEY(locale,native_id));
            CREATE TABLE wcf_reference_history(locale TEXT,native_id INTEGER,revision INTEGER,name TEXT,PRIMARY KEY(locale,native_id,revision));
            INSERT INTO wcf_schema_versions VALUES (5);
            CREATE TABLE wcf_community_generation(id INTEGER PRIMARY KEY,generation INTEGER NOT NULL);
            INSERT INTO wcf_community_generation VALUES (1,0);
            ''')

    def test_real_submission_is_idempotent_and_closed_service_refuses(self):
        data, _ = http.native_fixture()
        endpoint = self.h.url + 'api.php?action=community_snapshot'
        first = submit_additions(data, endpoint, _test_loopback=True)
        self.assertTrue(first['inserted'])
        self.assertEqual(first['records'], 2)
        self.assertFalse(submit_additions(data, endpoint, _test_loopback=True)['inserted'])
        self.assertEqual(len(self.h.call('community_snapshot')[1]['snapshot']['items']), 2)
        self.h.write_config(False)
        with self.assertRaisesRegex(ValueError, 'fermé'):
            submit_additions(data, endpoint, _test_loopback=True)

    def test_personal_note_submission_and_reception(self):
        note = dict(source='personal', source_ref='personal:reference120:Francais:test', name='Note personnelle',
                    comment='Essai\nDeuxieme ligne', map_id=17, category=37, x=25711, y=35957,
                    min_level=5, max_level=20, tags='', flags='')
        endpoint = self.h.url + 'api.php?action=community_snapshot'
        data = personal_addition(note)
        self.assertTrue(submit_additions(data, endpoint, _test_loopback=True)['inserted'])
        self.assertFalse(submit_additions(data, endpoint, _test_loopback=True)['inserted'])
        rows = decode_snapshot(self.h.call('community_snapshot')[1]['snapshot'])
        self.assertEqual(len(rows), 1)
        for key in ('name', 'comment', 'map_id', 'category', 'x', 'y', 'min_level', 'max_level'):
            self.assertEqual(rows[0][key], note[key])

    @unittest.skipUnless(openssl_path(), 'OpenSSL required for local HTTPS fixture')
    def test_two_clients_https_submission_and_offline_cache(self):
        with tempfile.TemporaryDirectory(prefix='wcf-client-e2e-') as temp:
            folder = Path(temp)
            cert, key = folder / 'cert.pem', folder / 'key.pem'
            subprocess.run([openssl_path(), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(key), '-out', str(cert), '-days', '1',
                            '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            php_port = self.h.port

            class Proxy(BaseHTTPRequestHandler):
                def forward(self):
                    size = int(self.headers.get('Content-Length', '0'))
                    body = self.rfile.read(size) if size else None
                    headers = {key: value for key, value in self.headers.items()
                               if key.lower() in ('cookie', 'content-type', 'accept')}
                    conn = HTTPConnection('127.0.0.1', php_port, timeout=15)
                    try:
                        conn.request(self.command, self.path, body=body, headers=headers)
                        response = conn.getresponse()
                        payload = response.read()
                        self.send_response(response.status)
                        for key, value in response.getheaders():
                            if key.lower() in ('content-type', 'set-cookie'):
                                self.send_header(key, value)
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    finally:
                        conn.close()

                def do_GET(self): self.forward()
                def do_POST(self): self.forward()
                def log_message(self, *args): pass

            server = HTTPServer(('127.0.0.1', 0), Proxy)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            endpoint = f'https://localhost:{server.server_port}/api.php?action=community_snapshot'
            try:
                with patch.dict(os.environ, {'SSL_CERT_FILE': str(cert)}):
                    data, _ = http.native_fixture()
                    self.assertTrue(submit_additions(data, endpoint)['inserted'])
                    a, b = CommunityClient(folder / 'a.json'), CommunityClient(folder / 'b.json')
                    one, two = a.sync(endpoint), b.sync(endpoint)
                    self.assertEqual(one['status'], 'online')
                    self.assertEqual(two['status'], 'online')
                    self.assertEqual([r['id'] for r in one['annotations']], [r['id'] for r in two['annotations']])
                    self.assertEqual(len(one['annotations']), 2)
                    original = one['annotations'][0]
                    self.assertEqual(submit_correction(original, 'Correction test', endpoint), 2)
                    with self.assertRaises(HTTPError) as conflict:
                        submit_correction(original, 'Version perimee', endpoint)
                    self.assertEqual(conflict.exception.code, 409)
                    updated_a, updated_b = a.sync(endpoint), b.sync(endpoint)
                    corrected = updated_a['annotations'][0]
                    self.assertEqual(corrected['name'], 'Correction test')
                    self.assertEqual(corrected['revision'], 2)
                    self.assertEqual(corrected, updated_b['annotations'][0])
                    old_fields = base64.b64decode(original['raw_base64']).split(b'|')
                    new_fields = base64.b64decode(corrected['raw_base64']).split(b'|')
                    self.assertEqual(old_fields[1:], new_fields[1:])
                    self.assertEqual(submit_correction_fields(corrected, {
                        'comment': 'Ligne 1\nLigne 2', 'category': 20, 'min_level': 12,
                        'max_level': 42}, endpoint), 3)
                    third_a, third_b = a.sync(endpoint), b.sync(endpoint)
                    edited = third_a['annotations'][0]
                    self.assertEqual(edited, third_b['annotations'][0])
                    self.assertEqual((edited['category'], edited['min_level'], edited['max_level'], edited['comment']),
                                     (20, 12, 42, 'Ligne 1\nLigne 2'))
                    for key in ('map_id', 'x', 'y', 'tags', 'flags'):
                        self.assertEqual(edited[key], original[key])
                    self.assertFalse(a.sync(endpoint)['changed'])
                    ref_a = ReferenceCommunityClient(folder / 'ref-a')
                    ref_b = ReferenceCommunityClient(folder / 'ref-b')
                    self.assertEqual(ref_a.sync('Francais', SOURCES['Francais'], endpoint)['items'], [])
                    self.assertEqual(submit_reference('Francais', SOURCES['Francais'], 2, 0, 'Nom communautaire', endpoint), 1)
                    expected = [{'id': 2, 'revision': 1, 'name': 'Nom communautaire'}]
                    self.assertEqual(ref_a.sync('Francais', SOURCES['Francais'], endpoint)['items'], expected)
                    self.assertEqual(ref_b.sync('Francais', SOURCES['Francais'], endpoint)['items'], expected)
                    with self.assertRaises(HTTPError) as ref_conflict:
                        submit_reference('Francais', SOURCES['Francais'], 2, 0, 'Ancien', endpoint)
                    self.assertEqual(ref_conflict.exception.code, 409)
                    self.assertEqual(ref_b.sync('US', SOURCES['US'], endpoint)['items'], [])
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()
            with patch.dict(os.environ, {'SSL_CERT_FILE': str(cert)}):
                recovered = CommunityClient(folder / 'a.json').sync(endpoint)
            self.assertEqual(recovered['status'], 'cached')
            self.assertEqual(len(recovered['annotations']), 2)
            self.assertEqual(recovered['annotations'][0]['name'], 'Correction test')
            with patch.dict(os.environ, {'SSL_CERT_FILE': str(cert)}):
                recovered_ref = ReferenceCommunityClient(folder / 'ref-a').sync('Francais', SOURCES['Francais'], endpoint)
            self.assertEqual(recovered_ref['status'], 'cached')
            self.assertEqual(recovered_ref['items'][0]['name'], 'Nom communautaire')


if __name__ == '__main__':
    unittest.main()
