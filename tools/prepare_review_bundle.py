"""Build an offline review bundle; it is not a WoWCartographe update file."""
import argparse
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from tools.native_additions import parse_additions
from tools.stage_native_additions import APP_ID, SCHEMA_VERSION

MAX_REVIEW_BYTES = 256 * 1024
MAX_SELECTION = 2000
MAX_SNAPSHOTS = 32
MAX_TOTAL_RAW = 8 * 1024 * 1024
HASH = re.compile(r'\A[0-9a-f]{64}\Z')


def prepare(connection, review):
    """Validate each selected row against its original snapshot before export."""
    if (connection.execute('PRAGMA application_id').fetchone()[0] != APP_ID or
            connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
        raise ValueError('Base de préparation non reconnue')
    if not isinstance(review, dict) or set(review) != {'format', 'accepted'} or review['format'] != 'wcf-local-review-v1':
        raise ValueError('Décisions locales non reconnues')
    selections = review['accepted']
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_SELECTION:
        raise ValueError('Nombre de sélections non accepté')
    keys = []
    for item in selections:
        if (not isinstance(item, dict) or set(item) != {'snapshot_sha256', 'ordinal'} or
                not isinstance(item['snapshot_sha256'], str) or not HASH.fullmatch(item['snapshot_sha256']) or
                type(item['ordinal']) is not int or item['ordinal'] < 1):
            raise ValueError('Sélection invalide')
        keys.append((item['snapshot_sha256'], item['ordinal']))
    if len(set(keys)) != len(keys) or len({key[0] for key in keys}) > MAX_SNAPSHOTS:
        raise ValueError('Sélection répétée ou trop de fichiers')
    results = []
    cache = {}
    total = 0
    for digest, ordinal in sorted(keys):
        if digest not in cache:
            snapshot = connection.execute(
                'SELECT id,raw_file,record_count FROM snapshots WHERE sha256=?',
                (bytes.fromhex(digest),)).fetchone()
            if not snapshot or not isinstance(snapshot[1], bytes):
                raise ValueError('Fichier source introuvable')
            snapshot_id, data, count = snapshot
            total += len(data)
            if total > MAX_TOTAL_RAW or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Fichier source incohérent ou budget dépassé')
            parsed = parse_additions(data)
            if count != len(parsed.records):
                raise ValueError('Nombre de lignes incohérent')
            cache[digest] = (snapshot_id, parsed)
        snapshot_id, parsed = cache[digest]
        if ordinal > len(parsed.records):
            raise ValueError('Annotation introuvable')
        original = b'|'.join(parsed.records[ordinal - 1].fields)
        stored = connection.execute('SELECT raw_record FROM records WHERE snapshot_id=? AND ordinal=?',
                                    (snapshot_id, ordinal)).fetchone()
        if not stored or stored[0] != original:
            raise ValueError('Annotation incohérente avec le fichier source')
        results.append({'snapshot_sha256': digest, 'ordinal': ordinal,
                        'record_sha256': hashlib.sha256(original).hexdigest(),
                        'record_base64': base64.b64encode(original).decode('ascii')})
    return {'format': 'wcf-offline-reviewed-bundle-v1', 'native_update': False, 'records': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Base locale de préparation existante')
    parser.add_argument('review', type=Path, help='Décisions locales explicites au format JSON')
    parser.add_argument('output', type=Path, help='Nouveau fichier de sortie hors du dépôt')
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError('La sortie doit être située hors du dépôt')
        with args.review.open('rb') as source:
            raw_review = source.read(MAX_REVIEW_BYTES + 1)
        if len(raw_review) > MAX_REVIEW_BYTES:
            raise ValueError('Décisions trop volumineuses')
        review = json.loads(raw_review)
        uri = args.database.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute('PRAGMA query_only = ON')
            db.execute('BEGIN')
            bundle = prepare(db, review)
        serialized = (json.dumps(bundle, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(serialized)
        print(json.dumps({'records': len(bundle['records']), 'sha256': hashlib.sha256(serialized).hexdigest()}))
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.exit(1, f'Préparation impossible : {exc}\n')


if __name__ == '__main__':
    main()
