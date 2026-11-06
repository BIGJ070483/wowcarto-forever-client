"""Download-only community snapshots for the reconstructed client.

The server's stable annotation IDs are kept even when positions coincide. The
native-client assembly/collision heuristic is deliberately not used by this
independent renderer. Network access happens only on an explicit ``sync`` call.
Local SHA-256 checks detect cache corruption; they are not server signatures.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import ssl
from urllib.parse import parse_qs, urlsplit, urlunsplit
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, Request, build_opener
import uuid

from tools.community_transport import MAX_RESPONSE, NoRedirect, SNAPSHOT_URL
from tools.native_additions import parse_additions

CACHE_FORMAT = 'wcf-clone-community-cache-v1'
HEADERS = b'#v001-id=1-WCF\r\n#1204-ab12'
MAX_AGGREGATE = 96 * 1024 * 1024
MAX_CACHE = MAX_AGGREGATE + 65536


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def snapshot_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError('Adresse communautaire invalide.')
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.fragment or any(ord(char) <= 32 for char in endpoint)):
        raise ValueError('La synchronisation nécessite une adresse HTTPS sans identifiants intégrés.')
    try:
        parsed.port
    except ValueError as error:
        raise ValueError('Port HTTPS invalide.') from error
    query = parse_qs(parsed.query, keep_blank_values=True)
    if 'action' in query and query['action'] != ['community_snapshot']:
        raise ValueError('Cette adresse ne désigne pas un instantané communautaire.')
    suffix = parsed.query if 'action' in query else parsed.query + ('&' if parsed.query else '') + 'action=community_snapshot'
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', suffix, ''))


def _base64(value):
    if not isinstance(value, str) or len(value) > MAX_RESPONSE:
        raise ValueError('Données communautaires encodées invalides.')
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('Données communautaires encodées invalides.') from error


def _uint(field, maximum=65535):
    if not field or len(field) > 10 or not field.isdigit() or int(field) > maximum:
        raise ValueError('Champ numérique communautaire non reconnu.')
    return int(field)


def decode_snapshot(snapshot):
    """Decode the actual bounded service schema, without native deduplication."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('items'), list):
        raise ValueError('Instantané communautaire invalide.')
    items = snapshot['items']
    if not items:
        if snapshot.get('header_source') is not None or snapshot.get('headers_base64') is not None:
            raise ValueError('Instantané vide incohérent.')
        return []
    if (len(items) > 100000 or snapshot.get('header_source') != 'wcf-neutral-v1' or
            _base64(snapshot.get('headers_base64')) != HEADERS):
        raise ValueError('Instantané communautaire incompatible.')
    result, seen, encoded = [], set(), len(snapshot['headers_base64'])
    for item in items:
        if not isinstance(item, dict) or any(type(item.get(key)) is not int or not 1 <= item[key] <= 2**63-1
                                             for key in ('id', 'import_id', 'ordinal', 'revision')):
            raise ValueError('Identité ou révision communautaire invalide.')
        if item['id'] in seen:
            raise ValueError('Identifiant communautaire répété.')
        seen.add(item['id'])
        raw = _base64(item.get('raw_base64'))
        encoded += len(item['raw_base64'])
        if encoded > MAX_AGGREGATE or b'\r' in raw or b'\n' in raw:
            raise ValueError('Instantané trop volumineux ou ligne invalide.')
        parsed = parse_additions(HEADERS + b'\r\n' + raw + b'\r\n')
        if len(parsed.records) != 1:
            raise ValueError('Une ligne communautaire doit représenter une annotation.')
        fields = parsed.records[0].fields
        text = lambda value: value.decode('cp1252', errors='replace')
        source_ref = 'community:' + str(item['id'])
        result.append(dict(
            id=source_ref, source_ref=source_ref, source='community', revision=item['revision'],
            name=text(fields[0]), map_id=_uint(fields[2]), category=_uint(fields[3]),
            x=_uint(fields[4]), y=_uint(fields[5]), min_level=_uint(fields[6]), max_level=_uint(fields[7]),
            comment='\n'.join(text(value) for value in fields[15:]), tags=text(fields[12]),
            flags=json.dumps({str(index+1): text(fields[index]) for index in (1, 8, 9, 10, 11, 13, 14)}, ensure_ascii=False),
            raw_base64=item['raw_base64'], raw_sha256=hashlib.sha256(raw).hexdigest(),
            provenance=dict(import_id=item['import_id'], ordinal=item['ordinal'], revision=item['revision'],
                            schema='native-v001', encoding='cp1252',
                            unknown_fields_preserved=True, geographic_mapping_verified=False),
        ))
    return sorted(result, key=lambda row: int(row['id'].split(':')[1]))


def fetch_snapshot(endpoint=SNAPSHOT_URL):
    """Fetch a consistent paginated snapshot, with compatibility for older servers."""
    endpoint = snapshot_endpoint(endpoint)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    def fetch(url):
        request = Request(url, headers={'Accept':'application/json','Accept-Encoding':'identity'})
        with opener.open(request, timeout=15) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise ValueError('Réponse communautaire non reconnue.')
            length=response.headers.get('Content-Length')
            if length is not None and (not length.isascii() or not length.isdigit() or int(length)>MAX_RESPONSE):
                raise ValueError('Réponse communautaire trop volumineuse.')
            raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE: raise ValueError('Réponse communautaire trop volumineuse.')
            if length is not None and len(raw)!=int(length): raise ValueError('Réponse communautaire incomplète.')
        try: data=json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError) as error: raise ValueError('Réponse communautaire invalide.') from error
        if not isinstance(data,dict) or data.get('ok') is not True: raise ValueError('Le service refuse cet instantané.')
        return data
    parsed=urlsplit(endpoint)
    base=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,'',''))
    for attempt in range(3):
        items=[]; after=0; generation=None; encoded=0
        try:
            while True:
                params={'action':'community_page','after':after}
                if generation is not None: params['generation']=generation
                try: data=fetch(base+'?'+urlencode(params))
                except HTTPError as error:
                    if error.code!=404 or after: raise
                    data=fetch(endpoint)
                if 'snapshot' in data:
                    if after: raise ValueError('Pagination communautaire incohérente.')
                    snapshot=data['snapshot']; decode_snapshot(snapshot); return snapshot
                page=data.get('page')
                if (not isinstance(page,dict) or type(page.get('generation')) is not int or page['generation']<0 or
                        type(page.get('has_more')) is not bool or not isinstance(page.get('items'),list) or len(page['items'])>100):
                    raise ValueError('Page communautaire invalide.')
                if generation is None: generation=page['generation']
                if page['generation']!=generation: raise ValueError('Pages communautaires incohérentes.')
                rows=page['items']
                if rows:
                    checked={'header_source':'wcf-neutral-v1','headers_base64':base64.b64encode(HEADERS).decode('ascii'),'items':rows}
                    decode_snapshot(checked)
                    ids=[row['id'] for row in rows]
                    if ids!=sorted(ids) or ids[0]<=after: raise ValueError('Pages communautaires non ordonnées.')
                    after=ids[-1]
                    encoded+=sum(len(row['raw_base64']) for row in rows)
                    items.extend(rows)
                if len(items)>100000 or encoded>MAX_AGGREGATE: raise ValueError('Instantané communautaire trop volumineux.')
                if not page['has_more']:
                    snapshot={'header_source':'wcf-neutral-v1' if items else None,
                              'headers_base64':base64.b64encode(HEADERS).decode('ascii') if items else None,'items':items}
                    decode_snapshot(snapshot); return snapshot
                if not rows: raise ValueError('Page communautaire incomplète.')
        except HTTPError as error:
            if error.code!=409 or attempt==2: raise
    raise ValueError('Synchronisation communautaire impossible.')


class CommunityClient:
    def __init__(self, cache_path):
        self.path = Path(cache_path)

    @staticmethod
    def _read(path):
        if path.stat().st_size > MAX_CACHE:
            raise ValueError('Cache communautaire trop volumineux.')
        with path.open('rb') as stream:
            raw = stream.read(MAX_CACHE + 1)
        if len(raw) > MAX_CACHE:
            raise ValueError('Cache communautaire trop volumineux.')
        try:
            record = json.loads(raw.decode('utf-8'))
        except (UnicodeError, ValueError) as error:
            raise ValueError('Cache communautaire illisible.') from error
        if not isinstance(record, dict) or record.get('format') != CACHE_FORMAT:
            raise ValueError('Format de cache communautaire inconnu.')
        body = {key: record.get(key) for key in ('format', 'endpoint', 'fetched_at', 'snapshot')}
        if hashlib.sha256(_json(body)).hexdigest() != record.get('sha256'):
            raise ValueError('Empreinte du cache communautaire incorrecte.')
        if (snapshot_endpoint(record.get('endpoint')) != record['endpoint'] or
                not isinstance(record.get('fetched_at'), str) or len(record['fetched_at']) > 100):
            raise ValueError('Métadonnées de cache invalides.')
        decode_snapshot(record['snapshot'])
        return record

    def cached(self, endpoint=None):
        record = self._read(self.path)
        if endpoint is not None and snapshot_endpoint(endpoint) != record['endpoint']:
            raise ValueError('Le cache appartient à un autre service communautaire.')
        return dict(annotations=decode_snapshot(record['snapshot']), status='cached',
                    fetched_at=record['fetched_at'], endpoint=record['endpoint'], error=None,
                    backup=None, changed=False)

    @staticmethod
    def _check_revisions(previous, incoming):
        old = {row['id']: row for row in decode_snapshot(previous)}
        new = {row['id']: row for row in decode_snapshot(incoming)}
        if not old.keys() <= new.keys():
            raise ValueError('Instantané incomplet : des annotations précédentes ont disparu.')
        for ident, before in old.items():
            after = new[ident]
            if (after['revision'] < before['revision'] or
                    (after['revision'] == before['revision'] and after['raw_sha256'] != before['raw_sha256'])):
                raise ValueError('Révision communautaire obsolète ou incohérente.')

    def _save(self, snapshot, endpoint):
        previous, old_raw = None, None
        if self.path.exists():
            old_raw = self.path.read_bytes()
            if len(old_raw) > MAX_CACHE:
                raise ValueError('Cache existant trop volumineux : conservation manuelle nécessaire.')
            try:
                previous = self._read(self.path)
            except (OSError, ValueError):
                pass
        if previous:
            if previous['endpoint'] != endpoint:
                raise ValueError('Le cache appartient à un autre service communautaire.')
            self._check_revisions(previous['snapshot'], snapshot)
            if previous['snapshot'] == snapshot:
                return None, False
        body = dict(format=CACHE_FORMAT, endpoint=endpoint,
                    fetched_at=datetime.now(timezone.utc).isoformat(), snapshot=snapshot)
        payload = dict(body, sha256=hashlib.sha256(_json(body)).hexdigest())
        raw = _json(payload)
        if len(raw) > MAX_CACHE:
            raise ValueError('Cache communautaire trop volumineux.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if old_raw is not None:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = self.path.with_name(self.path.name + '.avant-sync-' + stamp + '.json')
            with backup.open('xb') as stream:
                stream.write(old_raw)
            if hashlib.sha256(backup.read_bytes()).digest() != hashlib.sha256(old_raw).digest():
                raise ValueError('Sauvegarde communautaire divergente.')
        temporary = self.path.with_name(self.path.name + '.pending-' + uuid.uuid4().hex)
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self._read(temporary)
        if old_raw is not None and self.path.read_bytes() != old_raw:
            raise ValueError('Le cache a changé pendant la synchronisation ; remplacement refusé.')
        if old_raw is None and self.path.exists():
            raise ValueError('Un autre cache vient d’être créé ; remplacement refusé.')
        os.replace(temporary, self.path)
        self._read(self.path)
        return str(backup) if backup else None, True

    def sync(self, endpoint=SNAPSHOT_URL):
        endpoint = snapshot_endpoint(endpoint)
        # An explicitly changed endpoint must never silently reuse another origin.
        if self.path.exists():
            try:
                existing = self._read(self.path)
            except (OSError, ValueError):
                existing = None
            if existing and existing['endpoint'] != endpoint:
                raise ValueError('Utiliser un cache distinct pour ce service communautaire.')
        try:
            snapshot = fetch_snapshot(endpoint)
            backup, changed = self._save(snapshot, endpoint)
            return dict(annotations=decode_snapshot(snapshot), status='online',
                        fetched_at=datetime.now(timezone.utc).isoformat(), endpoint=endpoint,
                        error=None, backup=backup, changed=changed)
        except (OSError, ValueError, HTTPException) as error:
            try:
                result = self.cached(endpoint)
            except (OSError, ValueError):
                raise error
            result['error'] = str(error)
            return result
