"""Constrained Windows lab configuration and passive process measurements.

Requires a verified private snapshot and an already configured blocking firewall.
Never changes security settings, launches a process or modifies the installed app.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import zlib

from tools.inspect_native_config import KEY, inspect
from tools.local_install import checked_path, digest, EXPECTED_SHA256

CONFIGS = ('database/Francais/url.bin', 'database/US/url.bin')


def loopback_config(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Port invalide')
    host = f'http://127.0.0.1:{port}/'.encode('ascii')
    plain = b'\r\n'.join([b'1200', b'noreply@example.invalid', host, b'', b'manifest',
                           host + b'upload', host, b'false', b'Local isolated fixture'])
    encoded = zlib.compress(bytes(b ^ KEY[i % len(KEY)] for i, b in enumerate(plain)))
    inspect(encoded)
    return encoded


def blocking_firewall(executable):
    if os.name != 'nt':
        raise ValueError('Le contrôle du laboratoire nécessite Windows')
    script = r"""
$ErrorActionPreference='Stop'
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
if ($profiles.Count -ne 3 -or @($profiles | Where-Object { $_.Enabled -ne 'True' }).Count) { exit 2 }
$found = $false
foreach ($rule in Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Outbound -Action Block) {
    $app = $rule | Get-NetFirewallApplicationFilter
    if ($app.Program -ne $env:WCF_LAB_EXECUTABLE -or [string]$rule.Profile -ne 'Any') { continue }
    $addr = $rule | Get-NetFirewallAddressFilter
    $port = $rule | Get-NetFirewallPortFilter
    $iface = $rule | Get-NetFirewallInterfaceFilter
    $type = $rule | Get-NetFirewallInterfaceTypeFilter
    if ([string]$addr.RemoteAddress -eq 'Any' -and [string]$addr.LocalAddress -eq 'Any' -and
        [string]$port.Protocol -eq 'Any' -and [string]$port.RemotePort -eq 'Any' -and
        [string]$port.LocalPort -eq 'Any' -and [string]$iface.InterfaceAlias -eq 'Any' -and
        [string]$type.InterfaceType -eq 'Any') { $found=$true; break }
}
if (!$found) { exit 3 }
"""
    env = dict(os.environ, WCF_LAB_EXECUTABLE=str(executable))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=30, env=env,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise ValueError('Pare-feu non vérifié ; aucune protection ne sera modifiée')


def lab(copy, snapshot, *, require_closed=True):
    import psutil
    copy, snapshot = map(checked_path, (copy, snapshot))
    manifest = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 'wcf-private-snapshot-v1' or copy != checked_path(manifest['sources']['copie-test']):
        raise ValueError('La copie doit correspondre à la copie-test de cet instantané')
    exe = checked_path(copy / 'WowCartographe.exe')
    if digest(exe) != EXPECTED_SHA256:
        raise ValueError('Exécutable de référence différent')
    if require_closed:
        for process in psutil.process_iter(['exe']):
            try:
                if process.info['exe'] and Path(process.info['exe']).resolve() == exe:
                    raise ValueError('Fermer la copie avant de modifier sa configuration')
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
    blocking_firewall(exe)
    originals = {}
    for relative in CONFIGS:
        saved = checked_path(snapshot / 'copie-test' / relative)
        current = checked_path(copy / relative)
        metadata = next(f for f in manifest['files'] if f['root'] == 'copie-test' and f['path'] == relative)
        if digest(saved) != metadata['sha256']:
            raise ValueError('Sauvegarde de configuration incohérente')
        inspect(saved.read_bytes())
        originals[relative] = (current, saved.read_bytes())
    return exe, originals


def configure(copy, snapshot, port, restore=False):
    exe, originals = lab(copy, snapshot)
    fixture = loopback_config(port)
    for path, original in originals.values():
        if path.read_bytes() not in (original, fixture):
            raise ValueError('Configuration modifiée depuis la sauvegarde : écrasement refusé')
    changed = []
    try:
        for path, original in originals.values():
            before = path.read_bytes()
            payload = original if restore else fixture
            temporary = path.with_name('url.bin.wcf-new-' + str(time.time_ns()))
            with temporary.open('xb') as handle:
                handle.write(payload)
            os.replace(temporary, path)
            changed.append((path, before))
            if path.read_bytes() != payload:
                raise ValueError('Écriture de configuration incohérente')
    except Exception:
        for path, before in reversed(changed):
            path.write_bytes(before)
        raise
    return {'files': len(changed), 'restored': restore, 'loopback_only': not restore,
            'firewall_policy_verified': True, 'native_runtime_verified': False}


def measure(copy, snapshot, pid, seconds, label, output):
    import psutil
    exe, _ = lab(copy, snapshot, require_closed=False)
    output = checked_path(output)
    if output.is_relative_to(checked_path(copy)) or output.is_relative_to(checked_path(snapshot)) or output.exists():
        raise ValueError('Nouveau rapport hors des fichiers natifs et sauvegardes requis')
    if not 1 <= seconds <= 60:
        raise ValueError('Mesure limitée à 1–60 secondes')
    process = psutil.Process(pid)
    started = process.create_time()
    samples = []
    begin = time.perf_counter()
    first_cpu = process.cpu_times()
    while True:
        if process.create_time() != started or checked_path(process.exe()) != exe:
            raise ValueError('Le PID ne correspond plus à la copie contrôlée')
        memory = process.memory_info()
        cpu = process.cpu_times()
        elapsed = time.perf_counter() - begin
        samples.append({'elapsed_seconds': elapsed, 'working_set_bytes': memory.rss,
                        'private_bytes': memory.private,
                        'cpu_seconds': cpu.user + cpu.system - first_cpu.user - first_cpu.system})
        if elapsed >= seconds:
            break
        time.sleep(min(.25, seconds - elapsed))
    report = {'format': 'wcf-passive-process-measurement-v1', 'label': label,
              'executable_sha256': digest(exe), 'process_created_at': started,
              'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'samples': samples, 'elapsed_seconds': elapsed,
              'cpu_one_core_percent': 100 * samples[-1]['cpu_seconds'] / elapsed,
              'peak_private_bytes': max(s['private_bytes'] for s in samples),
              'visual_completion_verified': False, 'startup_latency_measured': False}
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)
    return {'samples': len(samples), 'elapsed_seconds': elapsed,
            'visual_completion_verified': False, 'startup_latency_measured': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    config = sub.add_parser('configure')
    config.add_argument('--port', type=int, default=8766)
    config.add_argument('--restore', action='store_true')
    sample = sub.add_parser('measure')
    sample.add_argument('--pid', type=int, required=True)
    sample.add_argument('--seconds', type=int, default=10)
    sample.add_argument('--label', required=True)
    sample.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'configure':
            result = configure(args.copy, args.snapshot, args.port, args.restore)
        else:
            result = measure(args.copy, args.snapshot, args.pid, args.seconds, args.label, args.output)
    except (OSError, ValueError, KeyError, StopIteration) as error:
        parser.exit(1, 'Laboratoire refusé : ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
