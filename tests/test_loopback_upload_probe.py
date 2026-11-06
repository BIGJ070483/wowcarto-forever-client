import http.client
import threading
import unittest

from tools.loopback_upload_probe import make_server, summarize


BODY = (b'--demo\r\nContent-Disposition: form-data; name="type"\r\n\r\nwow\r\n'
        b'--demo\r\nContent-Disposition: form-data; name="pass"\r\n\r\nfake-secret\r\n'
        b'--demo\r\nContent-Disposition: form-data; name="file"; filename="fake.txt"\r\n'
        b'Content-Type: application/msword\r\n\r\nfake-content\r\n--demo--\r\n')


class LoopbackProbeTests(unittest.TestCase):
    def test_synthetic_post_is_local_and_redacted(self):
        with make_server() as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request('POST', '/test', BODY, {'Content-Type': 'multipart/form-data; boundary=demo'})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b'<done>')
            conn.close()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            result = str(server.probe_summary)
            self.assertNotIn('fake-secret', result)
            self.assertNotIn('fake-content', result)
            self.assertEqual([x['field'] for x in server.probe_summary['parts']], ['type', 'pass', 'file'])

    def test_invalid_or_unknown_parts_rejected(self):
        with self.assertRaises(ValueError):
            summarize('text/plain', BODY)
        with self.assertRaises(ValueError):
            summarize('multipart/form-data; boundary=demo', BODY.replace(b'name="pass"', b'name="unknown"'))
        with self.assertRaises(ValueError):
            summarize('multipart/form-data; boundary=demo', BODY[:-14])


if __name__ == '__main__':
    unittest.main()
