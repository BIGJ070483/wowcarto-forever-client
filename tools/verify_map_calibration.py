"""Check the shipped transform against existing landmark correspondences."""
import argparse
import json
import math
from pathlib import Path
import statistics

from tools.clone_regions import FOREVER_REGISTRATION, project_forever


def verify(evidence):
    result = {}
    for group, (name, origin, _) in FOREVER_REGISTRATION.items():
        data = json.loads((Path(evidence) / (name + '-matches.json')).read_text(encoding='utf-8'))
        errors = []
        cells = set()
        for pair in data['coordinates']:
            x, y = pair['ref']
            expected = pair['modern']
            actual = project_forever(group, (origin[0] + x * 4, origin[1] + y * 4))
            error = math.dist(actual, (expected[0] * 16, expected[1] * 16))
            errors.append(error)
            cells.add((int(expected[0] // 16), int(expected[1] // 16)))
        if not errors or any(not math.isfinite(e) for e in errors):
            raise ValueError('Points de contrôle absents ou invalides.')
        result[name] = dict(landmarks=len(errors), terrain_cells=len(cells),
            median_scene_units=statistics.median(errors), maximum_scene_units=max(errors),
            screen_error_by_zoom={str(zoom): dict(median_px=statistics.median(errors)*zoom,
                                                max_px=max(errors)*zoom) for zoom in (.1,.25,.5,1,2)},
            scope='Existing image landmarks; not independent in-game annotation validation')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.evidence), indent=2))
