import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.clone_model import Model
from tools.clone_reference_community import SOURCES, fetch_reference


class _Store:
    def notes(self, include_deleted=False): return []


class _Index:
    def candidates(self, terms): return set()


class ReferenceCorrectionsTests(unittest.TestCase):
    def setUp(self):
        m=Model.__new__(Model); self.m=m
        m.store=_Store(); m.database_name='Francais'; m.map_set={'id':'reference120'}
        m.db={'source_sha256':SOURCES['Francais'], 'annotations':[dict(id=2,name='Original',map_id=1,x=0,y=0,category=18,comment='')]}
        m.tiles={1:dict(id=1,group_id=1,x=0,y=0)}; m.groups={1:dict(name='Région')}
        m.native_prefix='native:reference120:Francais:'; m.personal_prefix='personal:reference120:Francais:'
        m.reference_corrections={'Francais':{2:dict(id=2,revision=1,name='Correction unique')}}
        m.community=[]; m.index=_Index()

    def test_correction_is_searchable_without_mutating_original_or_other_locale(self):
        self.m.rebuild()
        self.assertEqual(self.m.db['annotations'][0]['name'],'Original')
        self.assertEqual([x['name'] for x in self.m.search('Correction unique')],['Correction unique'])
        self.m.database_name='US'; self.m.rebuild()
        self.assertEqual(self.m.annotations[0]['name'],'Original')

    def test_mismatched_source_never_receives_correction(self):
        self.m.db['source_sha256']='0'*64
        self.m.rebuild()
        self.assertEqual(self.m.annotations[0]['name'],'Original')

    def test_community_local_override_stays_visible_only_in_matching_base(self):
        self.m.map_set['community_coordinate_system']='wowcartographe-120'
        shared=dict(id='community:10',source_ref='community:10',name='Public',map_id=1,x=1,y=2,category=7,comment='')
        local=dict(id='note-1',source_ref='community:10',name='Personnel',map_id=1,x=1,y=2,category=7,comment='',deleted=False)
        self.m.community=[shared]
        self.m.store.notes=lambda include_deleted=False:[local]
        self.m.rebuild()
        self.assertEqual([r['name'] for r in self.m.annotations if r['category']==7],['Personnel'])
        self.m.database_name='US'; self.m.rebuild()
        self.assertEqual([r['name'] for r in self.m.annotations if r['category']==7],[])

    def test_paginated_fetch_restarts_when_generation_changes(self):
        calls=[]
        def request(action):
            calls.append(action)
            if len(calls)==1:
                return {'page':dict(source_sha256=SOURCES['Francais'],generation=1,items=[{'id':2,'revision':1,'name':'A'}],has_more=True)}
            if len(calls)==2:
                raise HTTPError('https://example.invalid',409,'conflict',None,None)
            return {'page':dict(source_sha256=SOURCES['Francais'],generation=2,items=[{'id':2,'revision':2,'name':'B'}],has_more=False)}
        with patch('tools.clone_reference_community._session',return_value=(request,'x',{})):
            result=fetch_reference('Francais',SOURCES['Francais'])
        self.assertEqual(result,{'generation':2,'items':[{'id':2,'revision':2,'name':'B'}]})
        self.assertEqual(len(calls),3)

if __name__=='__main__': unittest.main()
