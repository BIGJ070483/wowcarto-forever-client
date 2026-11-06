"""Create private edge-scaled icon strips from the owner's native bitmaps.

These research candidates preserve 29 cells and the source palette. They are
not proven to be compatible with the native client and add no invented detail.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from tools.inspect_native_icons import inspect
from tools.local_install import checked_path

ROOT = Path(__file__).resolve().parents[1]


def scale2x(cell):
    width, height = cell.size
    source = cell.load()
    out = Image.new('P', (width * 2, height * 2))
    out.putpalette(cell.getpalette())
    pixels = out.load()
    for y in range(height):
        for x in range(width):
            e = source[x, y]
            b = source[x, max(y - 1, 0)]
            d = source[max(x - 1, 0), y]
            f = source[min(x + 1, width - 1), y]
            h = source[x, min(y + 1, height - 1)]
            if b != h and d != f:
                values = (d if d == b else e, f if b == f else e,
                          d if d == h else e, f if h == f else e)
            else:
                values = (e, e, e, e)
            pixels[x * 2, y * 2], pixels[x * 2 + 1, y * 2] = values[:2]
            pixels[x * 2, y * 2 + 1], pixels[x * 2 + 1, y * 2 + 1] = values[2:]
    return out


def generate(source, output):
    source, output = checked_path(source), checked_path(output)
    if output.exists() or output.is_relative_to(ROOT) or output.is_relative_to(source):
        raise ValueError('Nouveau dossier privé hors du dépôt et des sources requis')
    paths = [source / 'icon_a.bmp', source / 'icon_b.bmp']
    data = [path.read_bytes() for path in paths]
    info = [inspect(item) for item in data]
    output.mkdir(parents=True, exist_ok=False)
    files = []
    for path, raw, record in zip(paths, data, info):
        with Image.open(path) as original:
            original.load()
            if original.mode != 'P' or original.size != (464, 16):
                raise ValueError('Bande indexée 1.20 non reconnue')
            for factor in (2, 4):
                strip = Image.new('P', (464 * factor, 16 * factor))
                strip.putpalette(original.getpalette())
                for i in range(29):
                    cell = original.crop((i * 16, 0, i * 16 + 16, 16))
                    cell = scale2x(cell)
                    if factor == 4:
                        cell = scale2x(cell)
                    strip.paste(cell, (i * 16 * factor, 0))
                destination = output / f'{path.stem}-{factor}x.bmp'
                with destination.open('xb') as handle:
                    strip.save(handle, format='BMP')
                files.append({'name': destination.name, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                              'source_sha256': record['sha256'], 'width': 464 * factor, 'height': 16 * factor})
        if path.read_bytes() != raw:
            raise ValueError('La source a changé pendant la génération')
    report = {'format': 'wcf-hd-icon-experiment-v1', 'files': files, 'cells': 29,
              'native_compatibility_verified': False, 'new_detail_created': False}
    with (output / 'manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Dossier personnel contenant icon_a.bmp et icon_b.bmp')
    parser.add_argument('output', type=Path, help='Nouveau dossier privé hors dépôt')
    args = parser.parse_args()
    try:
        print(json.dumps(generate(args.source, args.output), ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Génération refusée : {exc}\n')


if __name__ == '__main__':
    main()
