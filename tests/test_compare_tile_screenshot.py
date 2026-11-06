import importlib.util
import tempfile
import unittest
from pathlib import Path

from tools.compare_tile_screenshot import analyze


@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'), 'opencv/numpy optional')
class TileScreenshotTests(unittest.TestCase):
    def test_matches_synthetic_translation_and_known_point(self):
        import cv2
        import numpy as np
        rng = np.random.default_rng(42)
        tile = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
        screenshot = np.zeros((320, 320, 3), dtype=np.uint8)
        screenshot[30:286, 20:276] = tile
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / 'screen.png', Path(directory) / 'tile.png'
            cv2.imwrite(str(a), screenshot)
            cv2.imwrite(str(b), tile)
            result = analyze(a, b, (120, 110), (100 * 256, 80 * 256))
            self.assertGreaterEqual(result['inliers'], 8)
            self.assertLess(result['screen_error_pixels'], 1)
            self.assertEqual(result['tile_dimensions'], [256, 256])

    def test_unrelated_blank_images_refused(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'blank.png'
            cv2.imwrite(str(path), np.zeros((256, 256, 3), dtype=np.uint8))
            with self.assertRaises(ValueError):
                analyze(path, path, (10, 10), (0, 0))


if __name__ == '__main__':
    unittest.main()
