"""Prepare compact terrain overviews from existing verified preview images."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from PIL import Image

from tools.map_packs import checked_image, load_pack, sha256


def render_overview(tiles, size=(100, 100)):
    if not tiles:
        raise ValueError('Aucune tuile pour la mini-carte.')
    x0, y0 = min(t['x'] for t in tiles), min(t['y'] for t in tiles)
    x1, y1 = max(t['x'] + 256 for t in tiles), max(t['y'] + 256 for t in tiles)
    scale = min(size[0] / max(1, x1 - x0), size[1] / max(1, y1 - y0))
    image = Image.new('RGB', size, '#00242c')
    for tile in tiles:
        preview = checked_image(tile['root'], tile['preview'])
        left, top = round((tile['x'] - x0) * scale), round((tile['y'] - y0) * scale)
        right, bottom = round((tile['x'] + 256 - x0) * scale), round((tile['y'] + 256 - y0) * scale)
        if right > left and bottom > top:
            image.paste(preview.resize((right - left, bottom - top), Image.Resampling.LANCZOS), (left, top))
    return image, [x0, y0, x1, y1]


def prepare(pack_dir, destination):
    pack_dir, destination = Path(pack_dir), Path(destination)
    pack = load_pack(pack_dir / 'pack.json')
    previews = json.loads((pack_dir / 'previews.json').read_text(encoding='utf-8'))
    groups = {}
    for row in pack['maps']:
        match = re.fullmatch(r'map(\d+)_(\d+)', row['title'], re.I)
        if not match or row['group'] not in ('Azeroth', 'Kalimdor') or row['id'] not in previews:
            raise ValueError('Aperçu de terrain absent ou nom non reconnu.')
        groups.setdefault(row['group'], []).append(dict(x=int(match[1])*256, y=int(match[2])*256,
                                                        root=pack_dir, preview=previews[row['id']]))
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'format': 'wcf-minimaps-v1', 'groups': {}}
    for name, tiles in groups.items():
        image, bounds = render_overview(tiles)
        target = destination / (name.lower() + '.png')
        image.save(target)
        manifest['groups'][name] = {'path': target.name, 'sha256': sha256(target),
                                    'bytes': target.stat().st_size, 'width': 100, 'height': 100,
                                    'bounds': bounds, 'tiles': len(tiles)}
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack_dir', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.pack_dir, args.destination), ensure_ascii=False))
