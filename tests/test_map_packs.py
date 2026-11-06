import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from tools.map_packs import checked_image, create_pack, import_pack, load_pack, local_file, sha256


class MapPacksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'high').mkdir(parents=True)
        Image.new('RGB', (64, 64), 'blue').save(self.source / 'map00.jpg')
        Image.new('RGB', (256, 256), 'green').save(self.source / 'high' / 'map00.jpg')
        self.original = sha256(self.source / 'map00.jpg')

    def pack(self):
        return create_pack(self.source, self.root / 'pack', pack_id='test-v1', title='Test', mode='Modern',
                           provenance='Synthetic fixture', version='Fixture 1', limitations='No real geography')

    def test_copy_import_and_preservation(self):
        manifest = self.pack()
        data = load_pack(manifest, verify=True)
        self.assertEqual(len(data['maps']), 1)
        self.assertEqual(data['maps'][0]['high']['width'], 256)
        result = import_pack(manifest, self.root / 'library')
        self.assertEqual(load_pack(result, verify=True), data)
        self.assertEqual(sha256(self.source / 'map00.jpg'), self.original)
        with self.assertRaises(FileExistsError):
            import_pack(manifest, self.root / 'library')
        self.assertEqual(load_pack(result, verify=True), data)

    def test_missing_and_tampered_images_are_not_replaced(self):
        manifest = self.pack()
        data = load_pack(manifest)
        row = data['maps'][0]
        hd = local_file(manifest.parent, row['high']['path'])
        hd.unlink()
        with self.assertRaisesRegex(ValueError, 'manquante'):
            checked_image(manifest.parent, row['high'])
        self.assertEqual(checked_image(manifest.parent, row['standard']).size, (64, 64))
        standard = local_file(manifest.parent, row['standard']['path'])
        standard.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'modifiée'):
            load_pack(manifest, verify=True)
        with self.assertRaises(ValueError):
            import_pack(manifest, self.root / 'library')
        self.assertFalse((self.root / 'library').exists())

    def test_paths_and_dimensions_are_bounded(self):
        manifest = self.pack()
        for path in ('../outside.jpg', '/absolute.jpg', 'C:/outside.jpg', 'images\\outside.jpg'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                local_file(manifest.parent, path)
        data = load_pack(manifest)
        data['maps'][0]['standard']['width'] = 10000000
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            load_pack(manifest)

    def test_original_cannot_be_output(self):
        with self.assertRaises(ValueError):
            create_pack(self.source, self.source / 'nested', pack_id='no', title='no', mode='Legacy',
                        provenance='no', version='no', limitations='no')
        self.assertEqual(sha256(self.source / 'map00.jpg'), self.original)


if __name__ == '__main__':
    unittest.main()
