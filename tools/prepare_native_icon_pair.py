"""Prepare private 16px normal/hover BMP candidates without installing them.

Inputs are two finished 16x16 RGBA PNGs; no automatic redraw or enlargement is
performed. Palette mapping is only a format conversion, not a visual approval.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

from tools.inspect_native_icons import compare, inspect
from tools.local_install import checked_path

ROOT = Path(__file__).resolve().parents[1]


def build_strip(original, artwork, slot):
    info = inspect(original)
    if not 0 <= slot < info['slots']:
        raise ValueError('Indice de cellule hors limites')
    if artwork.mode != 'RGBA' or artwork.size != (16, 16):
        raise ValueError('Le dessin doit être un PNG RGBA exactement 16×16')
    pixels = [artwork.getpixel((x, y)) for y in range(16) for x in range(16)]
    if any(alpha not in (0, 255) for _, _, _, alpha in pixels):
        raise ValueError('Transparence partielle refusée : masque binaire requis')
    if not any(alpha == 255 for _, _, _, alpha in pixels):
        raise ValueError('Icône entièrement transparente refusée')
    mask = info['top_left_palette_index']
    palette = original[54:1078]
    colors = [(palette[i + 2], palette[i + 1], palette[i]) for i in range(0, 1024, 4)]
    candidates = [(i, color) for i, color in enumerate(colors) if color != colors[mask]]
    if not candidates:
        raise ValueError('Palette sans couleur distincte du masque')
    result = bytearray(original)
    pixel_offset = int.from_bytes(original[10:14], 'little')
    height = int.from_bytes(original[22:26], 'little', signed=True)
    for y in range(16):
        row = 15 - y if height > 0 else y
        for x in range(16):
            r, g, b, alpha = pixels[y * 16 + x]
            index = mask if not alpha else min(candidates, key=lambda item: (
                (item[1][0] - r) ** 2 + (item[1][1] - g) ** 2 + (item[1][2] - b) ** 2,
                item[0]))[0]
            result[pixel_offset + row * 464 + slot * 16 + x] = index
    # This position is also used by the native strip as the mask indicator.
    if slot == 0 and result[pixel_offset + (15 if height > 0 else 0) * 464] != mask:
        raise ValueError('Le pixel supérieur gauche doit rester transparent')
    candidate = bytes(result)
    check = compare(original, candidate)
    if any(i != slot for i in check['changed_slots_zero_based']):
        raise ValueError('Une autre cellule a changé')
    return candidate


def prepare(source, normal, hover, output, slot):
    source, normal, hover, output = map(checked_path, (source, normal, hover, output))
    if output.exists() or output.is_relative_to(ROOT) or output.is_relative_to(source):
        raise ValueError('Nouveau dossier privé hors dépôt et source requis')
    profile = os.environ.get('USERPROFILE')
    if profile and output.is_relative_to(checked_path(Path(profile) / 'Documents' / 'WoWCarto-sauvegardes')):
        raise ValueError('Le dossier des sauvegardes est réservé')
    if any(p.is_relative_to(output) for p in (source, normal, hover)):
        raise ValueError('Le dossier de sortie ne doit pas contenir les entrées')
    inputs = [checked_path(source / 'icon_a.bmp'), checked_path(source / 'icon_b.bmp')]
    originals = [p.read_bytes() for p in inputs]
    results = []
    for original, art_path in zip(originals, (normal, hover)):
        with Image.open(art_path) as artwork:
            if artwork.format != 'PNG':
                raise ValueError('Un dessin PNG est requis')
            results.append(build_strip(original, artwork, slot))
    if any(path.read_bytes() != data for path, data in zip(inputs, originals)):
        raise ValueError('La source a changé pendant la préparation')
    output.mkdir(parents=True, exist_ok=False)
    for name, data in zip(('icon_a.bmp', 'icon_b.bmp'), results):
        with (output / name).open('xb') as target:
            target.write(data)
    report = {'slot': slot, 'native_ui_tested': False, 'meaning_verified': False,
              'strips': [compare(old, new) for old, new in zip(originals, results)],
              'art_sha256': [hashlib.sha256(p.read_bytes()).hexdigest() for p in (normal, hover)]}
    with (output / 'manifest.json').open('x', encoding='utf-8') as target:
        json.dump(report, target, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Dossier privé des bandes originales')
    parser.add_argument('normal', type=Path, help='PNG RGBA 16×16 pour état normal')
    parser.add_argument('hover', type=Path, help='PNG RGBA 16×16 pour survol')
    parser.add_argument('output', type=Path, help='Nouveau dossier privé')
    parser.add_argument('--slot', type=int, required=True, help='Indice de 0 à 28')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.normal, args.hover, args.output, args.slot), ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Préparation refusée : {exc}\n')


if __name__ == '__main__':
    main()
