"""Versioned local image packs. Originals are read only; imports never overwrite."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil

from PIL import Image

FORMAT = 'wcf-map-pack-v1'
MODES = ('Legacy', 'Modern', 'Archives 1.20')
MAX_PIXELS = 16_777_216


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local_file(root, relative):
    root = Path(root).resolve()
    rel = PurePosixPath(relative)
    if not relative or '\\' in relative or ':' in relative or rel.is_absolute() or '..' in rel.parts:
        raise ValueError('Chemin de carte invalide.')
    candidate = root.joinpath(*rel.parts)
    if candidate.resolve().is_relative_to(root) and not any(
        p.is_symlink() or (getattr(p.stat(), 'st_file_attributes', 0) & 0x400)
        for p in [candidate, *candidate.parents] if p.exists() and p.is_relative_to(root)
    ):
        return candidate
    raise ValueError('Lien ou chemin hors du pack refusé.')


def read_image(path):
    try:
        with Image.open(path) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Carte supérieure à 16 millions de pixels.')
            image.load()
            return image.convert('RGB')
    except Image.DecompressionBombError as error:
        raise ValueError('Dimensions d’image excessives.') from error


def load_pack(path, verify=False):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Manifeste trop volumineux.')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('format') != FORMAT or data.get('mode') not in MODES:
        raise ValueError('Format ou mode de pack inconnu.')
    for key in ('id', 'title', 'provenance', 'version', 'limitations'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError('Métadonnée absente : ' + key)
    maps = data.get('maps')
    if not isinstance(maps, list) or len(maps) > 10000:
        raise ValueError('Liste de cartes invalide.')
    ids = set()
    for row in maps:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids:
            raise ValueError('Identifiant de carte absent ou dupliqué.')
        ids.add(row['id'])
        for key in ('title', 'group'):
            if not isinstance(row.get(key), str):
                raise ValueError('Nom ou groupe invalide.')
        for kind in ('standard', 'high'):
            asset = row.get(kind)
            if asset is None and kind == 'high':
                continue
            if not isinstance(asset, dict) or not isinstance(asset.get('path'), str):
                raise ValueError('Image absente du manifeste.')
            local_file(path.parent, asset['path'])
            if not re.fullmatch('[0-9a-f]{64}', asset.get('sha256', '')):
                raise ValueError('Empreinte invalide.')
            if any(type(asset.get(k)) is not int or not 1 <= asset[k] <= MAX_PIXELS for k in ('width', 'height', 'bytes')):
                raise ValueError('Dimensions ou taille invalides.')
            if asset['width'] * asset['height'] > MAX_PIXELS:
                raise ValueError('Image trop grande.')
            if verify:
                checked_image(path.parent, asset)
    return data


def checked_image(root, asset):
    path = local_file(root, asset['path'])
    if not path.is_file():
        raise ValueError('Carte manquante : ' + asset['path'])
    if path.stat().st_size != asset['bytes'] or sha256(path) != asset['sha256']:
        raise ValueError('Carte modifiée : ' + asset['path'])
    image = read_image(path)
    if image.size != (asset['width'], asset['height']):
        raise ValueError('Dimensions différentes du manifeste.')
    return image


def create_pack(source, destination, *, pack_id, title, mode, provenance, version, limitations):
    """Copy only images to a new pack and verify each copied byte and decode."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('La source et la destination doivent être distinctes.')
    if mode not in MODES or not re.fullmatch('[a-z0-9-]+', pack_id):
        raise ValueError('Mode ou identifiant invalide.')
    files = sorted((p for p in source.iterdir() if p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg', '.png')), key=lambda p: p.name.casefold())
    if not files:
        raise ValueError('Aucune carte dans le dossier choisi.')
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'INCOMPLETE').write_text('Copie non vérifiée', encoding='utf-8')
    data = dict(format=FORMAT, id=pack_id, title=title, mode=mode, provenance=provenance,
                version=version, limitations=limitations, source=str(source), maps=[])
    for original in files:
        row = dict(id=original.name, title=original.stem, group=re.sub(r'\d+$', '', original.stem).replace('dungeon_', 'Donjons / '))
        for kind, src in [('standard', original), ('high', source / 'high' / original.name)]:
            if not src.is_file():
                row[kind] = None
                continue
            relative = 'images/' + ('high/' if kind == 'high' else '') + original.name
            local_file(source, src.relative_to(source).as_posix())
            image = read_image(src)
            before = sha256(src)
            target = local_file(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with src.open('rb') as stream, target.open('xb') as copy:
                shutil.copyfileobj(stream, copy)
            if sha256(target) != before or sha256(src) != before:
                raise ValueError('Copie divergente ou source modifiée : ' + str(src))
            row[kind] = dict(path=relative, sha256=before, width=image.width, height=image.height, bytes=src.stat().st_size)
        data['maps'].append(row)
    manifest = destination / 'pack.json'
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    load_pack(manifest, verify=True)
    (destination / 'INCOMPLETE').unlink()
    return manifest


def import_pack(manifest, packs_dir):
    manifest = Path(manifest)
    data = load_pack(manifest, verify=True)
    if not re.fullmatch('[a-z0-9-]+', data['id']):
        raise ValueError('Identifiant invalide.')
    destination = Path(packs_dir) / data['id']
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'INCOMPLETE').write_text('Import non vérifié', encoding='utf-8')
    for row in data['maps']:
        for kind in ('standard', 'high'):
            if row.get(kind):
                rel = row[kind]['path']
                src, dst = local_file(manifest.parent, rel), local_file(destination, rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    with src.open('rb') as stream, dst.open('xb') as copy:
                        shutil.copyfileobj(stream, copy)
    result = destination / 'pack.json'
    result.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    load_pack(result, verify=True)
    (destination / 'INCOMPLETE').unlink()
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='Créer un nouveau pack sans écrasement')
    create.add_argument('source', type=Path)
    create.add_argument('destination', type=Path)
    create.add_argument('--id', required=True, dest='pack_id')
    create.add_argument('--title', required=True)
    create.add_argument('--mode', choices=MODES, required=True)
    create.add_argument('--provenance', required=True)
    create.add_argument('--version', required=True)
    create.add_argument('--limitations', required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('manifest', type=Path)
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        if command == 'create':
            print(create_pack(**args))
        else:
            data = load_pack(args['manifest'], verify=True)
            print(f"PASS : {len(data['maps'])} cartes, octets et décodage vérifiés.")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
