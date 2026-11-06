"""Prepare a private per-user copy from an already-owned 1.20 installation.

No download, process launch, security-policy change or native format conversion.
Sources, existing destinations and existing backups are never overwritten.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import zlib

EXE = 'WowCartographe.exe'
EXPECTED_SHA256 = '581d8940c6a5471184d7d8ce9e7cf01a2a5cf48ddabe427f8ff4b7483582d457'
FORMAT = 'wcf-local-install-v1'
MAX_FILE = 512 * 1024 * 1024
MAX_TOTAL = 4 * 1024 * 1024 * 1024
MAX_FILES = 20000
OFFLINE_CONFIGS = ('database/Francais/url.bin', 'database/US/url.bin')
REPO_ROOT = next((p for p in Path(__file__).resolve().parents if (p / '.git').exists()), None)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def checked_path(path):
    path = Path(path).absolute()
    # Check the unresolved path as well: resolve() alone conceals junctions.
    for parent in (path, *path.parents):
        if parent.exists() and (parent.is_symlink() or getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Lien symbolique ou point de réanalyse refusé')
    return path.resolve()


def inventory(root):
    files = {}
    total = 0
    for folder, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            checked_path(Path(folder) / name)
        for name in names:
            path = Path(folder) / name
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Fichier non régulier refusé')
            size = path.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
                raise ValueError('Budget de copie dépassé')
            files[path.relative_to(root).as_posix()] = {'size': size, 'sha256': digest(path)}
    return files


def separate(paths):
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path.is_relative_to(other) or other.is_relative_to(path):
                raise ValueError('Sources, copie et sauvegarde doivent être distinctes et non imbriquées')


def copy_verified(source, target, files):
    target.mkdir(parents=True, exist_ok=False)
    for relative, metadata in files.items():
        origin = source / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
        if digest(origin) != metadata['sha256'] or digest(destination) != metadata['sha256']:
            raise ValueError('Source modifiée ou copie incohérente ; conserver le dossier incomplet pour diagnostic')


def offline_config():
    # Loopback is a local sink, not proof of process-wide network isolation.
    host = b'http://127.0.0.1:9/'
    plain = b'\r\n'.join([b'1200', b'noreply@example.invalid', host, b'', b'manifest',
                           host + b'upload', host, b'false', b'Local offline copy'])
    key = hashlib.md5(b'cartokey', usedforsecurity=False).hexdigest().encode('ascii')
    return zlib.compress(bytes(byte ^ key[i % len(key)] for i, byte in enumerate(plain)))


def prepare(source, target, backup, overlay=None, *, expected_hash=EXPECTED_SHA256, offline=False):
    source, target, backup = map(checked_path, (source, target, backup))
    overlay = checked_path(overlay) if overlay is not None else None
    paths = [source, target, backup] + ([overlay] if overlay else [])
    separate(paths)
    if REPO_ROOT and any(p.is_relative_to(REPO_ROOT) for p in (target, backup)):
        raise ValueError('Les copies privées doivent rester hors du dépôt')
    if not source.is_dir() or not (source / EXE).is_file() or digest(source / EXE) != expected_hash:
        raise ValueError('Installation 1.20 de référence non reconnue')
    if target.exists() or backup.exists():
        raise ValueError('La copie et la sauvegarde doivent être nouvelles ; aucun écrasement')
    if overlay and not overlay.is_dir():
        raise ValueError('VirtualStore indiqué absent')
    source_files = inventory(source)
    overlay_files = inventory(overlay) if overlay else {}
    effective = dict(source_files)
    effective.update(overlay_files)
    if any(Path(name).suffix.lower() in {'.exe', '.dll', '.sys', '.cmd', '.bat', '.ps1', '.py'} for name in overlay_files):
        raise ValueError('Le VirtualStore ne peut pas substituer du code exécutable')
    if offline and not all(name in effective for name in OFFLINE_CONFIGS):
        raise ValueError('Configurations natives hors ligne introuvables')
    configs = sorted(name for name in effective if len(Path(name).parts) == 3
                     and Path(name).parts[0].lower() == 'database'
                     and Path(name).parts[2].lower() == 'url.bin')
    # The separate verified backup precedes any creation of the application copy.
    backup.mkdir(parents=True, exist_ok=False)
    copy_verified(source, backup / 'installation', source_files)
    if overlay:
        copy_verified(overlay, backup / 'virtualstore', overlay_files)
    backup_manifest = {'format': FORMAT, 'source': str(source), 'overlay': str(overlay) if overlay else None,
                       'installation': source_files, 'virtualstore': overlay_files}
    with (backup / 'backup-manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(backup_manifest, handle, indent=2)
    target.mkdir(parents=True, exist_ok=False)
    # An incomplete preparation cannot be mistaken for a verified installation.
    (target / 'INCOMPLETE').write_text('Preparation in progress; do not launch.\n', encoding='utf-8')
    copy_verified(backup / 'installation', target / 'client', source_files)
    for relative, metadata in overlay_files.items():
        destination = target / 'client' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / 'virtualstore' / relative, destination)
        if digest(destination) != metadata['sha256']:
            raise ValueError('VirtualStore : copie incohérente')
    if offline:
        payload = offline_config()
        for relative in configs:
            path = target / 'client' / relative
            path.write_bytes(payload)
            if path.read_bytes() != payload:
                raise ValueError('Configuration locale incohérente')
            effective[relative] = {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    # Detect changes to the personal sources during the whole operation.
    if inventory(source) != source_files or (overlay and inventory(overlay) != overlay_files):
        raise ValueError('Source modifiée pendant la préparation ; fermer le logiciel et recommencer dans un nouveau dossier')
    manifest = {'format': FORMAT, 'source': str(source), 'backup': str(backup), 'files': effective, 'offline_endpoints': offline,
                'native_update_verified': False, 'native_offline_ui_verified': False,
                'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with (target / 'install-manifest.json').open('x', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2)
    (target / 'INCOMPLETE').unlink()
    return {'files': len(effective), 'bytes': sum(f['size'] for f in effective.values()),
            'backup_verified': True, 'native_update_verified': False, 'auto_launched': False,
            'offline_endpoints': offline, 'network_isolation_verified': False}


def verify(target):
    target = checked_path(target)
    manifest_path = target / 'install-manifest.json'
    if manifest_path.stat().st_size > 16 * 1024 * 1024 or (target / 'INCOMPLETE').exists():
        raise ValueError('Préparation incomplète ou manifeste trop volumineux')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format') != FORMAT:
        raise ValueError('Manifeste inconnu')
    actual = inventory(checked_path(target / 'client'))
    expected = manifest['files']
    return {'missing': len(set(expected) - set(actual)), 'added': len(set(actual) - set(expected)),
            'changed': sum(actual[name] != expected[name] for name in set(actual) & set(expected)),
            'native_update_verified': False}


def archive(target, destination):
    target, destination = map(checked_path, (target, destination))
    separate([target, destination])
    # Native edits are allowed here: retirement preserves the entire tree as-is.
    verify(target)
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError('Destination de conservation nouvelle requise, dans un dossier existant')
    os.rename(target, destination)
    return {'preserved_at': str(destination), 'deleted_native_files': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    install = sub.add_parser('prepare', help='Copie locale privée, sauvegardée, sans lancement automatique')
    install.add_argument('--source', type=Path, required=True)
    install.add_argument('--overlay', type=Path)
    install.add_argument('--target', type=Path, required=True)
    install.add_argument('--backup', type=Path, required=True)
    install.add_argument('--offline', action='store_true', help='Rediriger les configurations natives connues vers loopback')
    check = sub.add_parser('verify')
    check.add_argument('target', type=Path)
    retire = sub.add_parser('archive', help='Retirer la copie en conservant tous ses fichiers ; fermer le client avant')
    retire.add_argument('target', type=Path)
    retire.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.source, args.target, args.backup, args.overlay, offline=args.offline)
        elif args.command == 'verify':
            result = verify(args.target)
        else:
            result = archive(args.target, args.destination)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'Opération refusée : ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    if args.command == 'verify' and any(result[k] for k in ('missing', 'added', 'changed')):
        sys.exit(2)


if __name__ == '__main__':
    main()
