"""Build and query an indexed, lossless local copy of the original 1.20 databases."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import unicodedata
import uuid

from tools.native_database import load_database

FORMAT = 'wcf-original-reference-sqlite-v1'


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def build(source_root, output):
    """Create a fresh SQLite copy; originals are read only and never overwritten."""
    source_root, output = Path(source_root), Path(output)
    if output.exists():
        raise FileExistsError(f'La base cible existe déjà : {output}')
    catalog = json.loads((source_root / 'catalog.json').read_text(encoding='utf-8'))
    source_set = next(s for s in catalog['sets'] if s['id'] == 'reference120')
    inputs = {}
    for locale in ('Francais', 'US'):
        spec = source_set['databases'][locale]
        path = (source_root / spec['path']).resolve()
        if not path.is_relative_to(source_root.resolve()):
            raise ValueError('Chemin de base source hors du catalogue.')
        raw = path.read_bytes()
        if sha(raw) != spec['sha256']:
            raise ValueError(f'Empreinte source incorrecte : {locale}')
        inputs[locale] = (path, raw, spec['sha256'])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.pending-' + uuid.uuid4().hex)
    summary = {}
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript('''
              PRAGMA foreign_keys=ON;
              CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE sources(locale TEXT PRIMARY KEY, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                                   original_blob BLOB NOT NULL, record_count INTEGER NOT NULL);
              CREATE TABLE annotations(rowid INTEGER PRIMARY KEY, locale TEXT NOT NULL REFERENCES sources(locale),
                 native_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, name TEXT NOT NULL,
                 name_folded TEXT NOT NULL, comment TEXT NOT NULL, comment_folded TEXT NOT NULL,
                 category INTEGER NOT NULL, map_id INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
                 min_level INTEGER NOT NULL, max_level INTEGER NOT NULL, data_json TEXT NOT NULL,
                 UNIQUE(locale,native_id), UNIQUE(locale,ordinal));
              CREATE INDEX idx_annotations_map_category ON annotations(locale,map_id,category);
              CREATE INDEX idx_annotations_category ON annotations(locale,category,native_id);
              CREATE TABLE native_tables(locale TEXT NOT NULL REFERENCES sources(locale), kind TEXT NOT NULL,
                                         native_id INTEGER NOT NULL, data_json TEXT NOT NULL,
                                         PRIMARY KEY(locale,kind,native_id));
              CREATE VIRTUAL TABLE annotation_search USING fts5(name_folded,comment_folded,
                  content='annotations',content_rowid='rowid',tokenize='trigram');
            ''')
            db.execute('INSERT INTO metadata VALUES(?,?)', ('format', FORMAT))
            for locale, (path, raw, digest) in inputs.items():
                native = load_database(path)
                rows = native['annotations']
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?)', (locale, digest, len(raw), raw, len(rows)))
                for kind in ('categories', 'groups', 'tiles', 'continents', 'flags'):
                    values = native[kind].values() if isinstance(native[kind], dict) else native[kind]
                    db.executemany('INSERT INTO native_tables VALUES(?,?,?,?)',
                                   ((locale, kind, item['id'], compact(item)) for item in values))
                db.executemany('''INSERT INTO annotations(locale,native_id,ordinal,name,name_folded,comment,
                    comment_folded,category,map_id,x,y,min_level,max_level,data_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    ((locale, r['id'], i, r['name'], fold(r['name']), r['comment'], fold(r['comment']),
                      r['category'], r['map_id'], r['x'], r['y'], r['min_level'], r['max_level'], compact(r))
                     for i, r in enumerate(rows)))
                summary[locale] = dict(sha256=digest, annotations=len(rows), categories=len(native['categories']),
                                       groups=len(native['groups']), tiles=len(native['tiles']))
            db.execute("INSERT INTO annotation_search(annotation_search) VALUES('rebuild')")
            db.execute("INSERT INTO annotation_search(annotation_search, rank) VALUES('integrity-check', 1)")
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Contrôle d’intégrité SQLite échoué.')
            for locale, (_, raw, digest) in inputs.items():
                found = db.execute('SELECT original_blob,record_count FROM sources WHERE locale=?', (locale,)).fetchone()
                if sha(found[0]) != digest or found[0] != raw or found[1] != db.execute(
                        'SELECT count(*) FROM annotations WHERE locale=?', (locale,)).fetchone()[0]:
                    raise ValueError(f'La copie diffère de la source : {locale}')
            db.execute('PRAGMA optimize')
        # Reserve the final filename without replacing an existing user file.
        with output.open('xb') as destination, temporary.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                destination.write(block)
        with closing(sqlite3.connect(f'{output.resolve().as_uri()}?mode=ro', uri=True)) as check:
            if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Vérification de la copie finale échouée.')
        return summary
    finally:
        if temporary.exists():
            temporary.unlink()


class ReferenceIndex:
    def __init__(self, path, locale, expected_sha):
        self.db = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
        try:
            version = self.db.execute('SELECT value FROM metadata WHERE key=?', ('format',)).fetchone()
            source = self.db.execute('SELECT sha256 FROM sources WHERE locale=?', (locale,)).fetchone()
            if version != (FORMAT,) or source != (expected_sha,):
                raise ValueError('Base indexée incompatible avec la source.')
        except Exception:
            self.db.close()
            raise
        self.locale = locale

    def candidates(self, terms):
        long_terms = [term for term in terms if len(term) >= 3]
        if not long_terms:
            return None
        query = ' AND '.join('"' + term.replace('"', '""') + '"' for term in long_terms)
        return {row[0] for row in self.db.execute('''SELECT a.native_id FROM annotation_search f
            JOIN annotations a ON a.rowid=f.rowid WHERE annotation_search MATCH ? AND a.locale=?''',
                                                   (query, self.locale))}

    def close(self):
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
