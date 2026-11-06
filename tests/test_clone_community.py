import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from email.message import Message
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from tools import clone_community as community
from tools.clone_app import restore_community_caches
from tools.clone_reference_community import ReferenceCommunityClient
from types import SimpleNamespace
from tests import test_community_transport as tls_fixture


def fixture(name=b'Point', ident=1, revision=1):
    row = b'|'.join([name, b'', b'17', b'37', b'25711', b'35957', b'40', b'50',
                     b'', b'', b'', b'', b'0' * 32, b'', b'', b'Commentaire'])
    return dict(id=ident, import_id=1, ordinal=ident, revision=revision,
                raw_base64=base64.b64encode(row).decode('ascii'))


def snapshot(*items):
    if not items:
        return dict(header_source=None, headers_base64=None, items=[])
    return dict(header_source='wcf-neutral-v1', headers_base64=base64.b64encode(community.HEADERS).decode('ascii'), items=list(items))


class CloneCommunityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'cache.json'
        self.client = community.CommunityClient(self.path)
        self.endpoint = 'https://example.invalid/api.php?action=community_snapshot'

    def test_distinct_ids_at_same_position_are_retained(self):
        rows = community.decode_snapshot(snapshot(fixture(b'A', 1), fixture(b'B', 2)))
        self.assertEqual(len(rows), 2)
        self.assertEqual([row['source_ref'] for row in rows], ['community:1', 'community:2'])
        self.assertEqual(rows[0]['map_id'], 17)
        self.assertEqual((rows[0]['x'], rows[0]['y']), (25711, 35957))
        self.assertEqual((rows[0]['min_level'], rows[0]['max_level']), (40, 50))
        self.assertTrue(rows[0]['provenance']['unknown_fields_preserved'])

    def test_sync_is_idempotent_and_offline_cache_survives_restart(self):
        data = snapshot(fixture(b'Point accentu\xe9'))
        with patch.object(community, 'fetch_snapshot', return_value=data) as fetch:
            first = self.client.sync(self.endpoint)
            original = self.path.read_bytes()
            second = self.client.sync(self.endpoint)
            self.assertEqual(fetch.call_count, 2)
        self.assertTrue(first['changed'])
        self.assertFalse(second['changed'])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(list(self.root.glob('*.avant-sync-*'))), 0)
        with patch.object(community, 'fetch_snapshot', side_effect=URLError('offline')):
            restored = community.CommunityClient(self.path).sync(self.endpoint)
        self.assertEqual(restored['status'], 'cached')
        self.assertIn('offline', restored['error'])
        self.assertEqual(restored['annotations'][0]['name'], 'Point accentué')
        self.assertEqual(self.path.read_bytes(), original)

    def test_startup_loads_real_cache_without_network(self):
        from tools.community_transport import SNAPSHOT_URL
        with patch.object(community, 'fetch_snapshot', return_value=snapshot(fixture())):
            self.client.sync(SNAPSHOT_URL)
        model=SimpleNamespace(community=[],reference_corrections={})
        with patch.object(community,'fetch_snapshot',side_effect=AssertionError('network not allowed')):
            errors=restore_community_caches(model,community.CommunityClient(self.path),ReferenceCommunityClient(self.root))
        self.assertEqual(errors,[])
        self.assertEqual(model.community[0]['name'],'Point')

    def test_new_revision_creates_verified_backup_and_rollback_is_refused(self):
        with patch.object(community, 'fetch_snapshot', return_value=snapshot(fixture())):
            self.client.sync(self.endpoint)
        original = self.path.read_bytes()
        corrected = snapshot(fixture(b'Corrig\xe9', revision=2))
        with patch.object(community, 'fetch_snapshot', return_value=corrected):
            changed = self.client.sync(self.endpoint)
        self.assertEqual(Path(changed['backup']).read_bytes(), original)
        current = self.path.read_bytes()
        with patch.object(community, 'fetch_snapshot', return_value=snapshot(fixture())):
            refused = self.client.sync(self.endpoint)
        self.assertEqual(refused['status'], 'cached')
        self.assertIn('obsolète', refused['error'])
        self.assertEqual(refused['annotations'][0]['revision'], 2)
        self.assertEqual(self.path.read_bytes(), current)

    def test_invalid_snapshot_never_replaces_valid_cache(self):
        data = snapshot(fixture())
        with patch.object(community, 'fetch_snapshot', return_value=data):
            self.client.sync(self.endpoint)
        original = self.path.read_bytes()
        invalid = deepcopy(data)
        invalid['items'][0]['raw_base64'] = base64.b64encode(b'bad row').decode('ascii')
        with patch.object(community, 'fetch_snapshot', return_value=invalid):
            result = self.client.sync(self.endpoint)
        self.assertEqual(result['status'], 'cached')
        self.assertEqual(self.path.read_bytes(), original)
        altered = json.loads(original)
        altered['snapshot']['items'][0]['revision'] = 88
        self.path.write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Empreinte'):
            self.client.cached()

    def test_wrong_origin_and_non_https_are_refused_before_network(self):
        for endpoint in ('http://localhost/api.php', 'https://user:password@example.invalid/api.php',
                         'https://example.invalid/api.php?action=community_edit',
                         'https://example.invalid/api.php#fragment'):
            with self.subTest(endpoint=endpoint), patch.object(community, 'fetch_snapshot') as fetch:
                with self.assertRaises(ValueError):
                    self.client.sync(endpoint)
                fetch.assert_not_called()
        with patch.object(community, 'fetch_snapshot', return_value=snapshot(fixture())):
            self.client.sync(self.endpoint)
        with patch.object(community, 'fetch_snapshot') as fetch:
            with self.assertRaises(ValueError):
                self.client.sync('https://another.invalid/api.php')
            fetch.assert_not_called()

    def test_snapshot_identity_and_raw_data_are_bounded(self):
        for data in (snapshot(fixture(), fixture()),
                     snapshot(dict(fixture(), id=True)),
                     snapshot(dict(fixture(), raw_base64='%%%')),
                     snapshot(dict(fixture(), raw_base64=base64.b64encode(b'a\nb').decode('ascii')))):
            with self.subTest(data=data), self.assertRaises(ValueError):
                community.decode_snapshot(data)
        self.assertEqual(community.decode_snapshot(snapshot()), [])

    def test_paginated_fetch_over_2000_rows_restarts_after_conflict(self):
        items=[fixture(('P%d' % i).encode(),i) for i in range(1,2002)]
        class Response:
            status=200
            def __init__(self, body):
                self.body=json.dumps(body).encode(); self.headers=Message(); self.headers['Content-Type']='application/json'; self.headers['Content-Length']=str(len(self.body))
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,limit): return self.body[:limit]
        class Opener:
            conflict=False
            def open(self,request,timeout):
                q=parse_qs(urlsplit(request.full_url).query); after=int(q['after'][0])
                if after and not self.conflict:
                    self.conflict=True; raise HTTPError(request.full_url,409,'conflict',None,None)
                rows=[x for x in items if x['id']>after][:100]
                return Response({'ok':True,'page':{'generation':2 if self.conflict else 1,'items':rows,'has_more':bool(rows and rows[-1]['id']<2001)}})
        opener=Opener()
        with patch.object(community,'build_opener',return_value=opener):
            result=community.fetch_snapshot('https://example.invalid/api.php')
        self.assertTrue(opener.conflict)
        self.assertEqual(len(community.decode_snapshot(result)),2001)
        self.assertEqual(result['items'][-1]['id'],2001)


@unittest.skipUnless(tls_fixture.openssl_path(), 'OpenSSL required for local TLS fixture')
class CloneCommunityTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tls_fixture.CommunityTransportTests.setUpClass()
        cls.fixture = tls_fixture.CommunityTransportTests

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def setUp(self):
        self.fixture.handler.body = json.dumps({'ok': True, 'snapshot': snapshot(fixture(b'A', 1), fixture(b'B', 2))}).encode('utf-8')
        self.fixture.handler.status = 200
        self.fixture.handler.declared_length = None

    def test_real_tls_fetch_keeps_collocated_ids_and_rejects_bad_trust(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': ''}), self.assertRaises(URLError):
            community.fetch_snapshot(self.fixture.url)
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.fixture.cert)}):
            result = community.fetch_snapshot(self.fixture.url)
            self.assertEqual(len(community.decode_snapshot(result)), 2)
            with self.assertRaises(URLError):
                community.fetch_snapshot(self.fixture.url.replace('localhost', '127.0.0.1'))

    def test_real_tls_redirect_and_truncated_response_are_rejected(self):
        with patch.dict(os.environ, {'SSL_CERT_FILE': str(self.fixture.cert)}):
            self.fixture.handler.status = 302
            with self.assertRaises(HTTPError):
                community.fetch_snapshot(self.fixture.url)
            self.fixture.handler.status = 200
            self.fixture.handler.body = b'{}'
            self.fixture.handler.declared_length = 10
            with self.assertRaisesRegex(ValueError, 'incomplète'):
                community.fetch_snapshot(self.fixture.url)


if __name__ == '__main__':
    unittest.main()
