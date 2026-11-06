"""Prepare a read-only public download of the checked original reference database."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import html
import json
from pathlib import Path
import shutil
import sqlite3

from tools.reference_database import FORMAT


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare(database, directory):
    database, directory = Path(database).resolve(), Path(directory).resolve()
    if not database.is_file():
        raise FileNotFoundError('Base de référence absente.')
    if directory.exists():
        raise FileExistsError('Le dossier de préparation existe déjà.')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Intégrité SQLite incorrecte.')
        if db.execute("SELECT value FROM metadata WHERE key='format'").fetchone() != (FORMAT,):
            raise ValueError('Format de base inattendu.')
        sources = []
        for locale, digest, size, raw, count in db.execute(
                'SELECT locale,sha256,bytes,original_blob,record_count FROM sources ORDER BY locale'):
            if hashlib.sha256(raw).hexdigest() != digest or len(raw) != size:
                raise ValueError('Empreinte native incohérente : ' + locale)
            if db.execute('SELECT count(*) FROM annotations WHERE locale=?', (locale,)).fetchone()[0] != count:
                raise ValueError('Nombre d’annotations incohérent : ' + locale)
            sources.append({'locale': locale, 'source_sha256': digest, 'annotations': count})
        if {source['locale'] for source in sources} != {'Francais', 'US'}:
            raise ValueError('Langues sources attendues absentes.')
    directory.mkdir(parents=True, exist_ok=False)
    archive = directory / 'reference-120.sqlite3.gz'
    with database.open('rb') as source, archive.open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=6, mtime=0) as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
    uncompressed = hashlib.sha256()
    with gzip.open(archive, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            uncompressed.update(block)
    database_sha = hash_file(database)
    if uncompressed.hexdigest() != database_sha:
        raise ValueError('La décompression ne restitue pas la base source.')
    manifest = {
        'format': 'wcf-public-reference-v1',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'database_format': FORMAT,
        'source_version': 'WoWCartographe 1.20',
        'provenance': 'Copie des bases natives françaises et US ; données historiques.',
        'limitations': ['Les données et coordonnées ne sont pas vérifiées pour WoW Forever.',
                        'Ce téléchargement est en lecture seule : les corrections collectives ne sont pas encore disponibles.'],
        'sources': sources,
        'download': {'file': archive.name, 'compression': 'gzip', 'bytes': archive.stat().st_size,
                     'sha256': hash_file(archive), 'uncompressed_bytes': database.stat().st_size,
                     'uncompressed_sha256': database_sha},
    }
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    labels = ''.join(f"<li>{html.escape(s['locale'])} : {s['annotations']:,} annotations</li>" for s in sources)
    page = f'''<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WoWCarto Forever — base de référence</title>
<style>body{{font:16px system-ui;max-width:52rem;margin:4rem auto;padding:0 1rem;line-height:1.55;color:#242424}}code{{overflow-wrap:anywhere}}a{{color:#175298}}</style>
</head><body><h1>Base de référence WoWCarto Forever</h1>
<p>Copie des données historiques de WoWCartographe 1.20, destinée à servir de point de départ au projet.</p>
<ul>{labels}</ul>
<p>Les informations et leurs positions peuvent être erronées ou avoir changé dans WoW Forever.</p>
<p><a href="reference-120.sqlite3.gz">Télécharger la base SQLite compressée</a> ({archive.stat().st_size:,} octets).
Décompressez le fichier avant de l'ouvrir avec un outil SQLite.</p>
<p><a href="manifest.json">Manifeste et empreintes SHA-256</a></p>
<p>Cette version est en lecture seule. Les corrections communautaires ne sont pas encore disponibles en ligne.</p>
</body></html>'''
    (directory / 'index.html').write_text(page, encoding='utf8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.database, args.directory), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
