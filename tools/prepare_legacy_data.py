"""Add the static Legacy candidate to a new data directory without executing it."""
import argparse
import json
from pathlib import Path
import shutil

from tools.map_packs import sha256
from tools.native_database import load_database


def prepare(source, legacy_source, destination):
    source, legacy_source, destination = map(Path, (source, legacy_source, destination))
    if destination.exists():
        raise ValueError('La destination doit être nouvelle.')
    checked = {}
    report = {}
    for locale in ('Francais', 'US'):
        old_path = legacy_source / locale / 'master.loc'
        old = load_database(old_path, expected_version=1062)
        reference = load_database(source / 'databases' / locale / 'master.loc')
        by_id = {row['id']: row for row in reference['tiles']}
        missing = []
        for row in old['tiles']:
            other = by_id.get(row['id'])
            if other is None:
                missing.append(row['filename'])
            elif any(row[key] != other[key] for key in ('filename', 'group_id', 'x_raw', 'y_raw')):
                raise ValueError('Géométrie de carte incompatible : ' + row['filename'])
        if missing != (['kalimdor59.jpg'] if locale == 'Francais' else []):
            raise ValueError('Inventaire Legacy différent du candidat examiné.')
        checked[locale] = (old_path, sha256(old_path))
        report[locale] = dict(annotations=len(old['annotations']), tiles=len(old['tiles']),
                              missing=missing, source_sha256=old['source_sha256'])
    shutil.copytree(source, destination)
    (destination / 'INCOMPLETE').write_text('Intégration Legacy en cours', encoding='utf-8')
    catalog = json.loads((destination / 'catalog.json').read_text(encoding='utf-8'))
    spec = next(row for row in catalog['sets'] if row['id'] == 'legacy108')
    spec.update(native_version=1062, title='Legacy candidat 1.08b — fonds 1.20', databases={},
                packs=['packs/generated-120/pack.json', 'packs/legacy-108b-candidat/pack.json'])
    for locale, (old_path, digest) in checked.items():
        relative = Path('databases/legacy108') / locale / 'master.loc'
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old_path, target)
        if sha256(target) != digest or sha256(old_path) != digest:
            raise ValueError('Copie Legacy divergente.')
        spec['databases'][locale] = dict(path=relative.as_posix(), sha256=digest)
    (destination / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'legacy-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    (destination / 'INCOMPLETE').unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('legacy_source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    prepare(args.source, args.legacy_source, args.destination)
