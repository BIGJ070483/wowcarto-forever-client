from contextlib import closing
import sqlite3
import unittest

from tests import test_hosting_http as http

FR='f1838d4946221d610fb7f54241bb574670dadb3da4b1bf1398821b640b7bb6b5'
EN='58ae7d6ffd4f3e6a16ef27dc696b2f9c754a1752a1ae774f30ccf7d02637761e'

@unittest.skipUnless(http.PHP, 'PHP with pdo_sqlite required')
class ReferenceCommunityHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): http.HostingHttpTests.setUpClass()
    @classmethod
    def tearDownClass(cls): http.HostingHttpTests.tearDownClass()
    def setUp(self):
        self.h=http.HostingHttpTests('test_closed_service_health_and_no_writes'); self.h.setUp()
        with closing(sqlite3.connect(self.h.database)) as db, db:
            db.executescript('''
              INSERT INTO wcf_schema_versions VALUES (3),(4);
              CREATE TABLE wcf_reference_generation(id INTEGER PRIMARY KEY,generation INTEGER NOT NULL);
              INSERT INTO wcf_reference_generation VALUES (1,0);
              CREATE TABLE wcf_reference_edits(locale TEXT,native_id INTEGER,revision INTEGER,name TEXT,PRIMARY KEY(locale,native_id));
              CREATE TABLE wcf_reference_history(locale TEXT,native_id INTEGER,revision INTEGER,name TEXT,PRIMARY KEY(locale,native_id,revision));
            ''')

    def test_edit_version_source_and_generation(self):
        base={'locale':'Francais','source_sha256':FR,'id':'2','revision':'0','name':'Nom corrigé'}
        self.assertEqual(self.h.call('reference_edit',base,csrf=False)[0],403)
        self.assertEqual(self.h.call('reference_edit',dict(base,source_sha256=EN))[0],400)
        self.assertEqual(self.h.call('reference_edit',dict(base,id='999999'))[0],404)
        code, first=self.h.call('reference_edit',base)
        self.assertEqual((code,first['revision']),(200,1))
        self.assertEqual(self.h.call('reference_edit',base)[0],409)
        page=self.h.call('reference_page&locale=Francais&source_sha256='+FR)[1]['page']
        self.assertEqual(page['items'],[{'id':2,'revision':1,'name':'Nom corrigé'}])
        self.assertEqual(page['generation'],1)
        self.assertEqual(self.h.call('reference_page&locale=US&source_sha256='+EN)[1]['page']['items'],[])
        self.assertEqual(self.h.call('reference_page&locale=Francais&source_sha256='+FR+'&generation=0')[0],409)
        second=dict(base,revision='1',name='Deuxième correction')
        self.assertEqual(self.h.call('reference_edit',second)[1]['revision'],2)
        with closing(sqlite3.connect(self.h.database)) as db:
            self.assertEqual(db.execute('SELECT revision,name FROM wcf_reference_history ORDER BY revision').fetchall(),[(1,'Nom corrigé'),(2,'Deuxième correction')])

if __name__=='__main__': unittest.main()
