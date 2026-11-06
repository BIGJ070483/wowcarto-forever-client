import unittest

from tools.clone_model import Model, folded


class SearchFieldsTests(unittest.TestCase):
    def setUp(self):
        self.model = Model.__new__(Model)
        self.model.annotations = [
            dict(name='Écureuil', search_name=folded('Écureuil'), comment='près de la banque',
                 category=7, group_id=1, min_level=3, max_level=4),
            dict(name='Banquier', search_name=folded('Banquier'), comment='cherche un écureuil',
                 category=3, group_id=1, min_level=10, max_level=20),
        ]

    def test_name_and_comment_options_select_distinct_records(self):
        self.assertEqual([r['name'] for r in self.model.search('ecureuil')], ['Écureuil'])
        self.assertEqual([r['name'] for r in self.model.search('ecureuil', name=False, comment=True)], ['Banquier'])
        self.assertEqual(len(self.model.search('ecureuil', name=True, comment=True)), 2)

    def test_category_and_level_filters_can_be_used_without_a_term(self):
        rows = self.model.search('', categories={7}, minimum=3, maximum=4)
        self.assertEqual([r['name'] for r in rows], ['Écureuil'])

    def test_geography_properties_and_local_filter_are_combined(self):
        self.model.groups={1:{'continent_id':11},2:{'continent_id':22}}
        self.model.annotations=[
            dict(name='Dedans',search_name='dedans',comment='',category=7,group_id=1,wx=5,wy=5,property_names=['Minerai'],source='personal'),
            dict(name='Bord',search_name='bord',comment='',category=7,group_id=1,wx=10,wy=5,property_names=['Minerai'],source='native'),
            dict(name='Dehors',search_name='dehors',comment='',category=7,group_id=1,wx=11,wy=5,property_names=['Minerai'],source='personal'),
            dict(name='Autre',search_name='autre',comment='',category=7,group_id=2,wx=5,wy=5,property_names=['Minerai'],source='personal'),
        ]
        area=[(1,[(0,0),(10,0),(10,10),(0,10)])]
        rows=self.model.search('',regions=area,continents={11},properties=['Minerai'])
        self.assertEqual([r['name'] for r in rows],['Dedans','Bord'])
        self.assertEqual([r['name'] for r in self.model.search('',regions=area,continents={11},properties=['Minerai'],local_only=True)],['Dedans'])


if __name__ == '__main__':
    unittest.main()
