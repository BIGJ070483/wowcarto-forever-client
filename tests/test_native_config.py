import hashlib
import unittest
import zlib

from tools.inspect_native_config import MAX_DECODED, MAX_INPUT, inspect


def sample(lines=None):
    if lines is None:
        lines = [b'1200', b'member@example.invalid', b'http://example.invalid/',
                 b'updates/', b'update.php', b'https://example.invalid/upload',
                 b'http://example.invalid/', b'false', b'Example text']
    plain = b'\r\n'.join(lines) + b'\r\n'
    key = hashlib.md5(b'cartokey').hexdigest().encode('ascii')
    return zlib.compress(bytes(value ^ key[i % len(key)] for i, value in enumerate(plain)))


class NativeConfigTests(unittest.TestCase):
    def test_known_synthetic_structure_redacts_content(self):
        data = sample()
        result = inspect(data)
        self.assertEqual(result['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(result['url_schemes_at_lines_3_6_7'], ['http', 'https', 'http'])
        self.assertNotIn('example', str(result))
        self.assertTrue(result['contents_redacted'])

    def test_invalid_or_truncated_zlib_rejected(self):
        for data in (b'garbage', sample()[:-1], sample() + b'extra'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                inspect(data)

    def test_size_limits(self):
        for data in (b'x' * (MAX_INPUT + 1), zlib.compress(b'x' * (MAX_DECODED + 1))):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inspect(data)

    def test_unknown_version_and_malformed_address_rejected(self):
        lines = [b'1199', b'a', b'http://example.invalid', b'b', b'c',
                 b'https://example.invalid', b'http://example.invalid', b'false', b'd']
        with self.assertRaises(ValueError):
            inspect(sample(lines))
        lines[0] = b'1200'
        lines[5] = b'not a url'
        with self.assertRaises(ValueError):
            inspect(sample(lines))


if __name__ == '__main__':
    unittest.main()
