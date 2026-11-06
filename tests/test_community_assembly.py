import unittest
import base64

from tools.community_assembly import Source, assemble, assemble_snapshot
from tools.native_additions import parse_additions
from tests.test_hosting_http import native_fixture


class CommunityAssemblyTests(unittest.TestCase):
    def test_preserves_bytes_and_provenance_and_coalesces_exact_duplicates(self):
        data, row = native_fixture()
        other = data.replace(b'TEST \xe9', b'AUTRE \xe9').replace(b'|123|', b'|124|')
        result = assemble([Source('z', 2, data), Source('a', 1, other)])
        records = parse_additions(result['payload']).records
        self.assertEqual([b'|'.join(r.fields) for r in records],
                         [row.replace(b'TEST \xe9', b'AUTRE \xe9').replace(b'|123|', b'|124|'), row])
        self.assertEqual([len(item['origins']) for item in result['records']], [2, 2])
        self.assertFalse(result['native_verified'])
        self.assertEqual(result['header_source'], 'a')
        self.assertEqual(result, assemble([Source('a', 1, other), Source('z', 2, data)]))

    def test_rejects_invalid_or_ambiguous_source_identity(self):
        data, _ = native_fixture()
        for inputs in ([], [Source('x', 1, data), Source('x', 2, data)],
                       [Source('x', True, data)], [Source('x', 1, b'invalid')]):
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                assemble(inputs)

    def test_rejects_rows_with_same_opaque_position_and_different_type(self):
        data, _ = native_fixture()
        other = data.replace(b'TEST \xe9', b'AUTRE \xe9').replace(b'|37|', b'|22|')
        with self.assertRaisesRegex(ValueError, 'ambigus'):
            assemble([Source('a', 1, data), Source('b', 1, other)])

    def test_normalizes_position_numbers_and_rejects_unbounded_values(self):
        data, _ = native_fixture()
        leading = data.replace(b'|17|37|123|', b'|017|37|00123|')
        with self.assertRaisesRegex(ValueError, 'ambigus'):
            assemble([Source('a', 1, data), Source('b', 1, leading)])
        for bad in (b'|17|37|65536|', b'|17|37|-1|', b'|17|37|abc|'):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'position'):
                assemble([Source('a', 1, data.replace(b'|17|37|123|', bad))])
        with self.assertRaisesRegex(ValueError, 'Identifiant natif'):
            assemble([Source('a', 1, data.replace(b'|17|37|123|', b'|32768|37|123|'))])

    def test_snapshot_preserves_revision_provenance_and_rejects_corruption(self):
        _, row = native_fixture()
        encoded = lambda value: base64.b64encode(value).decode('ascii')
        snapshot = {'header_source': 'wcf-neutral-v1', 'headers_base64': encoded(b'#v001-id=1-WCF\r\n#1204-ab12'),
                    'items': [{'id': 10, 'import_id': 7, 'ordinal': 3, 'revision': 2, 'raw_base64': encoded(row)}]}
        result = assemble_snapshot(snapshot)
        self.assertEqual(result['snapshot_header_source'], 'wcf-neutral-v1')
        self.assertEqual(result['source_metadata']['10'], {'import_id': 7, 'ordinal': 3})
        self.assertEqual(result['records'][0]['origins'][0]['revision'], 2)
        self.assertEqual(b'|'.join(parse_additions(result['payload']).records[0].fields), row)
        self.assertIsNone(assemble_snapshot({'header_source': None, 'headers_base64': None, 'items': []}))
        for altered in ({**snapshot, 'headers_base64': '%%%INVALID'},
                        {**snapshot, 'items': snapshot['items'] * 2},
                        {**snapshot, 'items': [{**snapshot['items'][0], 'raw_base64': encoded(row + b'\nmalicious')}]}):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                assemble_snapshot(altered)


if __name__ == '__main__':
    unittest.main()
