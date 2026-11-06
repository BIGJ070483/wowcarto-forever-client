import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.clone_app import Application
from tools.clone_annotation_dialog import local_fields
from tools.clone_store import Store, ConflictError


class AnnotationEditorTests(unittest.TestCase):
    def test_native_metadata_and_coordinates_survive_local_override(self):
        native=dict(name='Point',comment='Description',category=7,map_id=17,x=123,y=456,
                    min_level=10,max_level=20,property_names=['Herbe','Rare'],flags=(1,2,3))
        fields=local_fields(native)
        self.assertEqual((fields['map_id'],fields['x'],fields['y']),(17,123,456))
        self.assertEqual(fields['tags'],'Herbe; Rare')
        self.assertEqual(json.loads(fields['flags']),[1,2,3])
        with tempfile.TemporaryDirectory() as d, Store(Path(d)/'notes.sqlite3') as store:
            note=store.set_override('native:reference120:Francais:1',fields)
            changed=dict(fields,name='Correction',comment='Autre description',min_level=11)
            store.update_note(note['id'],changed,expected_revision=note['revision'])
            with self.assertRaises(ConflictError):
                store.update_note(note['id'],{'comment':'Ancienne version'},expected_revision=note['revision'])
            saved=store.get_note(note['id'])
            self.assertEqual((saved['name'],saved['comment'],saved['x'],saved['y']),('Correction','Autre description',123,456))
            self.assertEqual(len(store.history(note['id'])),2)
            self.assertEqual(native['name'],'Point')

    def test_existing_note_flags_are_not_reencoded(self):
        row=dict(name='Local',map_id=1,x=2,y=3,tags='A; B',flags='{"opaque":1}')
        fields=local_fields(row)
        self.assertEqual(fields['flags'],'{"opaque":1}')
        self.assertEqual(fields['tags'],'A; B')

    def test_hiding_is_revision_checked_and_reversible(self):
        with tempfile.TemporaryDirectory() as d, Store(Path(d)/'notes.sqlite3') as store:
            note_id=store.add_note(dict(name='Local',map_id=1,x=2,y=3,source_ref='personal:reference120:Francais:test'))
            app=Application.__new__(Application); app.can_edit=lambda:True; app.store=store
            app.tr=lambda text:text; app.root=Mock(); app.status=Mock(); app.refresh_local_annotations=Mock()
            with patch('tools.clone_app.messagebox.askyesno',return_value=True):
                app.hide_local_note({'note_id':note_id,'revision':1})
            self.assertTrue(store.get_note(note_id)['deleted'])
            self.assertEqual(store.get_note(note_id)['revision'],2)
            store.restore_note(note_id,expected_revision=2)
            self.assertFalse(store.get_note(note_id)['deleted'])
            self.assertEqual(len(store.history(note_id)),3)

if __name__=='__main__': unittest.main()
