"""Explicit submission of a user-selected historical additions file."""
from __future__ import annotations

import base64
import binascii
from http.cookiejar import CookieJar
import json
import re
import ssl
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
import uuid

from tools.community_transport import NoRedirect, SNAPSHOT_URL
from tools.clone_community import HEADERS
from tools.native_additions import parse_additions

MAX_FILE = 1024 * 1024
MAX_REPLY = 1024 * 1024


def _session(endpoint, test_loopback):
    u = urlsplit(endpoint)
    loopback = test_loopback and u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost', '::1')
    if ((u.scheme != 'https' and not loopback) or not u.hostname or u.username is not None or
            u.password is not None or u.fragment or any(ord(c) <= 32 for c in endpoint)):
        raise ValueError('Adresse communautaire non sécurisée.')
    base = urlunsplit((u.scheme, u.netloc, u.path or '/', '', ''))
    opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()),
                          HTTPSHandler(context=ssl.create_default_context()))
    def request(action, body=None, headers=None):
        req = Request(base + '?action=' + action, data=body, headers=headers or {'Accept': 'application/json'})
        with opener.open(req, timeout=15) as response:
            if response.status not in (200, 201) or response.headers.get_content_type() != 'application/json':
                raise ValueError('Réponse du service communautaire invalide.')
            raw = response.read(MAX_REPLY + 1)
            if len(raw) > MAX_REPLY:
                raise ValueError('Réponse communautaire trop volumineuse.')
        try:
            reply = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError) as error:
            raise ValueError('Réponse communautaire illisible.') from error
        if not isinstance(reply, dict) or reply.get('ok') is not True:
            raise ValueError('Le service communautaire a refusé la demande.')
        return reply
    bootstrap = request('bootstrap')
    csrf = bootstrap.get('csrf')
    if bootstrap.get('open') is not True or bootstrap.get('community_ready') is not True:
        raise ValueError('Le service communautaire est fermé ou en préparation.')
    if not isinstance(csrf, str) or not re.fullmatch('[0-9a-f]{64}', csrf):
        raise ValueError('Session communautaire invalide.')
    return request, csrf, bootstrap.get('limits')


def submit_additions(data, endpoint=SNAPSHOT_URL, *, _test_loopback=False):
    """Submit only after an explicit user action; never store or transform data."""
    if not isinstance(data, bytes) or len(data) > MAX_FILE:
        raise ValueError('Fichier absent ou supérieur à 1 Mio.')
    parsed = parse_additions(data)
    request, csrf, limits = _session(endpoint, _test_loopback)
    if (not isinstance(limits, dict) or type(limits.get('bytes')) is not int or
            type(limits.get('records')) is not int or len(data) > limits['bytes'] or
            len(parsed.records) > limits['records']):
        raise ValueError('Le fichier dépasse les limites du service.')
    boundary = 'wcf-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n{csrf}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="additions.txt"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode('ascii')
    body += data + f'\r\n--{boundary}--\r\n'.encode('ascii')
    reply = request('community_upload', body, {'Accept': 'application/json',
                    'Content-Type': 'multipart/form-data; boundary=' + boundary})
    result = reply.get('import')
    if (not isinstance(result, dict) or type(result.get('records')) is not int or
            result['records'] != len(parsed.records) or type(result.get('inserted')) is not bool):
        raise ValueError('Confirmation de contribution incohérente.')
    return result


def personal_addition(row):
    """Encode a new French 1.20 personal note; never discard unsupported fields."""
    if (not isinstance(row, dict) or row.get('source') != 'personal' or
            not str(row.get('source_ref', '')).startswith('personal:reference120:Francais:')):
        raise ValueError('Seules les nouvelles annotations personnelles FR 1.20 peuvent être partagées ainsi.')
    if row.get('tags') or row.get('flags'):
        raise ValueError('Le partage direct ne prend pas encore en charge les tags ou indicateurs de cette annotation.')
    fields = [b'', b'', b'0', b'0', b'0', b'0', b'0', b'0', b'', b'', b'', b'', b'0' * 32, b'', b'', b'']
    for key, index in (('map_id', 2), ('category', 3), ('x', 4), ('y', 5), ('min_level', 6), ('max_level', 7)):
        value = row.get(key)
        if type(value) is not int or not 0 <= value <= 65535:
            raise ValueError('Coordonnées, catégorie ou niveaux invalides.')
        fields[index] = str(value).encode('ascii')
    fields[0] = b'Note'
    raw = b'|'.join(fields)
    synthetic = {'source': 'community', 'id': 'community:1', 'revision': 1,
                 'raw_base64': base64.b64encode(raw).decode('ascii')}
    changed = corrected_record(synthetic, {'name': row.get('name'), 'comment': row.get('comment', '')})
    data = HEADERS + b'\r\n' + changed + b'\r\n'
    parse_additions(data)
    return data


def submit_correction(row, name, endpoint=SNAPSHOT_URL, *, _test_loopback=False):
    """Change only a community record's name using optimistic revision checks."""
    return submit_correction_fields(row, {'name': name}, endpoint, _test_loopback=_test_loopback)


def corrected_record(row, changes):
    """Change understood fields only, preserving location and opaque native bytes."""
    if not isinstance(row, dict) or row.get('source') != 'community':
        raise ValueError('Cette annotation ne provient pas de la communauté.')
    ident = row.get('id')
    revision = row.get('revision')
    if (not isinstance(ident, str) or not re.fullmatch(r'community:[1-9][0-9]{0,17}', ident) or
            type(revision) is not int or not 1 <= revision < 100):
        raise ValueError('Identifiant ou révision communautaire invalide.')
    if not isinstance(changes, dict) or not changes or set(changes) - {'name', 'comment', 'category', 'min_level', 'max_level'}:
        raise ValueError('Champs de correction non reconnus.')
    try:
        raw = base64.b64decode(row.get('raw_base64', ''), validate=True)
    except (UnicodeError, binascii.Error, ValueError, TypeError) as error:
        raise ValueError('Encodage de correction non pris en charge.') from error
    parsed = parse_additions(HEADERS + b'\r\n' + raw + b'\r\n')
    if len(parsed.records) != 1 or b'\r' in raw or b'\n' in raw:
        raise ValueError('Annotation communautaire invalide.')
    fields = list(parsed.records[0].fields)
    try:
        for key, index in (('name', 0), ('comment', 15)):
            if key not in changes:
                continue
            value = changes[key]
            if (not isinstance(value, str) or '\x00' in value or '\r' in value or '|' in value or
                    (key == 'name' and (not value.strip() or len(value) > 200 or '\n' in value))):
                raise ValueError('Texte de correction invalide.')
            parts = [part.encode('cp1252', errors='strict') for part in value.split('\n')]
            if key == 'comment':
                fields[15:] = parts
            else:
                fields[index] = parts[0]
    except UnicodeEncodeError as error:
        raise ValueError('Encodage de correction non pris en charge.') from error
    for key, index in (('category', 3), ('min_level', 6), ('max_level', 7)):
        if key in changes:
            value = changes[key]
            if type(value) is not int or not 0 <= value <= 65535:
                raise ValueError('Valeur numérique de correction invalide.')
            fields[index] = str(value).encode('ascii')
    minimum, maximum = int(fields[6]), int(fields[7])
    if minimum and maximum and minimum > maximum:
        raise ValueError('La plage de niveaux est inversée.')
    changed = b'|'.join(fields)
    parse_additions(HEADERS + b'\r\n' + changed + b'\r\n')
    return changed


def submit_correction_fields(row, changes, endpoint=SNAPSHOT_URL, *, _test_loopback=False):
    """Submit a revision preserving all unspecified bytes and the original location."""
    changed = corrected_record(row, changes)
    ident, revision = row['id'], row['revision']
    request, csrf, _ = _session(endpoint, _test_loopback)
    body = urlencode({'csrf': csrf, 'id': ident.split(':', 1)[1], 'revision': str(revision),
                      'record_base64': base64.b64encode(changed).decode('ascii')}).encode('ascii')
    reply = request('community_edit', body, {'Accept': 'application/json',
                    'Content-Type': 'application/x-www-form-urlencoded'})
    if type(reply.get('revision')) is not int or reply['revision'] != revision + 1:
        raise ValueError('Confirmation de correction incohérente.')
    return reply['revision']
