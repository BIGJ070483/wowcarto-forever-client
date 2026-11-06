import base64
import copy
import hashlib
import unittest
from tools.prepare_native_review_candidate import compose_candidate


class NativeReviewCandidateTests(unittest.TestCase):
    def setUp(self):
        self.row = b'|'.join([b'TEST\xe9', b'', b'17', b'37', b'123', b'456'] + [b''] * 9 + [b'comment'])
        self.original = b'#v001-id=1-fixture\r\n#1204-ab12\r\n' + self.row + b'\r\n' + self.row
        self.selection = {'format': 'wcf-server-review-v1', 'native_update': False, 'records': [
            {'ordinal': n, 'event_id': n, 'source_sha256': hashlib.sha256(self.original).hexdigest(),
             'sha256': hashlib.sha256(self.row).hexdigest(), 'raw_base64': base64.b64encode(self.row).decode()}
            for n in (1, 2)]}

    def test_exact_bytes_headers_endings_and_duplicate_records(self):
        self.assertEqual(compose_candidate(self.original, self.selection), self.original)
        self.selection['records'].reverse()
        self.assertEqual(compose_candidate(self.original, self.selection), self.original)
        self.selection['records'] = self.selection['records'][:1]
        self.assertEqual(compose_candidate(self.original, self.selection), self.original.splitlines(keepends=True)[0] + self.original.splitlines(keepends=True)[1] + self.row)

    def test_cross_source_duplicate_position_and_tampering_are_refused(self):
        for key, value in [('source_sha256', 'a' * 64), ('sha256', 'b' * 64),
                           ('raw_base64', '!!!'), ('ordinal', 3), ('event_id', 0)]:
            selection = copy.deepcopy(self.selection)
            selection['records'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                compose_candidate(self.original, selection)
        self.selection['records'].append(self.selection['records'][0])
        with self.assertRaises(ValueError): compose_candidate(self.original, self.selection)
