"""Single-use loopback-only multipart probe; no credentials or file contents logged.

Research tool for synthetic data only. It does not authenticate, persist, import,
or implement a community service. Never expose it on a non-loopback interface.
"""
import argparse
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

MAX_BODY = 2 * 1024 * 1024


def summarize(content_type, body):
    if len(body) > MAX_BODY or not content_type.lower().startswith('multipart/form-data;'):
        raise ValueError('Invalid multipart request')
    # Avoid header injection into the synthetic MIME envelope.
    if '\r' in content_type or '\n' in content_type:
        raise ValueError('Invalid content type')
    msg = BytesParser(policy=policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('ascii') + b'\r\nMIME-Version: 1.0\r\n\r\n' + body)
    if not msg.is_multipart() or msg.defects:
        raise ValueError('Malformed multipart')
    parts = []
    allowed = {'type', 'base', 'pass', 'file'}
    for part in msg.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if part.defects or part.get_content_disposition() != 'form-data' or name not in allowed:
            raise ValueError('Unexpected part')
        payload = part.get_payload(decode=True)
        if payload is None:
            raise ValueError('Unreadable part')
        parts.append({'field': name, 'bytes': len(payload), 'has_filename': part.get_filename() is not None,
                      'content_type': part.get_content_type()})
    return {'parts': parts, 'body_bytes': len(body), 'values_redacted': True}


class ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            if self.headers.get('Transfer-Encoding') is not None:
                raise ValueError('Unsupported transfer encoding')
            length = int(self.headers.get('Content-Length', ''))
            if length < 0 or length > MAX_BODY:
                raise ValueError('Invalid length')
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Truncated body')
            summary = summarize(self.headers.get('Content-Type', ''), body)
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        self.server.probe_summary = summary
        response = b'<done>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=us-ascii')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def make_server(port=0):
    server = HTTPServer(('127.0.0.1', port), ProbeHandler)
    server.timeout = 30
    server.probe_summary = None
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with make_server(args.port) as server:
        print(json.dumps({'listening': '127.0.0.1', 'port': server.server_port}), flush=True)
        server.handle_request()
        print(json.dumps({'result': server.probe_summary}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
