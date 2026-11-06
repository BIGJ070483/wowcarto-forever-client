import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.clone_model import Model
from tools.clone_translations import FORMAT, load_translation


class TranslationPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'de.json'
        self.native = {'annotations': [{'id': 7, 'name': 'Forest', 'comment': 'Original', 'x': 120}],
                       'categories': {18: {'id': 18, 'name': 'Zone'}},
                       'groups': [{'id': 1, 'name': 'Kalimdor'}]}
        self.pack = {'format': FORMAT, 'language': 'de', 'source': 'US', 'source_sha256': 'a' * 64,
                     'annotations': {'7': {'name': 'Wald'}}, 'categories': {'18': 'Gebiet'}}

    def write(self):
        self.path.write_text(json.dumps(self.pack), encoding='utf-8')

    def test_overlay_preserves_coordinates_and_source(self):
        self.write()
        translated, coverage = load_translation(self.path, 'de', 'a' * 64, self.native)
        self.assertEqual(translated['annotations'][0], {'id': 7, 'name': 'Wald', 'comment': 'Original', 'x': 120})
        self.assertEqual(self.native['annotations'][0]['name'], 'Forest')
        self.assertEqual(translated['categories'][18]['name'], 'Gebiet')
        self.assertEqual(coverage, {'translated_annotations': 1, 'total_annotations': 1})

    def test_wrong_source_and_unknown_ids_are_rejected(self):
        self.write()
        with self.assertRaises(ValueError):
            load_translation(self.path, 'de', 'b' * 64, self.native)
        self.pack['annotations'] = {'8': {'name': 'Wald'}}
        self.write()
        with self.assertRaises(ValueError):
            load_translation(self.path, 'de', 'a' * 64, self.native)

    def test_duplicate_json_keys_are_rejected(self):
        self.path.write_text('{"format":"a","format":"b"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_translation(self.path, 'de', 'a' * 64, self.native)

    def test_languages_load_lazily_and_only_one_translation_is_cached(self):
        root = Path(self.temp.name)
        folder = root / 'translations'
        folder.mkdir()
        source = root / 'source.loc'
        source.write_bytes(b'source')
        digest = hashlib.sha256(b'source').hexdigest()
        catalog = {'format': 'wcf-clone-catalog-v1', 'sets': [{'id': 'reference120',
                   'databases': {'US': {'path': 'source.loc', 'sha256': digest}}, 'packs': []}]}
        (root / 'catalog.json').write_text(json.dumps(catalog), encoding='utf-8')
        native = {'annotations': [{'id': 7, 'name': 'Forest', 'comment': '', 'x': 120, 'y': 180,
                                   'map_id': 1, 'category': 18}],
                  'categories': {18: {'id': 18, 'name': 'Zone'}},
                  'groups': [{'id': 1, 'name': 'Kalimdor'}],
                  'tiles': [{'id': 1, 'filename': 'map', 'group_id': 1, 'x': 0, 'y': 0}]}
        for language, name in (('de', 'Wald'), ('es', 'Bosque')):
            pack = {'format': FORMAT, 'language': language, 'source': 'US', 'source_sha256': digest,
                    'annotations': {'7': {'name': name}}}
            (folder / (language + '.json')).write_text(json.dumps(pack), encoding='utf-8')
        class EmptyStore:
            def notes(self, **kwargs): return []
        with patch('tools.clone_model.load_database', return_value=native):
            model = Model(root, EmptyStore())
            self.assertEqual(model.available_languages(), ['Francais', 'US', 'de', 'es'])
            self.assertNotIn(('reference120', 'de'), model.loaded)
            model.open('reference120', 'de')
            self.assertEqual(model.search('Wald')[0]['id'], 7)
            model.open('reference120', 'es')
            self.assertEqual(model.search('Bosque')[0]['id'], 7)
            self.assertNotIn(('reference120', 'de'), model.loaded)


if __name__ == '__main__':
    unittest.main()
