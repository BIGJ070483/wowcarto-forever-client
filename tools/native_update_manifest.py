"""Bounded research reader and synthetic loopback probe for the 1.20 updater.

The wire structure is supported by static analysis, not yet native runtime
validation. This module never writes application files or generates master.loc.
MD5 is a historical corruption check, not authentication.
"""
import argparse
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import zlib

MAX_MANIFEST = 64 * 1024
MAX_LINE = 2048
MAX_FILES = 256
MAX_PAYLOAD = 8 * 1024 * 1024
HEADERS = frozenset((b'VERS=', b'UPDH=', b'IVRS=', b'MESS=', b'MESE='))
SEGMENT = re.compile(rb'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
FILENAME = re.compile(rb'[A-Za-z0-9][A-Za-z0-9_. -]{0,119}\Z')
DIGEST = re.compile(rb'[0-9a-f]{32}\Z')
SAFE_EXTENSIONS = frozenset((b'.txt', b'.loc', b'.bmp', b'.jpg'))
RESERVED = frozenset((b'CON', b'PRN', b'AUX', b'NUL',
                      *(b'COM' + str(n).encode() for n in range(1, 10)),
                      *(b'LPT' + str(n).encode() for n in range(1, 10))))
SYNTHETIC_PAYLOAD = b'WCF_SYNTHETIC_UPDATE_PROBE\r\n'


def _text(value):
    if not isinstance(value, bytes) or not value or any(c < 32 or c == 127 for c in value):
        raise ValueError('Invalid manifest text')


def _segment(value):
    return bool(SEGMENT.fullmatch(value)) and value.upper() not in RESERVED


@dataclass(frozen=True)
class Header:
    key: bytes
    value: bytes


@dataclass(frozen=True)
class FileRecord:
    """Eight observed fields; field five's precise meaning remains unknown."""
    fields: tuple

    @property
    def digest(self):
        return self.fields[2]

    @property
    def compressed(self):
        return self.fields[5] == b'1'

    def validate(self):
        if not isinstance(self.fields, tuple) or len(self.fields) != 8:
            raise ValueError('Exactly eight FILE fields are required')
        for value in self.fields:
            _text(value)
            if b'|' in value:
                raise ValueError('Field delimiter in FILE value')
        filename, directory, digest, remote, display, compression, reload, group = self.fields
        stem, dot, suffix = filename.rpartition(b'.')
        if (not FILENAME.fullmatch(filename) or not dot or b'.' + suffix.lower() not in SAFE_EXTENSIONS
                or filename.endswith((b' ', b'.')) or b'..' in filename
                or stem.split(b'.')[0].upper() in RESERVED):
            raise ValueError('Unsafe or unsupported target filename')
        if (not directory.startswith(b'\\') or not directory.endswith(b'\\')
                or not all(_segment(part) for part in directory[1:-1].split(b'\\'))):
            raise ValueError('Unsafe target directory')
        # This reader deliberately admits only data directories, never EXE/DLL targets.
        if directory[1:-1].split(b'\\')[0].lower() not in (b'res', b'ajouts', b'database', b'maps'):
            raise ValueError('Unsupported target directory')
        if (not re.fullmatch(rb'[A-Za-z0-9][A-Za-z0-9_./-]{0,199}', remote)
                or any(part in (b'', b'.', b'..') for part in remote.split(b'/'))):
            raise ValueError('Unsafe remote path')
        if not DIGEST.fullmatch(digest) or compression not in (b'0', b'1') or reload not in (b'0', b'1'):
            raise ValueError('Invalid checksum or flags')
        if len(display) > 256 or not re.fullmatch(rb'[A-Za-z0-9][A-Za-z0-9_ :=.-]{0,63}', group):
            raise ValueError('Unsupported display field or group')


@dataclass(frozen=True)
class NativeManifest:
    entries: tuple
    raw: bytes = b''

    @property
    def files(self):
        return tuple(entry for entry in self.entries if isinstance(entry, FileRecord))


def parse_manifest(data):
    """Read a deliberately narrow safe subset; never contact a URL in the input."""
    if not isinstance(data, bytes) or not data or len(data) > MAX_MANIFEST:
        raise ValueError('Manifest size refused')
    # Native TStringList accepts line endings; retain exact input in .raw.
    if b'\r' in data.replace(b'\r\n', b''):
        raise ValueError('Unsupported line endings')
    lines = data.replace(b'\r\n', b'\n').split(b'\n')
    if lines[-1] == b'':
        lines.pop()
    if not lines or lines[-1] != b'EOF' or b'EOF' in lines[:-1]:
        raise ValueError('Missing or misplaced EOF')
    entries, seen, destinations = [], set(), set()
    for line in lines[:-1]:
        _text(line)
        if len(line) > MAX_LINE:
            raise ValueError('Manifest line too long')
        key, value = line[:5], line[5:]
        if key == b'FILE=':
            record = FileRecord(tuple(value.split(b'|')))
            record.validate()
            destination = (record.fields[1] + record.fields[0]).lower()
            if destination in destinations:
                raise ValueError('Repeated target destination')
            destinations.add(destination)
            entries.append(record)
            if len(destinations) > MAX_FILES:
                raise ValueError('Too many files')
        elif key in HEADERS:
            _text(value)
            if key in seen:
                raise ValueError('Repeated manifest header')
            seen.add(key)
            if key == b'IVRS=' and not re.fullmatch(rb'[1-9][0-9]{0,6}', value):
                raise ValueError('Invalid client version')
            if key == b'UPDH=':
                try:
                    url = urlsplit(value.decode('ascii'))
                    _ = url.port
                except (UnicodeError, ValueError) as exc:
                    raise ValueError('Malformed download base') from exc
                if (url.scheme not in ('http', 'https') or not url.hostname or url.username is not None
                        or url.password is not None or url.query or url.fragment or not url.path.endswith('/')
                        or b'\\' in value or any(c <= 32 or c >= 127 for c in value)):
                    raise ValueError('Unsupported download base')
            entries.append(Header(key, value))
        else:
            raise ValueError('Unknown manifest line')
    if not {b'VERS=', b'UPDH=', b'IVRS='}.issubset(seen):
        raise ValueError('Required headers missing')
    return NativeManifest(tuple(entries), data)


def build_manifest(manifest):
    """Serialize the research subset and validate it before returning bytes."""
    lines = []
    for entry in manifest.entries:
        if isinstance(entry, Header):
            lines.append(entry.key + entry.value)
        elif isinstance(entry, FileRecord):
            entry.validate()
            lines.append(b'FILE=' + b'|'.join(entry.fields))
        else:
            raise ValueError('Unsupported manifest entry')
    data = b'\r\n'.join(lines + [b'EOF', b''])
    parse_manifest(data)
    return data


def verify_payload(record, data, limit=MAX_PAYLOAD):
    """Return verified raw bytes; no file is applied or written."""
    record.validate()
    if not isinstance(data, bytes) or not 0 < len(data) <= limit or not 1 <= limit <= MAX_PAYLOAD:
        raise ValueError('Payload size refused')
    raw = data
    if record.compressed:
        stream = zlib.decompressobj()
        try:
            raw = stream.decompress(data, limit + 1)
        except zlib.error as exc:
            raise ValueError('Invalid zlib payload') from exc
        if (len(raw) > limit or stream.unconsumed_tail or not stream.eof or stream.unused_data):
            raise ValueError('Oversized, truncated or trailing zlib data')
    digest = hashlib.md5(raw, usedforsecurity=False).hexdigest().encode('ascii')
    if digest != record.digest:
        raise ValueError('Payload MD5 mismatch')
    return raw


def synthetic_fixture(port, compressed=False):
    """Generate one inert text update, exclusively for IPv4 loopback."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    raw = SYNTHETIC_PAYLOAD
    fields = (b'wcf_probe.txt', b'\\res\\',
              hashlib.md5(raw, usedforsecurity=False).hexdigest().encode('ascii'),
              b'payload', b'Synthetic probe', b'1' if compressed else b'0', b'0', b'res')
    manifest = NativeManifest((Header(b'VERS=', b'WCF synthetic probe'),
        Header(b'IVRS=', b'1204'), Header(b'UPDH=', f'http://127.0.0.1:{port}/'.encode('ascii')),
        Header(b'MESS=', b'Essai fictif local'), FileRecord(fields)))
    return build_manifest(manifest), zlib.compress(raw) if compressed else raw


class FixtureServer(HTTPServer):
    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(2)
        self.requests_seen += 1
        return sock, address


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if len(self.path) > MAX_LINE:
            self.send_error(414)
            return
        try:
            path = urlsplit(self.path)
        except ValueError:
            self.send_error(400)
            return
        if path.scheme or path.netloc or path.fragment:
            self.send_error(400)
            return
        if path.path == '/manifest':
            body, kind = self.server.manifest, 'manifest'
        elif path.path == '/payload' and not path.query:
            body, kind = self.server.payload, 'payload'
        else:
            self.send_error(404)
            return
        self.server.observations.append(kind)
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)


def make_fixture_server(port=0, compressed=False):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid port')
    server = FixtureServer(('127.0.0.1', port), FixtureHandler)
    server.timeout = .5
    server.manifest, server.payload = synthetic_fixture(server.server_port, compressed)
    server.observations = []
    server.requests_seen = 0
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='Inspect a local manifest without displaying its contents')
    inspect.add_argument('file', type=Path)
    serve = commands.add_parser('serve', help='Serve only an immutable fictitious update on 127.0.0.1')
    serve.add_argument('--port', type=int, default=8766)
    serve.add_argument('--seconds', type=int, default=30)
    serve.add_argument('--requests', type=int, default=8)
    serve.add_argument('--compressed', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'inspect':
            with args.file.open('rb') as source:
                data = source.read(MAX_MANIFEST + 1)
            manifest = parse_manifest(data)
            print(json.dumps({'sha256': hashlib.sha256(data).hexdigest(), 'files': len(manifest.files),
                              'contents_redacted': True, 'native_runtime_verified': False}))
        else:
            if not 1 <= args.seconds <= 60 or not 1 <= args.requests <= 64:
                raise ValueError('Probe duration or request count refused')
            with make_fixture_server(args.port, args.compressed) as server:
                print(json.dumps({'listening': '127.0.0.1', 'port': server.server_port,
                                  'synthetic_only': True}), flush=True)
                deadline = time.monotonic() + args.seconds
                while server.requests_seen < args.requests and time.monotonic() < deadline:
                    server.handle_request()
                print(json.dumps({'responses': server.observations, 'native_runtime_verified': False}))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Probe refused: {exc}\n')


if __name__ == '__main__':
    main()
