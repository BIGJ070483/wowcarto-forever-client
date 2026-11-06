import hashlib
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from tools.prepare_clone_minimaps import render_overview


class MinimapTests(unittest.TestCase):
    def test_overview_preserves_tile_order_and_rejects_changed_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tiles = []
            for x, color in ((0, 'red'), (256, 'green')):
                path = root / f'{x}.png'
                Image.new('RGB', (16, 16), color).save(path)
                raw = path.read_bytes()
                asset = dict(path=path.name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), width=16, height=16)
                tiles.append(dict(x=x, y=0, root=root, preview=asset))
            image, bounds = render_overview(tiles)
            self.assertEqual(bounds, [0, 0, 512, 256])
            self.assertEqual(image.getpixel((25, 25)), (255, 0, 0))
            self.assertEqual(image.getpixel((75, 25)), (0, 128, 0))
            (root / '0.png').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                render_overview(tiles)


if __name__ == '__main__':
    unittest.main()
