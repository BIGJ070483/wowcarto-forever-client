"""Local TLS integration tests; keys are generated only in a temporary folder."""
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from tools import community_transport as transport
from tests.test_hosting_http import native_fixture


def openssl_path():
    found = shutil.which('openssl')
    if found:
        return found
    candidate = Path(r'C:\Program Files\Git\usr\bin\openssl.exe')
    return str(candidate) if os.name == 'nt' and candidate.is_file() else None


@unittest.skipUnless(openssl_path(), 'OpenSSL required to generate temporary TLS fixture')
class CommunityTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='wcf-tls-fixture-')
        root = Path(cls.temp.name)
        cls.cert, key = root / 'certificate.pem', root / 'temporary-key.pem'
        subprocess.run([openssl_path(), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        class Handler(BaseHTTPRequestHandler):
            body = b''
            status = 200
            declared_length = None
            def do_GET(self):
                self.send_response(self.status)
                if self.status == 302:
                    self.send_header('Location', 'https://example.invalid/')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(self.body) if self.declared_length is None else self.declared_length))
                self.end_headers()
                try:
                    self.wfile.write(self.body)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass
            def log_message(self, *args):
                pass

        cls.handler = Handler
        cls.http = HTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.http.socket = context.wrap_socket(cls.http.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'https://localhost:{cls.http.server_port}/api.php?action=community_snapshot'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.thread.join(timeout=3)
        cls.http.server_close()
        cls.temp.cleanup()

    def setUp(self):
        _, row = native_fixture()
        encode = lambda value: base64.b64encode(value).decode('ascii')
        snapshot = {'header_source': 'wcf-neutral-v1', 'headers_base64': encode(b'#v001-id=1-WCF\r\n#1204-ab12'),
                    'items': [{'id': 1, 'import_id': 1, 'ordinal': 1, 'revision': 1, 'raw_base64': encode(row)}]}
        self.handler.body = json.dumps({'ok': True, 'snapshot': snapshot}).encode('utf-8')
        self.handler.status = 200
        self.handler.declared_length = None

    @contextmanager
    def origin(self, trust):
        env = {'SSL_CERT_FILE': str(self.cert)} if trust else {'SSL_CERT_FILE': ''}
        with patch.object(transport, 'SNAPSHOT_URL', self.url), patch.dict(os.environ, env):
            yield

    def test_untrusted_certificate_is_rejected_and_trusted_fixture_succeeds(self):
        with self.origin(False), self.assertRaises(URLError) as failure:
            transport.fetch_snapshot()
        self.assertIsInstance(failure.exception.reason, ssl.SSLCertVerificationError)
        with self.origin(True):
            result = transport.fetch_snapshot()
        self.assertEqual(len(result['records']), 1)
        self.assertFalse(result['native_verified'])
        with self.origin(True), patch.object(transport, 'SNAPSHOT_URL', self.url.replace('localhost', '127.0.0.1')):
            with self.assertRaises(URLError) as mismatch:
                transport.fetch_snapshot()
        self.assertIsInstance(mismatch.exception.reason, ssl.SSLCertVerificationError)

    def test_redirect_and_invalid_response_are_rejected(self):
        with self.origin(True):
            self.handler.status = 302
            with self.assertRaises(HTTPError):
                transport.fetch_snapshot()
            self.handler.status = 200
            self.handler.body = b'{invalid json'
            with self.assertRaisesRegex(ValueError, 'invalide'):
                transport.fetch_snapshot()
            self.handler.body = b'{}'
            self.handler.declared_length = 10
            with self.assertRaisesRegex(ValueError, 'incomplète'):
                transport.fetch_snapshot()
            self.handler.declared_length = None
            self.handler.body = b'x' * (transport.MAX_RESPONSE + 1)
            with self.assertRaisesRegex(ValueError, 'volumineuse'):
                transport.fetch_snapshot()
