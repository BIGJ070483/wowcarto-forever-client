"""Compare a screenshot to a local tile and assess an explicitly chosen scale.

Research-only: image matching can fail or select incorrect visual features. The
result is evidence for this screenshot, not proof of a universal coordinate format.
Requires optional opencv-python and numpy; images remain local.
"""
import argparse
import json
from pathlib import Path


def analyze(screenshot, tile, screen_point, raw_point, units_per_pixel=256):
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ValueError('opencv-python et numpy sont nécessaires') from exc
    if units_per_pixel <= 0:
        raise ValueError('Échelle invalide')
    screen = cv2.imdecode(np.fromfile(screenshot, dtype=np.uint8), cv2.IMREAD_COLOR)
    image = cv2.imdecode(np.fromfile(tile, dtype=np.uint8), cv2.IMREAD_COLOR)
    if screen is None or image is None:
        raise ValueError('Image non lisible')
    sx, sy = screen_point
    if not (0 <= sx < screen.shape[1] and 0 <= sy < screen.shape[0]):
        raise ValueError('Point hors capture')
    sift = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.015)
    ks, ds = sift.detectAndCompute(screen, None)
    kt, dt = sift.detectAndCompute(image, None)
    if ds is None or dt is None or len(dt) < 2:
        raise ValueError('Pas assez de détails visuels')
    pairs = cv2.BFMatcher().knnMatch(ds, dt, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .72 * pair[1].distance]
    if len(good) < 8:
        raise ValueError('Pas assez de correspondances visuelles')
    src = np.float32([ks[m.queryIdx].pt for m in good])
    dst = np.float32([kt[m.trainIdx].pt for m in good])
    matrix, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=3)
    if matrix is None or mask is None or int(mask.sum()) < 8:
        raise ValueError('Transformation visuelle non fiable')
    selected = mask.ravel().astype(bool)
    errors = np.linalg.norm(cv2.transform(src[None, :, :], matrix)[0] - dst, axis=1)
    predicted_tile = np.array([raw_point[0] / units_per_pixel, raw_point[1] / units_per_pixel, 1.])
    predicted_screen = cv2.invertAffineTransform(matrix) @ predicted_tile
    observed_tile = matrix @ np.array([sx, sy, 1.])
    return {
        'tile_dimensions': [int(image.shape[1]), int(image.shape[0])],
        'feature_matches': len(good), 'inliers': int(selected.sum()),
        'median_residual_tile_pixels': float(np.median(errors[selected])),
        'assumed_units_per_pixel': units_per_pixel,
        'assumed_tile_point': predicted_tile[:2].tolist(),
        'predicted_screen_point': predicted_screen.tolist(),
        'observed_screen_point': [sx, sy],
        'observed_tile_point': observed_tile.tolist(),
        'screen_error_pixels': float(np.linalg.norm(predicted_screen - [sx, sy])),
        'coordinate_convention': 'top-left image origin; experimental',
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('screenshot', type=Path)
    p.add_argument('tile', type=Path)
    p.add_argument('--screen-point', type=float, nargs=2, required=True, metavar=('X', 'Y'))
    p.add_argument('--raw-point', type=int, nargs=2, required=True, metavar=('X', 'Y'))
    p.add_argument('--units-per-pixel', type=float, default=256)
    a = p.parse_args()
    try:
        result = analyze(a.screenshot, a.tile, a.screen_point, a.raw_point, a.units_per_pixel)
    except (OSError, ValueError) as exc:
        p.exit(1, f'Comparaison impossible : {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
