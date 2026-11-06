"""Inspect a native 1.20 icon strip or compare a proposed 16px replacement.

Read-only: does not extract images, install assets or assert command semantics.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

MAX_BYTES = 4 * 1024 * 1024
WIDTH, HEIGHT, CELL = 464, 16, 16


def inspect(data):
    if len(data) < 54 or len(data) > MAX_BYTES or data[:2] != b'BM':
        raise ValueError('Fichier BMP invalide ou trop volumineux')
    size, pixel_offset = struct.unpack_from('<IxxxxI', data, 2)
    dib, width, height, planes, bits, compression = struct.unpack_from('<IiiHHI', data, 14)
    colors = struct.unpack_from('<I', data, 46)[0]
    if (dib != 40 or width != WIDTH or abs(height) != HEIGHT or planes != 1 or bits != 8 or
            compression != 0 or colors not in (0, 256) or size != len(data)):
        raise ValueError('Format observé 464×16 BMP 8 bits non reconnu')
    palette_start = 14 + dib
    stride = ((width + 3) // 4) * 4
    if pixel_offset < palette_start + 1024 or pixel_offset + stride * HEIGHT != len(data):
        raise ValueError('Palette ou données bitmap incohérentes')
    palette = data[palette_start:palette_start + 1024]
    rows = [data[pixel_offset + y * stride:pixel_offset + y * stride + width] for y in range(HEIGHT)]
    if height > 0:
        rows.reverse()
    slots = [b''.join(row[x:x + CELL] for row in rows) for x in range(0, WIDTH, CELL)]
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'width': width, 'height': abs(height), 'bits_per_pixel': bits,
            'slots': len(slots), 'cell_size': CELL,
            'palette_sha256': hashlib.sha256(palette).hexdigest(),
            'slot_sha256': [hashlib.sha256(slot).hexdigest() for slot in slots],
            'top_left_palette_index': rows[0][0]}


def compare(reference, candidate):
    old, new = inspect(reference), inspect(candidate)
    if old['palette_sha256'] != new['palette_sha256']:
        raise ValueError('Palette différente : compatibilité non démontrée')
    if old['top_left_palette_index'] != new['top_left_palette_index']:
        raise ValueError('Indice du pixel supérieur gauche différent')
    return {'reference_sha256': old['sha256'], 'candidate_sha256': new['sha256'],
            'changed_slots_zero_based': [i for i, (a, b) in enumerate(zip(old['slot_sha256'], new['slot_sha256'])) if a != b],
            'palette_identical': True, 'native_ui_tested': False, 'meaning_verified': False}


def read_bounded(path):
    with path.open('rb') as source:
        return source.read(MAX_BYTES + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path)
    parser.add_argument('--candidate', type=Path)
    args = parser.parse_args()
    try:
        original = read_bounded(args.reference)
        result = compare(original, read_bounded(args.candidate)) if args.candidate else inspect(original)
    except (OSError, ValueError, struct.error) as exc:
        parser.exit(1, f'Inspection impossible : {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
