import unittest
from unittest.mock import Mock

from tools.clone_app import Application, restore_community_caches
from tools.community_transport import SNAPSHOT_URL


class SyncViewTests(unittest.TestCase):
    def test_reference_sync_preserves_current_view(self):
        app=Application.__new__(Application)
        app.mode=Mock(); app.mode.get.return_value='Référence 1.20'
        app.database=Mock(); app.database.get.return_value='Francais'
        app.model=Mock(); app.model.reference_corrections={}; app.model.geographic_regions.return_value=[]
        app.model.by_group={1:[{'category':7,'name':'Point'}]}
        app.region_native_ids={}; app.tiles=[{'id':1}]; app.group=1; app.map=Mock(); app.status=Mock()
        app.tr=lambda text:text
        app.reference_sync_complete('Francais',{'items':[],'status':'online'})
        app.map.set_scene.assert_called_once_with(app.tiles,[{'category':7,'name':'Point'}],fit=False)

    def test_search_results_can_reach_rows_after_first_500(self):
        app=Application.__new__(Application)
        app.search_matches=[{'name':str(n),'region':'R'} for n in range(1002)]
        app.results_offset=0; app.results_tree=Mock(); app.results_tree.get_children.return_value=[]
        app.results_tree.insert.side_effect=lambda *args,**kwargs: kwargs['text']
        app.results_page=Mock(); app.status=Mock(); app.previous_results=Mock(); app.next_results=Mock(); app.tr=lambda text:text
        app.render_results(); self.assertEqual(len(app.results),500)
        app.move_results(1); self.assertEqual(app.results_offset,500); self.assertIn('999 — R',app.results)
        app.move_results(1); self.assertEqual(app.results_offset,1000); self.assertIn('1001 — R',app.results)
        app.move_results(1); self.assertEqual(app.results_offset,1000)
        app.move_results(-1); self.assertEqual(app.results_offset,500)

    def test_local_edit_refresh_keeps_map_view(self):
        app=Application.__new__(Application)
        app.model=Mock(); app.model.by_group={1:[{'category':7,'name':'Local'}]}
        app.group=1; app.tiles=[{'id':1}]; app.map=Mock()
        app.refresh_local_annotations()
        app.map.set_scene.assert_called_once_with(app.tiles,[{'category':7,'name':'Local'}],fit=False)

    def test_startup_restores_cached_community_and_reports_invalid_cache(self):
        model=Mock(); model.reference_corrections={}
        community=Mock(); community.cached.return_value={'annotations':[{'id':'community:1'}]}
        reference=Mock()
        def cached(locale):
            if locale=='Francais': return [{'id':2,'name':'Corrigé','revision':1}]
            raise ValueError('corrupted')
        reference.cached.side_effect=cached
        errors=restore_community_caches(model,community,reference)
        community.cached.assert_called_once_with(SNAPSHOT_URL)
        self.assertEqual(model.community,[{'id':'community:1'}])
        self.assertEqual(model.reference_corrections['Francais'][2]['name'],'Corrigé')
        self.assertEqual(errors,['US'])

if __name__=='__main__': unittest.main()
