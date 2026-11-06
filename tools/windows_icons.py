"""Reversible installation of a specifically verified private icon candidate."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

from tools.inspect_native_icons import compare
from tools.local_install import checked_path, digest

NAMES = ('icon_a.bmp', 'icon_b.bmp')
ORIGINAL = ('0d23043780e9d40f9015b15d04d24e360779b9c2df7463b01bbef10b1514787a',
            '8babf3422491fc0724504c3415716ff0fa3c9feff4bc2f8e34b32e5b6f2c04fb')
CANDIDATE = ('808831fedea2919f8580c6488e492939a88333131db0aa3a8de81bae002a613c',
             '2af91867c76f262188a463bdb8c1ac26d62234da73c4a822324c3bc552b344db')


def client_running(exe):
    if os.name != 'nt':
        raise ValueError('Vérification de processus disponible uniquement sur Windows')
    script = r"""
$ErrorActionPreference='Stop'
foreach ($p in Get-CimInstance Win32_Process -Filter "Name='WowCartographe.exe'") {
  if (!$p.ExecutablePath) { exit 2 }
  if ($p.ExecutablePath -ieq $env:WCF_ICON_EXE) { exit 1 }
}
exit 0
"""
    env = dict(os.environ, WCF_ICON_EXE=str(exe))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode not in (0, 1):
        raise ValueError('Impossible de vérifier les processus WoWCartographe')
    return result.returncode == 1


def paths(target):
    target = checked_path(target)
    return ([checked_path(target / 'client' / 'data' / n) for n in NAMES],
            [checked_path(target / 'wcf-icones-originales' / n) for n in NAMES],
            checked_path(target / 'wcf-icones-en-cours.json'))


def replace_verified(destination, payload, expected):
    fd, tmp = tempfile.mkstemp(prefix='wcf-icon-', suffix='.tmp', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(Path(tmp)) != expected:
            raise ValueError('Écriture temporaire non vérifiée')
        os.replace(tmp, destination)
        if digest(destination) != expected:
            raise ValueError('Remplacement non vérifié')
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def recover(target, *, running=client_running):
    destinations, backups, marker = paths(target)
    if not marker.exists():
        return False
    if running(checked_path(Path(target) / 'client' / 'WowCartographe.exe')):
        raise ValueError('Ferme WoWCartographe avant de récupérer les icônes.')
    if json.loads(marker.read_text(encoding='utf-8')) != {'format': 'wcf-icons-transaction-v1'}:
        raise ValueError('Transaction d’icônes inconnue ; intervention nécessaire')
    if any(not backup.is_file() or digest(backup) != expected for backup, expected in zip(backups, ORIGINAL)):
        raise ValueError('Sauvegarde des icônes invalide ; intervention nécessaire')
    if any(not dest.is_file() or digest(dest) not in (old, new)
           for dest, old, new in zip(destinations, ORIGINAL, CANDIDATE)):
        raise ValueError('Icône modifiée pendant la transaction ; écrasement refusé')
    for dest, backup, expected in zip(destinations, backups, ORIGINAL):
        replace_verified(dest, backup.read_bytes(), expected)
    marker.unlink()
    return True


def change_icons(target, assets, *, restore=False, running=client_running):
    target, assets = checked_path(target), checked_path(assets)
    exe = checked_path(target / 'client' / 'WowCartographe.exe')
    destinations, backups, marker = paths(target)
    if running(exe):
        raise ValueError('Ferme WoWCartographe avant de changer les icônes.')
    recover(target, running=running)
    current = [digest(path) for path in destinations]
    wanted = list(ORIGINAL if restore else CANDIDATE)
    if current == wanted:
        return 'already'
    expected_current = list(CANDIDATE if restore else ORIGINAL)
    if current != expected_current:
        raise ValueError('Icônes personnalisées ou inconnues : aucune modification.')
    if restore:
        if any(not path.is_file() or digest(path) != expected for path, expected in zip(backups, ORIGINAL)):
            raise ValueError('Sauvegarde des icônes d’origine invalide.')
        payloads = [path.read_bytes() for path in backups]
    else:
        candidates = [checked_path(assets / n) for n in NAMES]
        if any(not path.is_file() or digest(path) != expected for path, expected in zip(candidates, CANDIDATE)):
            raise ValueError('Paquet d’icônes inconnu ou endommagé.')
        payloads = [path.read_bytes() for path in candidates]
        for dest, payload in zip(destinations, payloads):
            compare(dest.read_bytes(), payload)
        backups[0].parent.mkdir(parents=True, exist_ok=True)
        for dest, backup, expected in zip(destinations, backups, ORIGINAL):
            if backup.exists():
                if not backup.is_file() or digest(backup) != expected:
                    raise ValueError('Sauvegarde existante différente ; écrasement refusé.')
            else:
                with backup.open('xb') as file:
                    file.write(dest.read_bytes())
                if digest(backup) != expected or digest(dest) != expected:
                    raise ValueError('Sauvegarde des icônes non vérifiée.')
    with marker.open('x', encoding='utf-8') as file:
        json.dump({'format': 'wcf-icons-transaction-v1'}, file)
        file.flush()
        os.fsync(file.fileno())
    try:
        for dest, payload, expected in zip(destinations, payloads, wanted):
            replace_verified(dest, payload, expected)
        marker.unlink()
    except Exception:
        recover(target, running=running)
        raise
    return 'restored' if restore else 'applied'
