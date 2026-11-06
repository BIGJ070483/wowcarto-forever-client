"""Compile a complete private icon set, without modifying native files.

The artwork directory must contain 00-normal.png through 28-normal.png and
00-hover.png through 28-hover.png. Every PNG must be 16x16 RGBA with binary
transparency. Validation and encoding happen before the output is created.
"""
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path

from PIL import Image

from tools.inspect_native_icons import compare
from tools.local_install import checked_path
from tools.prepare_native_icon_pair import ROOT, build_strip


def prepare_set(source, artwork_dir, output):
    source, artwork_dir, output = map(checked_path, (source, artwork_dir, output))
    if output.exists() or output.is_relative_to(ROOT) or output.is_relative_to(source) or output.is_relative_to(artwork_dir):
        raise ValueError('Un nouveau dossier privé distinct est requis')
    if source.is_relative_to(output) or artwork_dir.is_relative_to(output):
        raise ValueError('La sortie ne doit pas contenir les entrées')
    profile = os.environ.get('USERPROFILE')
    if profile and output.is_relative_to(checked_path(Path(profile) / 'Documents' / 'WoWCarto-sauvegardes')):
        raise ValueError('Le dossier des sauvegardes est réservé')
    paths = [checked_path(source / name) for name in ('icon_a.bmp', 'icon_b.bmp')]
    originals = [p.read_bytes() for p in paths]
    variants = originals.copy()
    records = []
    for slot in range(29):
        for index, state in enumerate(('normal', 'hover')):
            path = checked_path(artwork_dir / f'{slot:02d}-{state}.png')
            with path.open('rb') as source_file:
                data = source_file.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError('PNG trop volumineux')
            with Image.open(BytesIO(data)) as artwork:
                if artwork.format != 'PNG':
                    raise ValueError('Un dessin PNG est requis')
                variants[index] = build_strip(variants[index], artwork, slot)
            records.append({'slot': slot, 'state': state,
                            'sha256': hashlib.sha256(data).hexdigest()})
    if any(path.read_bytes() != data for path, data in zip(paths, originals)):
        raise ValueError('Les bandes de référence ont changé')
    comparisons = [compare(original, variant) for original, variant in zip(originals, variants)]
    output.mkdir(parents=True, exist_ok=False)
    for name, data in zip(('icon_a.bmp', 'icon_b.bmp'), variants):
        with (output / name).open('xb') as file:
            file.write(data)
    report = {'format': 'wcf-private-native-icon-set-v1', 'artwork': records,
              'strips': comparisons, 'native_ui_tested': False, 'meaning_verified': False}
    with (output / 'manifest.json').open('x', encoding='utf-8') as file:
        json.dump(report, file, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('artwork', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare_set(args.source, args.artwork, args.output), ensure_ascii=False))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Préparation refusée : {exc}\n')


if __name__ == '__main__':
    main()
