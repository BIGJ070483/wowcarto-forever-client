"""Anonymous community editing against the actual disposable PHP server."""
import base64
from contextlib import closing
import sqlite3
import unittest
from tests import test_hosting_http as http
from tools.community_assembly import assemble_snapshot


@unittest.skipUnless(http.PHP, 'PHP pdo_sqlite required')
class CommunityHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): http.HostingHttpTests.setUpClass()

    @classmethod
    def tearDownClass(cls): http.HostingHttpTests.tearDownClass()

    def setUp(self):
        self.h = http.HostingHttpTests('test_closed_service_health_and_no_writes')
        self.h.setUp()
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.executescript('''
            INSERT INTO wcf_schema_versions VALUES (3);
            CREATE TABLE wcf_community_imports(id INTEGER PRIMARY KEY,sha256 BLOB UNIQUE,raw_file BLOB,record_count INTEGER);
            CREATE TABLE wcf_community_annotations(id INTEGER PRIMARY KEY,import_id INTEGER,ordinal INTEGER,revision INTEGER,raw_record BLOB,UNIQUE(import_id,ordinal));
            CREATE TABLE wcf_community_revisions(id INTEGER PRIMARY KEY,annotation_id INTEGER,revision INTEGER,raw_record BLOB,UNIQUE(annotation_id,revision));
            ''')

    def test_anonymous_upload_edit_history_conflict_and_closed_mode(self):
        data, row = http.native_fixture()
        self.assertEqual(self.h.call('community_upload', upload=data, csrf=False)[0], 403)
        code, first = self.h.call('community_upload', upload=data)
        self.assertEqual(code, 201)
        self.assertEqual(first['import']['records'], 2)
        self.assertEqual(self.h.call('community_upload', upload=data)[0], 200)
        items = self.h.call('community_list')[1]['items']
        self.assertEqual([base64.b64decode(item['raw_base64']) for item in items], [row,row])
        changed = row.replace(b'TEST \xe9', b'CORRIGE \xe9')
        fields = {'id': str(items[0]['id']), 'revision': '1', 'record_base64': base64.b64encode(changed).decode()}
        self.assertEqual(self.h.call('community_edit', fields)[1]['revision'], 2)
        self.assertEqual(self.h.call('community_edit', fields)[0], 409)
        versions = self.h.call('community_history&id=1')[1]['versions']
        self.assertEqual([v['revision'] for v in versions], [2, 1])
        restore = {'id': '1', 'revision': '2', 'record_base64': versions[1]['raw_base64']}
        self.assertEqual(self.h.call('community_edit', restore)[1]['revision'], 3)
        with closing(sqlite3.connect(self.h.database)) as db:
            db.text_factory = bytes
            self.assertEqual(db.execute('SELECT revision,raw_record FROM wcf_community_revisions WHERE annotation_id=1 ORDER BY revision').fetchall(), [(1,row),(2,changed),(3,row)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wcf_members').fetchone()[0], 0)
        self.h.write_config(False)
        self.assertEqual(self.h.call('community_list')[0], 503)
        self.assertEqual(self.h.call('community_upload', upload=data)[0], 503)
        self.assertEqual(self.h.call('community_edit', fields)[0], 503)

    def test_invalid_record_does_not_write_revision(self):
        self.h.call('community_upload', upload=http.native_fixture()[0])
        fields={'id':'1','revision':'1','record_base64':base64.b64encode(b'bad\nrow').decode()}
        self.assertEqual(self.h.call('community_edit', fields)[0], 400)
        with closing(sqlite3.connect(self.h.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wcf_community_revisions').fetchone()[0], 2)

    def test_unreadable_coordinates_are_rejected_before_import(self):
        data, row=http.native_fixture(); fields=row.split(b'|'); fields[4]=b'not-a-coordinate'
        bad=data.replace(row,b'|'.join(fields))
        self.assertEqual(self.h.call('community_upload',upload=bad)[0],400)
        with closing(sqlite3.connect(self.h.database)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wcf_community_imports').fetchone()[0],0)

    def test_upload_never_exceeds_snapshot_record_limit(self):
        data, row = http.native_fixture()
        headers = data.splitlines()[:2]
        almost_full = b'\r\n'.join(headers + [row] * 1999) + b'\r\n'
        self.assertEqual(self.h.call('community_upload', upload=almost_full)[0], 201)
        self.assertEqual(self.h.call('community_upload', upload=data)[0], 413)
        code, snapshot = self.h.call('community_snapshot')
        self.assertEqual(code, 200)
        self.assertEqual(len(snapshot['snapshot']['items']), 1999)

    def test_paginated_uploads_exceed_legacy_limit_without_losing_rows(self):
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.executescript('INSERT INTO wcf_schema_versions VALUES (4),(5); CREATE TABLE wcf_community_generation(id INTEGER PRIMARY KEY,generation INTEGER NOT NULL); INSERT INTO wcf_community_generation VALUES (1,0);')
        data, row = http.native_fixture()
        headers=data.splitlines()[:2]
        self.assertEqual(self.h.call('community_upload',upload=b'\r\n'.join(headers+[row]*1999)+b'\r\n')[0],201)
        self.assertEqual(self.h.call('community_upload',upload=data)[0],201)
        ids=[]; after=0; generation=None
        while True:
            action='community_page&after='+str(after)
            if generation is not None: action+='&generation='+str(generation)
            code, reply=self.h.call(action); self.assertEqual(code,200)
            page=reply['page']; generation=page['generation']; ids.extend(x['id'] for x in page['items'])
            if not page['has_more']: break
            after=ids[-1]
        self.assertEqual(len(ids),2001)
        self.assertEqual(len(set(ids)),2001)
        self.assertEqual(self.h.call('community_page&after=0&generation=0')[0],409)
        self.assertEqual(self.h.call('community_snapshot')[0],413)

    def test_snapshot_contains_current_rows_and_rejects_inconsistent_history(self):
        code, reply = self.h.call('community_snapshot')
        self.assertEqual(code, 200)
        self.assertEqual(reply['snapshot']['items'], [])
        data, row = http.native_fixture()
        self.h.call('community_upload', upload=data)
        first_snapshot = self.h.call('community_snapshot')[1]['snapshot']
        assembled = assemble_snapshot(first_snapshot)
        self.assertEqual(len(assembled['records']), 1)
        self.assertEqual(len(assembled['records'][0]['origins']), 2)
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.execute('UPDATE wcf_community_imports SET raw_file=? WHERE id=1', (b'corrupt',))
        self.assertEqual(self.h.call('community_snapshot')[0], 503)
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.execute('UPDATE wcf_community_imports SET raw_file=? WHERE id=1', (data,))
        changed = row.replace(b'TEST \xe9', b'CORRIGE \xe9')
        fields = {'id': '1', 'revision': '1', 'record_base64': base64.b64encode(changed).decode()}
        self.assertEqual(self.h.call('community_edit', fields)[0], 200)
        snapshot = self.h.call('community_snapshot')[1]['snapshot']
        self.assertEqual(snapshot['header_source'], 'wcf-neutral-v1')
        self.assertEqual(base64.b64decode(snapshot['headers_base64']), b'#v001-id=1-WCF\r\n#1204-ab12')
        self.assertEqual([base64.b64decode(item['raw_base64']) for item in snapshot['items']], [changed, row])
        self.assertEqual([item['revision'] for item in snapshot['items']], [2, 1])
        with self.assertRaisesRegex(ValueError, 'ambigus'):
            assemble_snapshot(snapshot)
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.execute('UPDATE wcf_community_revisions SET raw_record=? WHERE annotation_id=1 AND revision=2', (b'corrupt',))
        self.assertEqual(self.h.call('community_snapshot')[0], 503)
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.execute('UPDATE wcf_community_annotations SET raw_record=? WHERE id=1', (b'x' * (4 * 1024 * 1024 + 1),))
        self.assertEqual(self.h.call('community_snapshot')[0], 413)
        self.h.write_config(False)
        self.assertEqual(self.h.call('community_snapshot')[0], 503)

    def test_legacy_candidate_route_never_exposes_original_header(self):
        data, _ = http.native_fixture()
        self.assertEqual(self.h.call('community_upload', upload=data)[0], 201)
        self.assertEqual(self.h.call('community_candidate&id=1')[0], 404)
        self.h.write_config(False)
        self.assertEqual(self.h.call('community_candidate&id=1')[0], 404)

    def test_snapshot_detects_corruption_in_later_sources_and_initial_revisions(self):
        data, row = http.native_fixture()
        other = data.replace(b'|123|', b'|124|')
        self.assertEqual(self.h.call('community_upload', upload=data)[0], 201)
        self.assertEqual(self.h.call('community_upload', upload=other)[0], 201)
        self.assertEqual(len(self.h.call('community_snapshot')[1]['snapshot']['items']), 4)
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.execute('UPDATE wcf_community_imports SET raw_file=? WHERE id=2', (b'corrupt',))
        self.assertEqual(self.h.call('community_snapshot')[0], 503)
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.execute('UPDATE wcf_community_imports SET raw_file=? WHERE id=2', (other,))
            db.execute('UPDATE wcf_community_revisions SET raw_record=? WHERE annotation_id=3 AND revision=1', (row,))
        self.assertEqual(self.h.call('community_snapshot')[0], 503)
