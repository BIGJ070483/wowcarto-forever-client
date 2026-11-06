"""Local, experimental staging of observed additions; no network or native export.

This stores potentially private bytes in a local SQLite database. Never commit or
publish the database. Record order and duplicate rows are preserved per snapshot.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from tools.inspect_native_additions import MAX_BYTES
from tools.native_additions import parse_additions

APP_ID = 0x57434631
SCHEMA_VERSION = 1


def stage(connection, data):
    parsed = parse_additions(data)  # Validate before any changes.
    digest = hashlib.sha256(data).digest()
    with connection:
        cursor = connection.execute(
            'INSERT OR IGNORE INTO snapshots(sha256, raw_file, record_count) VALUES (?, ?, ?)',
            (digest, data, len(parsed.records)))
        inserted = cursor.rowcount == 1
        if inserted:
            snapshot_id = cursor.lastrowid
            connection.executemany(
                'INSERT INTO records(snapshot_id, ordinal, raw_record) VALUES (?, ?, ?)',
                ((snapshot_id, number, b'|'.join(record.fields))
                 for number, record in enumerate(parsed.records, 1)))
    return {'inserted': inserted, 'record_count': len(parsed.records)}


def open_staging(path):
    # A new file must be explicitly named; never overwrite an existing file.
    path = Path(path).absolute()
    created = False
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        created = True
    except FileExistsError:
        pass
    connection = sqlite3.connect(path)
    try:
        if created:
            connection.executescript(f'''
                PRAGMA application_id = {APP_ID};
                PRAGMA user_version = {SCHEMA_VERSION};
                CREATE TABLE snapshots (
                    id INTEGER PRIMARY KEY,
                    sha256 BLOB NOT NULL UNIQUE,
                    raw_file BLOB NOT NULL,
                    record_count INTEGER NOT NULL
                );
                CREATE TABLE records (
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
                    ordinal INTEGER NOT NULL,
                    raw_record BLOB NOT NULL,
                    PRIMARY KEY(snapshot_id, ordinal)
                );
            ''')
        else:
            app_id = connection.execute('PRAGMA application_id').fetchone()[0]
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if app_id != APP_ID or version != SCHEMA_VERSION:
                raise ValueError('Base non reconnue : aucune modification effectuée')
        connection.execute('PRAGMA foreign_keys = ON')
        return connection
    except Exception:
        connection.close()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    try:
        repo_root = Path(__file__).resolve().parents[1]
        if args.database.resolve().is_relative_to(repo_root):
            raise ValueError('La base doit être située hors du dépôt')
        if (args.input.resolve() == args.database.resolve() or
                (args.database.exists() and os.path.samefile(args.input, args.database))):
            raise ValueError('La base et le fichier source doivent être distincts')
        with args.input.open('rb') as source:
            data = source.read(MAX_BYTES + 1)
        parse_additions(data)  # Do not create a database for invalid input.
        with closing(open_staging(args.database)) as db:
            result = stage(db, data)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'Import local impossible : {exc}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
