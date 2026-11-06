"""Create an atlas-compatible manifest for extracted Forever minimap textures."""
import argparse
import json
from pathlib import Path
import re

from PIL import Image
from tools.map_packs import FORMAT, load_pack, sha256


def prepare(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    validation = json.loads((root / 'validation-decoding.json').read_text(encoding='utf-8'))
    if manifest.get('product') != 'wowf' or manifest.get('build') != '1.60.1.70755':
        raise ValueError('Produit ou build non reconnu.')
    if validation.get('errors') or validation.get('decoded') != manifest.get('ok'):
        raise ValueError('La validation complète des images est absente ou incomplète.')
    rows = []
    for item in manifest['items']:
        if item.get('error'):
            continue
        match = re.fullmatch(r'world/minimaps/(azeroth|kalimdor)/(map\d+_\d+)\.blp', item['name'], re.I)
        if not match:
            raise ValueError('Nom de texture inattendu.')
        path = root / (str(item['id']) + '.blp')
        if path.stat().st_size != item['bytes'] or sha256(path) != item['sha256']:
            raise ValueError('Texture modifiée : ' + str(item['id']))
        with Image.open(path) as image:
            width, height = image.size
        asset = dict(path=path.name, bytes=item['bytes'], sha256=item['sha256'], width=width, height=height)
        rows.append(dict(id=match[1].lower() + '-' + match[2].lower(), title=match[2],
                         group=match[1].title(), standard=asset, high=None))
    data = dict(format=FORMAT, id='wow-forever-70755-terrain',
                title='WoW Forever — textures de terrain', mode='Modern',
                version='WoW Forever 1.60.1.70755',
                provenance='Extraction locale du produit wowf, noms du verified listfile wowdev, textures BLP originales.',
                limitations='Tuiles individuelles ; quatre raccords observés. Géographie et coordonnées WoWCartographe non calibrées.',
                maps=rows)
    output = root / 'pack.json'
    with output.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
    load_pack(output)
    return output, len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    path, count = prepare(args.directory)
    print(f'{path} : {count} textures.')
