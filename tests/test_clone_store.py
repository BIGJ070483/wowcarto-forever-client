import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.clone_store import ConflictError, Store


class CloneStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'notes.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def note(self, **changes):
        return dict(dict(name='Point de recette', comment='Une note locale', category=37,
                         map_id=17, x=25711, y=35957), **changes)

    def test_persistence_history_conflict_and_soft_delete(self):
        ident = self.store.add_note(self.note())
        first = self.store.get_note(ident)
        updated = self.store.update_note(ident, {'name': 'Révision 2'}, expected_revision=1)
        self.assertEqual(updated['revision'], 2)
        with self.assertRaises(ConflictError):
            self.store.update_note(ident, {'name': 'Écriture obsolète'}, expected_revision=1)
        deleted = self.store.delete_note(ident, expected_revision=2)
        self.assertEqual(deleted['revision'], 3)
        self.assertEqual(self.store.notes(), [])
        restored = self.store.restore_note(ident, revision=1, expected_revision=3)
        self.assertEqual(restored['name'], first['name'])
        self.assertEqual(restored['revision'], 4)
        with Store(self.path) as reopened:
            self.assertEqual(reopened.get_note(ident), restored)
            self.assertEqual(len(reopened.history(ident)), 4)

    def test_native_overrides_have_stable_references_and_tombstones(self):
        native = self.root / 'master.loc'
        native.write_bytes(b'immutable-native-database')
        result = self.store.set_override('base:sha:17', self.note())
        again = self.store.set_override('base:sha:17', self.note(name='Correction locale'), hidden=True)
        self.assertEqual(result['id'], again['id'])
        self.assertTrue(again['deleted'])
        self.assertEqual(len(self.store.notes(include_deleted=True)), 1)
        with self.assertRaises(ValueError):
            self.store.update_note(result['id'], {'source_ref': 'base:different:17'})
        self.store.restore_note(result['id'])
        self.assertEqual(len(self.store.notes()), 1)
        self.assertEqual(native.read_bytes(), b'immutable-native-database')

    def test_import_is_idempotent_preserves_history_and_keeps_export(self):
        ident = self.store.add_note(self.note())
        self.store.update_note(ident, {'comment': 'Deuxième version'})
        export = self.root / 'notes.json'
        self.store.export_notes(export)
        original = export.read_bytes()
        with Store(self.root / 'imported.sqlite3') as imported:
            self.assertEqual(imported.import_notes(export)['added'], 1)
            before = imported.get_note(ident)
            status = imported.import_notes(export)
            self.assertEqual(status['skipped'], 1)
            self.assertEqual(imported.get_note(ident), before)
            self.assertEqual(len(imported.history(ident)), 2)
            with closing(sqlite3.connect(status['backup'])) as backup:
                self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(backup.execute('SELECT COUNT(*) FROM clone_notes').fetchone()[0], 1)
        with self.assertRaises(FileExistsError):
            self.store.export_notes(export)
        self.assertEqual(export.read_bytes(), original)

    def test_invalid_import_or_conflict_cannot_partially_write(self):
        first = self.store.add_note(self.note())
        second = self.store.add_note(self.note(name='Autre point'))
        export = self.root / 'notes.json'
        self.store.export_notes(export)
        data = json.loads(export.read_text(encoding='utf-8'))
        data['notes'][1]['x'] = 70000
        malformed = self.root / 'malformed.json'
        malformed.write_text(json.dumps(data), encoding='utf-8')
        with Store(self.root / 'target.sqlite3') as target:
            with self.assertRaises(ValueError):
                target.import_notes(malformed)
            self.assertEqual(target.notes(include_deleted=True), [])
            second_only = json.loads(export.read_text(encoding='utf-8'))
            second_only['notes'] = [item for item in second_only['notes'] if item['id'] == second]
            subset = self.root / 'second-only.json'
            subset.write_text(json.dumps(second_only), encoding='utf-8')
            target.import_notes(subset)
            target.update_note(second, {'name': 'Correction indépendante'})
            before = target.notes(include_deleted=True)
            with self.assertRaises(ConflictError):
                target.import_notes(export)
            self.assertEqual(target.notes(include_deleted=True), before)
            with self.assertRaises(KeyError):
                target.get_note(first)
        self.assertEqual(self.store.get_note(first)['revision'], 1)

    def test_invalid_edit_does_not_create_a_revision(self):
        ident = self.store.add_note(self.note())
        for patch in ({'x': -1}, {'x': True}, {'name': ''}, {'comment': 'nul\x00'},
                      {'min_level': 20, 'max_level': 10}, {'unexpected': 1}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.update_note(ident, patch)
        self.assertEqual(self.store.get_note(ident)['revision'], 1)
        self.assertEqual(len(self.store.history(ident)), 1)

    def test_second_connection_cannot_overwrite_stale_revision(self):
        ident = self.store.add_note(self.note())
        with Store(self.path) as other:
            observed = other.get_note(ident)
            self.store.update_note(ident, {'name': 'Correction du premier client'}, expected_revision=1)
            with self.assertRaises(ConflictError):
                other.update_note(ident, {'name': 'Correction obsolète'}, expected_revision=observed['revision'])
            self.assertEqual(other.get_note(ident)['name'], 'Correction du premier client')
        self.assertEqual(len(self.store.history(ident)), 2)


if __name__ == '__main__':
    unittest.main()
