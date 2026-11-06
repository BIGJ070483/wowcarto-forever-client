import unittest

from tools.clone_regions import project_historical_records


class HistoricalProjectionTests(unittest.TestCase):
    def test_keeps_historical_points_even_outside_available_tiles(self):
        source = [dict(key='native:1', name='Repère', group_id=1000, category=7,
                       wx=-100000., wy=-100000., region='Kalimdor')]
        projected = list(project_historical_records(source))
        self.assertEqual(len(projected), 1)
        self.assertEqual(projected[0]['key'], 'native:1')
        self.assertTrue(projected[0]['historical_unverified'])
        self.assertEqual(source[0]['wx'], -100000.)


if __name__ == '__main__':
    unittest.main()
