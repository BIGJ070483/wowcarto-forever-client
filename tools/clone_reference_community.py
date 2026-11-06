"""Versioned community corrections bound to the historical FR/EN sources."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
import uuid

from tools.clone_community import snapshot_endpoint
from tools.clone_contributions import _session
from tools.community_transport import SNAPSHOT_URL

SOURCES = {
    'Francais': 'f1838d4946221d610fb7f54241bb574670dadb3da4b1bf1398821b640b7bb6b5',
    'US': '58ae7d6ffd4f3e6a16ef27dc696b2f9c754a1752a1ae774f30ccf7d02637761e',
}
MAX_CACHE = 64 * 1024 * 1024


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _valid_source(locale, sha):
    if SOURCES.get(locale) != sha:
        raise ValueError('Base de référence incompatible avec le service.')


def _valid_name(name):
    return (isinstance(name, str) and bool(name.strip()) and len(name) <= 200 and
            len(name.encode('utf-8')) <= 800 and not any(ord(c) < 32 or ord(c) == 127 for c in name))


def submit_reference(locale, sha, ident, revision, name, endpoint=SNAPSHOT_URL, *, _test_loopback=False):
    _valid_source(locale, sha)
    if type(ident) is not int or not 1 <= ident <= 1000000 or type(revision) is not int or not 0 <= revision < 1000000 or not _valid_name(name):
        raise ValueError('Correction de référence invalide.')
    request, csrf, _ = _session(endpoint, _test_loopback)
    data = urlencode(dict(csrf=csrf, locale=locale, source_sha256=sha, id=ident, revision=revision, name=name)).encode()
    reply = request('reference_edit', data, {'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded'})
    if type(reply.get('revision')) is not int or reply['revision'] != revision + 1:
        raise ValueError('Confirmation de correction incohérente.')
    return reply['revision']


def _validate(items):
    if not isinstance(items, list) or len(items) > 500000:
        raise ValueError('Liste de corrections invalide.')
    last = 0
    for row in items:
        if (not isinstance(row, dict) or set(row) != {'id','revision','name'} or
                type(row['id']) is not int or not last < row['id'] <= 1000000 or
                type(row['revision']) is not int or not 1 <= row['revision'] <= 1000000 or not _valid_name(row['name'])):
            raise ValueError('Correction de référence invalide.')
        last = row['id']


def fetch_reference(locale, sha, endpoint=SNAPSHOT_URL, *, _test_loopback=False):
    _valid_source(locale, sha)
    for attempt in range(3):
        request, _, _ = _session(endpoint, _test_loopback)
        items, after, generation = [], 0, None
        try:
            while True:
                params = dict(locale=locale, source_sha256=sha, after=after)
                if generation is not None: params['generation'] = generation
                page = request('reference_page&' + urlencode(params)).get('page')
                if (not isinstance(page, dict) or page.get('source_sha256') != sha or
                        type(page.get('generation')) is not int or page['generation'] < 0 or
                        type(page.get('has_more')) is not bool or not isinstance(page.get('items'), list) or
                        len(page['items']) > 500):
                    raise ValueError('Page de corrections invalide.')
                if generation is None: generation = page['generation']
                if page['generation'] != generation: raise ValueError('Pages de corrections incohérentes.')
                _validate(page['items'])
                if page['items'] and page['items'][0]['id'] <= after: raise ValueError('Pages de corrections non ordonnées.')
                items.extend(page['items'])
                if len(items) > 500000: raise ValueError('Trop de corrections.')
                if not page['has_more']: return {'generation':generation, 'items':items}
                if not page['items']: raise ValueError('Page de corrections incomplète.')
                after = page['items'][-1]['id']
        except HTTPError as error:
            if error.code != 409 or attempt == 2: raise
    raise ValueError('Synchronisation de corrections impossible.')


class ReferenceCommunityClient:
    def __init__(self, folder):
        self.folder = Path(folder)

    def _path(self, locale):
        if locale not in SOURCES: raise ValueError('Langue non prise en charge.')
        return self.folder / ('reference-community-' + locale + '.json')

    def _read(self, locale, endpoint):
        path = self._path(locale)
        if path.stat().st_size > MAX_CACHE: raise ValueError('Cache de corrections trop volumineux.')
        record = json.loads(path.read_bytes())
        body = {key:record.get(key) for key in ('format','endpoint','locale','source_sha256','generation','items')}
        if (record.get('sha256') != hashlib.sha256(_json(body)).hexdigest() or body['format'] != 'wcf-reference-corrections-v1' or
                body['endpoint'] != endpoint or body['locale'] != locale or body['source_sha256'] != SOURCES[locale] or
                type(body['generation']) is not int or body['generation'] < 0):
            raise ValueError('Cache de corrections incohérent.')
        _validate(body['items'])
        return body

    def cached(self, locale, endpoint=SNAPSHOT_URL):
        return self._read(locale, snapshot_endpoint(endpoint))['items']

    def sync(self, locale, sha, endpoint=SNAPSHOT_URL):
        _valid_source(locale, sha)
        endpoint = snapshot_endpoint(endpoint)
        path = self._path(locale)
        previous = self._read(locale, endpoint) if path.exists() else None
        try:
            current = fetch_reference(locale, sha, endpoint)
            if previous:
                old = {r['id']:r for r in previous['items']}; new = {r['id']:r for r in current['items']}
                if current['generation'] < previous['generation'] or not old.keys() <= new.keys(): raise ValueError('Corrections reçues incomplètes ou anciennes.')
                for ident, row in old.items():
                    if new[ident]['revision'] < row['revision'] or (new[ident]['revision'] == row['revision'] and new[ident]['name'] != row['name']):
                        raise ValueError('Révision de correction incohérente.')
            body = dict(format='wcf-reference-corrections-v1', endpoint=endpoint, locale=locale, source_sha256=sha, **current)
            if body != previous:
                payload = dict(body, sha256=hashlib.sha256(_json(body)).hexdigest()); raw = _json(payload)
                if len(raw)>MAX_CACHE: raise ValueError('Cache de corrections trop volumineux.')
                self.folder.mkdir(parents=True, exist_ok=True)
                old_raw = path.read_bytes() if path.exists() else None
                if old_raw is not None:
                    backup = path.with_name(path.name + '.avant-sync-' + uuid.uuid4().hex)
                    with backup.open('xb') as stream: stream.write(old_raw)
                temporary = path.with_name(path.name + '.pending-' + uuid.uuid4().hex)
                with temporary.open('xb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                if (path.read_bytes() if path.exists() else None) != old_raw: raise ValueError('Cache modifié pendant la synchronisation.')
                os.replace(temporary,path)
                self._read(locale,endpoint)
            return dict(status='online', items=current['items'], error=None)
        except (OSError, ValueError) as error:
            if previous is None: raise
            return dict(status='cached', items=previous['items'], error=str(error))
