from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.reference_database import FORMAT, ReferenceIndex, fold


class ReferenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'reference.sqlite3'
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE sources(locale TEXT PRIMARY KEY,sha256 TEXT NOT NULL);
              CREATE TABLE annotations(rowid INTEGER PRIMARY KEY,locale TEXT,native_id INTEGER,
                                       name_folded TEXT,comment_folded TEXT);
              CREATE VIRTUAL TABLE annotation_search USING fts5(name_folded,comment_folded,
                   content='annotations',content_rowid='rowid',tokenize='trigram');
            ''')
            db.execute('INSERT INTO metadata VALUES(?,?)', ('format', FORMAT))
            db.executemany('INSERT INTO sources VALUES(?,?)', [('Francais', 'f' * 64), ('US', 'e' * 64)])
            db.executemany('INSERT INTO annotations(locale,native_id,name_folded,comment_folded) VALUES(?,?,?,?)', [
                ('Francais', 1, fold('Écureuil de forêt'), fold('près de la banque')),
                ('Francais', 2, fold('Banquier'), fold('un écureuil se trouve ici')),
                ('US', 1, 'squirrel', 'forest'),
            ])
            db.execute("INSERT INTO annotation_search(annotation_search) VALUES('rebuild')")
            db.commit()

    def test_name_comments_multiword_and_locale(self):
        idx = ReferenceIndex(self.path, 'Francais', 'f' * 64)
        self.addCleanup(idx.close)
        self.assertEqual(idx.candidates(['ecureuil']), {1, 2})
        self.assertEqual(idx.candidates(['ecureuil', 'banque']), {1})
        self.assertEqual(idx.candidates(['forest']), set())
        self.assertIsNone(idx.candidates(['de']))

    def test_mismatched_source_rejected(self):
        with self.assertRaises(ValueError):
            ReferenceIndex(self.path, 'Francais', '0' * 64)


if __name__ == '__main__':
    unittest.main()
