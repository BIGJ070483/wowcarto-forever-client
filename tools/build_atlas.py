"""Package the offline atlas. Local proprietary packs are supplied separately."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from tools.map_packs import load_pack, sha256

ROOT = Path(__file__).resolve().parents[1]


def build(destination, packs):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    build_dir = destination / 'build'
    app_dir = destination / 'WoWCartoForever-Atlas'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
                    '--noupx', '--name', 'WoWCartoForever-Atlas', '--paths', str(ROOT),
                    '--distpath', str(destination), '--workpath', str(build_dir), '--specpath', str(build_dir),
                    str(ROOT / 'tools' / 'map_viewer.py')], check=True, cwd=ROOT)
    library = app_dir / 'packs'
    library.mkdir()
    for pack in packs:
        data = load_pack(Path(pack) / 'pack.json', verify=True)
        shutil.copytree(pack, library / data['id'])
        load_pack(library / data['id'] / 'pack.json', verify=True)
    shutil.copyfile(ROOT / 'docs' / 'ATLAS_LOCAL.md', app_dir / 'LIRE-MOI.md')
    with zipfile.ZipFile(app_dir / 'sources-atlas.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for name in ('tools/__init__.py', 'tools/map_packs.py', 'tools/map_viewer.py', 'tools/build_atlas.py',
                     'tests/test_map_packs.py', 'docs/ATLAS_LOCAL.md', 'requirements.txt', 'requirements-build-windows.txt'):
            archive.write(ROOT / name, name)
    checksums = []
    for path in sorted(app_dir.rglob('*')):
        if path.is_file():
            checksums.append(sha256(path) + '  ' + path.relative_to(app_dir).as_posix())
    (app_dir / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    archive_path = destination / 'WoWCartoForever-Atlas-Windows.zip'
    with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app_dir.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(destination))
    print(str(app_dir / 'WoWCartoForever-Atlas.exe'))
    print('ZIP SHA-256 : ' + sha256(archive_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--pack', type=Path, action='append', default=[])
    args = parser.parse_args()
    build(args.destination, args.pack)


if __name__ == '__main__':
    main()
