import unittest

from tools.clone_model import Model, folded


class GeographicRegionsTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.__new__(Model)
        self.model.categories = {18: {'name': 'Région'}, 7: {'name': 'Pnj'}}
        self.model.native_prefix = 'native:'
        self.model.tiles = {
            1: {'id': 1, 'group_id': 1, 'x': 0, 'y': 0},
            2: {'id': 2, 'group_id': 2, 'x': 0, 'y': 0},
        }
        self.model.groups = {1: {'name': 'Kalimdor'}, 2: {'name': '[D] Donjon'}}
        def row(ident, name, category, tile):
            return dict(id=ident, name=name, category=category, map_id=tile, x=0, y=0)
        self.model.db = {'annotations': [row(1, 'Azshara', 18, 1), row(2, 'Écureuil', 7, 1),
                                        row(3, '[BG] Arathi', 18, 1), row(4, 'Donjon', 18, 2)]}
        self.model.annotations = [row(5, 'Annotation personnelle', 18, 1)]

    def test_only_native_geographical_rows_are_listed(self):
        self.assertEqual([r['name'] for r in self.model.geographic_regions()], ['Azshara'])

    def test_filter_normalizes_accents_and_case(self):
        self.assertIn(folded('trefonds'), folded('Les Tréfonds'))

    def test_english_database_zone_category_is_geographic(self):
        self.model.categories[18]['name'] = 'Zone'
        self.assertEqual([r['name'] for r in self.model.geographic_regions()], ['Azshara'])


if __name__ == '__main__':
    unittest.main()
