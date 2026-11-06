"""Build an experimental, byte-preserving v001 community candidate in memory.

This does not install data, perform network I/O, or establish native compatibility.
Headers are copied from the first source; their full semantics remain unknown.
"""
from dataclasses import dataclass
from hashlib import sha256
import base64
import binascii

from tools.native_additions import parse_additions
from tools.inspect_native_additions import MAX_BYTES


@dataclass(frozen=True)
class Source:
    identity: str
    revision: int
    data: bytes


def assemble(sources):
    """Order by source identity; coalesce only byte-identical records.

    Distinct rows are never heuristically merged: two corrections to the same
    logical point may coexist until a real identity/conflict rule is established.
    """
    sources = list(sources)
    if not sources or len(sources) > 2000:
        raise ValueError('Nombre de sources invalide')
    if any(not isinstance(s, Source) or not isinstance(s.identity, str)
           or not s.identity or len(s.identity) > 128 or type(s.revision) is not int
           or s.revision < 1 or not isinstance(s.data, bytes) for s in sources):
        raise ValueError('Source invalide')
    if len({s.identity for s in sources}) != len(sources):
        raise ValueError('Identifiant de source répété')
    if sum(len(s.data) for s in sources) > MAX_BYTES:
        raise ValueError('Budget total des sources dépassé')
    parsed = [(s, parse_additions(s.data)) for s in sorted(sources, key=lambda s: s.identity)]
    records = {}
    opaque_positions = {}
    for source, content in parsed:
        for ordinal, record in enumerate(content.records, 1):
            raw = b'|'.join(record.fields)
            # One native observation lost a row when these fields matched;
            # another showed both after field 5 changed. The complete native
            # identity rule is unknown, so treat matching bytes in fields 3,
            # 5 and 6 as a possible collision, even across different types.
            coordinates = (record.fields[2], record.fields[4], record.fields[5])
            if any(not field.isdigit() or len(field) > 5 or int(field) > 65535
                   for field in coordinates):
                raise ValueError('Champs de position non reconnus ou hors limite')
            position = tuple(int(field) for field in coordinates)
            if position[0] > 32767:
                raise ValueError('Identifiant natif hors limite expérimentale')
            if position in opaque_positions and opaque_positions[position] != raw:
                raise ValueError('Lignes distinctes avec champs natifs ambigus')
            opaque_positions[position] = raw
            records.setdefault(raw, []).append({'source': source.identity,
                                               'revision': source.revision,
                                               'ordinal': ordinal})
    headers = parsed[0][1].headers
    payload = b'\r\n'.join((*headers, *records.keys())) + b'\r\n'
    if len(payload) > MAX_BYTES:
        raise ValueError('Candidat trop volumineux')
    parse_additions(payload)
    return {'payload': payload, 'sha256': sha256(payload).hexdigest(),
            'records': tuple({'sha256': sha256(row).hexdigest(), 'origins': tuple(origins)}
                             for row, origins in records.items()),
            'header_source': parsed[0][0].identity, 'native_verified': False}


def assemble_snapshot(snapshot):
    """Validate a bounded server snapshot and build a candidate, without I/O."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('items'), list):
        raise ValueError('Instantané invalide')
    items = snapshot['items']
    if not items:
        if snapshot.get('header_source') is not None or snapshot.get('headers_base64') is not None:
            raise ValueError('Instantané vide incohérent')
        return None
    if len(items) > 2000 or snapshot.get('header_source') != 'wcf-neutral-v1':
        raise ValueError('Instantané invalide ou trop volumineux')

    def decode(value):
        if not isinstance(value, str) or len(value) > (MAX_BYTES * 4 // 3 + 8):
            raise ValueError('Données encodées invalides')
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError('Données encodées invalides') from error

    headers = decode(snapshot.get('headers_base64'))
    if headers != b'#v001-id=1-WCF\r\n#1204-ab12':
        raise ValueError('En-têtes invalides')
    sources, metadata = [], {}
    encoded_budget = len(snapshot['headers_base64'])
    for item in items:
        if not isinstance(item, dict) or any(type(item.get(key)) is not int or item[key] < 1
                                             for key in ('id', 'import_id', 'ordinal', 'revision')):
            raise ValueError('Provenance invalide')
        identity = str(item['id'])
        if identity in metadata:
            raise ValueError('Identifiant de source répété')
        if not isinstance(item.get('raw_base64'), str):
            raise ValueError('Données encodées invalides')
        encoded_budget += len(item['raw_base64'])
        if encoded_budget > 6 * 1024 * 1024:
            raise ValueError('Instantané trop volumineux')
        raw = decode(item.get('raw_base64'))
        if b'\r' in raw or b'\n' in raw:
            raise ValueError('Ligne native invalide')
        sources.append(Source(identity, item['revision'], headers + b'\r\n' + raw + b'\r\n'))
        metadata[identity] = {'import_id': item['import_id'], 'ordinal': item['ordinal']}
    result = assemble(sources)
    result['snapshot_header_source'] = snapshot['header_source']
    result['source_metadata'] = metadata
    return result
