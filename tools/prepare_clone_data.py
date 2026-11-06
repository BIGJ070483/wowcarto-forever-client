"""Prepare a new private clone data directory from already verified local packs."""
import argparse
import json
from pathlib import Path
import shutil

from tools.map_packs import load_pack, sha256

ROOT = Path(__file__).resolve().parents[1]


def prepare(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'INCOMPLETE').write_text('Préparation en cours', encoding='utf-8')
    packs = [ROOT / 'outputs/atlas-data-20261104/generated-120',
             ROOT / 'outputs/atlas-data-20261104/native-plans-120',
             ROOT / 'outputs/forever-70755-terrains-bruts',
             ROOT / 'outputs/legacy-research-20261104/pack-legacy-108b-candidat']
    for source in packs:
        data = load_pack(source / 'pack.json')
        target = destination / 'packs' / data['id']
        target.mkdir(parents=True)
        # Copy only assets referenced by the manifest, never incidental research files.
        shutil.copyfile(source / 'pack.json', target / 'pack.json')
        for row in data['maps']:
            for kind in ('standard', 'high'):
                asset = row.get(kind)
                if not asset:
                    continue
                src, dst = source / asset['path'], target / asset['path']
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copyfile(src, dst)
                    if sha256(dst) != asset['sha256'] or sha256(src) != asset['sha256']:
                        raise ValueError('Copie divergente : ' + str(src))
    databases = {}
    for name in ('Francais', 'US'):
        source = Path(r'C:\Program Files (x86)\WoWCartographe\database') / name / 'master.loc'
        dest = destination / 'databases' / name / 'master.loc'
        dest.parent.mkdir(parents=True)
        before = sha256(source)
        shutil.copyfile(source, dest)
        if sha256(source) != before or sha256(dest) != before:
            raise ValueError('Base source modifiée pendant la copie.')
        databases[name] = {'path': dest.relative_to(destination).as_posix(), 'sha256': before}
    catalog = {'format': 'wcf-clone-catalog-v1', 'sets': [
        {'id': 'reference120', 'title': 'Référence 1.20', 'mode': 'Référence 1.20',
         'databases': databases, 'packs': ['packs/generated-120/pack.json', 'packs/native-plans-120/pack.json'],
         'community_coordinate_system': 'wowcartographe-120'},
        {'id': 'forever70755', 'title': 'WoW Forever 1.60.1.70755', 'mode': 'Modern',
         'terrain_pack': 'packs/wow-forever-70755-terrain/pack.json'},
        {'id': 'legacy108', 'title': '1.08b — plans candidats', 'mode': 'Legacy',
         'legacy_pack': 'packs/legacy-108b-candidat/pack.json'}]}
    (destination / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'INCOMPLETE').unlink()
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    prepare(parser.parse_args().destination)
