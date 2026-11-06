"""Private SQLite annotation overlays for the reconstructed client.

Native databases are never opened here. ``source_ref`` identifies an immutable
native annotation overridden by a local note. A deleted override is a tombstone:
call ``notes(include_deleted=True)`` when deciding which native rows to hide.
All successful changes append a recoverable revision inside one transaction.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


FORMAT = 'wcf-clone-notes-v1'
MAX_EXPORT = 16 * 1024 * 1024
FIELDS = ('name', 'comment', 'category', 'map_id', 'x', 'y', 'min_level', 'max_level',
          'tags', 'flags', 'source_ref')


class ConflictError(ValueError):
    """The requested change does not match the current local revision."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _payload(data):
    if not isinstance(data, dict) or set(data) - set(FIELDS):
        raise ValueError('Champs d’annotation non reconnus.')
    values = dict(name='', comment='', category=37, map_id=0, x=0, y=0,
                  min_level=0, max_level=0, tags='', flags='', source_ref=None)
    values.update(data)
    for field, limit in (('name', 200), ('comment', 65536), ('tags', 4096), ('flags', 4096)):
        value = values[field]
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError('Texte invalide : ' + field)
    if not values['name'].strip():
        raise ValueError('Le nom est obligatoire.')
    for field, maximum in (('category', 65535), ('map_id', 65535), ('x', 65535),
                           ('y', 65535), ('min_level', 65535), ('max_level', 65535)):
        if type(values[field]) is not int or not 0 <= values[field] <= maximum:
            raise ValueError('Valeur numérique invalide : ' + field)
    if values['min_level'] and values['max_level'] and values['min_level'] > values['max_level']:
        raise ValueError('La plage de niveaux est inversée.')
    ref = values['source_ref']
    if ref is not None and (not isinstance(ref, str) or not ref or len(ref) > 512 or '\x00' in ref):
        raise ValueError('Référence native invalide.')
    return values


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=5000')
        with self.db:
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS clone_meta(version INTEGER NOT NULL);
                INSERT INTO clone_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM clone_meta);
                CREATE TABLE IF NOT EXISTS clone_notes(
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision > 0),
                    deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
                    payload TEXT NOT NULL, source_ref TEXT UNIQUE,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS clone_note_history(
                    note_id TEXT NOT NULL REFERENCES clone_notes(id),
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
                    payload TEXT NOT NULL, changed_at TEXT NOT NULL,
                    PRIMARY KEY(note_id, revision));
            ''')
        versions = [row[0] for row in self.db.execute('SELECT version FROM clone_meta')]
        if versions != [1]:
            self.db.close()
            raise ValueError('Version de base locale non prise en charge.')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _decode(row):
        return dict(json.loads(row['payload']), id=row['id'], revision=row['revision'],
                    deleted=bool(row['deleted']), created_at=row['created_at'], updated_at=row['updated_at'])

    def get_note(self, note_id):
        row = self.db.execute('SELECT * FROM clone_notes WHERE id=?', (note_id,)).fetchone()
        if row is None:
            raise KeyError('Annotation locale introuvable.')
        return self._decode(row)

    def notes(self, include_deleted=False):
        sql = 'SELECT * FROM clone_notes' + ('' if include_deleted else ' WHERE deleted=0') + ' ORDER BY created_at,id'
        return [self._decode(row) for row in self.db.execute(sql)]

    def history(self, note_id):
        self.get_note(note_id)
        return [dict(json.loads(row['payload']), revision=row['revision'], deleted=bool(row['deleted']),
                     changed_at=row['changed_at']) for row in self.db.execute(
                         'SELECT * FROM clone_note_history WHERE note_id=? ORDER BY revision', (note_id,))]

    def _insert(self, note_id, payload, revision=1, deleted=False, created=None, updated=None):
        created, updated = created or _now(), updated or _now()
        self.db.execute('INSERT INTO clone_notes VALUES(?,?,?,?,?,?,?)',
                        (note_id, revision, int(deleted), _json(payload), payload['source_ref'], created, updated))
        self.db.execute('INSERT INTO clone_note_history VALUES(?,?,?,?,?)',
                        (note_id, revision, int(deleted), _json(payload), updated))

    def add_note(self, data):
        payload = _payload(data)
        note_id = str(uuid.uuid4())
        try:
            with self.db:
                self._insert(note_id, payload)
        except sqlite3.IntegrityError as error:
            raise ConflictError('Une annotation locale existe déjà pour cette référence native.') from error
        return note_id

    def _change(self, note_id, patch, deleted, expected_revision=None):
        # BEGIN IMMEDIATE makes the read/version check and write indivisible even
        # when a second client opens this same personal store.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current = self.get_note(note_id)
            if expected_revision is not None and current['revision'] != expected_revision:
                raise ConflictError('L’annotation a changé ; recharger sa dernière version.')
            before = {field: current[field] for field in FIELDS}
            if not isinstance(patch, dict) or set(patch) - set(FIELDS):
                raise ValueError('Modification invalide.')
            payload = _payload(dict(before, **patch))
            if payload['source_ref'] != before['source_ref']:
                raise ValueError('La référence native d’une annotation est immuable.')
            state = current['deleted'] if deleted is None else deleted
            if type(state) is not bool:
                raise ValueError('État d’annotation invalide.')
            if payload == before and state == current['deleted']:
                return current
            revision, stamp = current['revision'] + 1, _now()
            self.db.execute('UPDATE clone_notes SET revision=?,deleted=?,payload=?,updated_at=? WHERE id=?',
                            (revision, int(state), _json(payload), stamp, note_id))
            self.db.execute('INSERT INTO clone_note_history VALUES(?,?,?,?,?)',
                            (note_id, revision, int(state), _json(payload), stamp))
        return self.get_note(note_id)

    def update_note(self, note_id, data, expected_revision=None):
        return self._change(note_id, data, None, expected_revision)

    def delete_note(self, note_id, expected_revision=None):
        return self._change(note_id, {}, True, expected_revision)

    def restore_note(self, note_id, revision=None, expected_revision=None):
        payload = {}
        if revision is not None:
            row = self.db.execute('SELECT payload FROM clone_note_history WHERE note_id=? AND revision=?',
                                  (note_id, revision)).fetchone()
            if row is None:
                raise KeyError('Version locale introuvable.')
            payload = json.loads(row['payload'])
        return self._change(note_id, payload, False, expected_revision)

    def set_override(self, source_ref, data, hidden=False):
        if type(hidden) is not bool or not isinstance(source_ref, str) or not source_ref:
            raise ValueError('État de masquage invalide.')
        if not isinstance(data, dict):
            raise ValueError('Modification invalide.')
        payload = _payload(dict(data, source_ref=source_ref))
        row = self.db.execute('SELECT id FROM clone_notes WHERE source_ref=?', (source_ref,)).fetchone()
        if row is None:
            note_id = str(uuid.uuid4())
            try:
                with self.db:
                    self._insert(note_id, payload, deleted=hidden)
            except sqlite3.IntegrityError as error:
                raise ConflictError('Une autre modification vient de créer cette référence native.') from error
            return self.get_note(note_id)
        return self._change(row['id'], payload, hidden)

    def export_notes(self, path):
        path = Path(path)
        notes = []
        with self.db:
            self.db.execute('BEGIN')
            for note in self.notes(include_deleted=True):
                notes.append(dict(note, history=self.history(note['id'])))
        raw = (_json(dict(format=FORMAT, notes=notes)) + '\n').encode('utf-8')
        if len(raw) > MAX_EXPORT:
            raise ValueError('Export supérieur à 16 Mio.')
        if path.resolve() == self.path.resolve():
            raise ValueError('L’export ne peut pas remplacer la base locale.')
        with path.open('xb') as stream:
            stream.write(raw)
        return dict(path=str(path), notes=len(notes), sha256=hashlib.sha256(raw).hexdigest())

    def _backup(self):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = self.path.with_name(self.path.name + '.avant-import-' + stamp + '.sqlite3')
        # Reserve the name exclusively before asking SQLite to write its snapshot.
        with path.open('xb'):
            pass
        with closing(sqlite3.connect(path)) as backup:
            self.db.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('La sauvegarde avant import n’est pas intègre.')
        return str(path)

    def import_notes(self, path):
        path = Path(path)
        if path.stat().st_size > MAX_EXPORT:
            raise ValueError('Import supérieur à 16 Mio.')
        with path.open('rb') as stream:
            raw = stream.read(MAX_EXPORT + 1)
        if len(raw) > MAX_EXPORT:
            raise ValueError('Import supérieur à 16 Mio.')
        data = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(data, dict) or data.get('format') != FORMAT or not isinstance(data.get('notes'), list):
            raise ValueError('Format d’export non reconnu.')
        if len(data['notes']) > 50000:
            raise ValueError('Trop d’annotations à importer.')
        prepared, ids, refs = [], set(), set()
        for item in data['notes']:
            if not isinstance(item, dict):
                raise ValueError('Annotation importée invalide.')
            ident, revision, deleted = item.get('id'), item.get('revision'), item.get('deleted')
            try:
                if str(uuid.UUID(ident)) != ident:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError('Identifiant local invalide.') from error
            if ident in ids or type(revision) is not int or not 1 <= revision <= 1000000 or type(deleted) is not bool:
                raise ValueError('Identifiant répété ou révision invalide.')
            ids.add(ident)
            payload = _payload({field: item[field] for field in FIELDS if field in item})
            ref = payload['source_ref']
            if ref is not None:
                if ref in refs:
                    raise ValueError('Référence native répétée dans l’import.')
                refs.add(ref)
            for field in ('created_at', 'updated_at'):
                if not isinstance(item.get(field), str) or len(item[field]) > 100:
                    raise ValueError('Horodatage local invalide.')
            history = item.get('history')
            if not isinstance(history, list) or not history:
                raise ValueError('Historique local absent.')
            parsed_history, seen = [], set()
            for version in history:
                if not isinstance(version, dict):
                    raise ValueError('Historique local invalide.')
                number, state = version.get('revision'), version.get('deleted')
                if type(number) is not int or not 1 <= number <= revision or number in seen or type(state) is not bool:
                    raise ValueError('Révision historique invalide.')
                seen.add(number)
                old = _payload({field: version[field] for field in FIELDS if field in version})
                stamp = version.get('changed_at')
                if old['source_ref'] != ref or not isinstance(stamp, str) or len(stamp) > 100:
                    raise ValueError('Référence ou horodatage historique invalide.')
                parsed_history.append((number, state, old, stamp))
            latest = max(parsed_history, key=lambda entry: entry[0])
            if latest[:3] != (revision, deleted, payload):
                raise ValueError('La dernière révision ne correspond pas à l’annotation.')
            prepared.append((ident, revision, deleted, payload, item['created_at'], item['updated_at'], parsed_history))
        backup = self._backup() if self.db.execute('SELECT 1 FROM clone_notes LIMIT 1').fetchone() else None
        added, skipped = 0, 0
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                for ident, revision, deleted, payload, created, updated, history in prepared:
                    existing = self.db.execute('SELECT * FROM clone_notes WHERE id=?', (ident,)).fetchone()
                    if existing:
                        if json.loads(existing['payload']) == payload and bool(existing['deleted']) == deleted:
                            skipped += 1
                            continue
                        raise ConflictError('L’import contient une version différente d’une annotation locale existante.')
                    if payload['source_ref'] is not None and self.db.execute(
                        'SELECT 1 FROM clone_notes WHERE source_ref=?', (payload['source_ref'],)).fetchone():
                        raise ConflictError('Une autre annotation locale modifie déjà cette référence native.')
                    self._insert(ident, payload, revision, deleted, created, updated)
                    for number, state, old, stamp in history:
                        if number != revision:
                            self.db.execute('INSERT INTO clone_note_history VALUES(?,?,?,?,?)',
                                            (ident, number, int(state), _json(old), stamp))
                    added += 1
        except sqlite3.IntegrityError as error:
            raise ConflictError('Import incohérent ; aucune annotation n’a été modifiée.') from error
        return dict(added=added, skipped=skipped, backup=backup)
