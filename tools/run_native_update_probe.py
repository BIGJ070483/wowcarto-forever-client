"""Run bounded fictitious update cases through the original Windows dialog.

Uses the explicitly selected, previously verified Win32 controller. Requires the
isolated copy already open, an external-blocking firewall and loopback config.
Only res/wcf_probe.txt, containing our exact fixture, may be replaced by tests.
This is not an annotation import test or a public update server.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import threading
import time

from tools.native_lab import lab, loopback_config
from tools.native_update_manifest import (
    FileRecord, NativeManifest, SYNTHETIC_PAYLOAD, build_manifest,
    make_fixture_server, parse_manifest, synthetic_fixture,
)


def load_controller(path):
    spec = importlib.util.spec_from_file_location('wcf_selected_controller', path)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    return control


def update_window(control):
    windows = [w for w in control.windows() if w['visible'] and w['classname'] == 'TForm_update']
    if len(windows) != 1:
        raise ValueError('Ouvrir le dialogue de mise à jour dans la copie isolée')
    return windows[0]['hwnd']


def click_update(control):
    c, w, u = control.c, control.w, control.u
    hwnd = update_window(control)
    buttons = []
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    @callback_type
    def visit(child, _):
        title, kind = c.create_unicode_buffer(256), c.create_unicode_buffer(64)
        u.GetWindowTextW(child, title, 256); u.GetClassNameW(child, kind, 64)
        if kind.value == 'TButton' and title.value == 'Se connecter et télécharger les mises à jour' and u.IsWindowVisible(child):
            buttons.append(int(child))
        return True
    u.EnumChildWindows.argtypes = [w.HWND, callback_type, w.LPARAM]
    u.EnumChildWindows(hwnd, visit, 0)
    if len(buttons) != 1 or not control.target_window(buttons[0]):
        raise ValueError('Bouton natif absent ou ambigu')
    control.focus(hwnd)
    u.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, c.POINTER(c.c_size_t)]
    u.SendMessageTimeoutW.restype = w.LPARAM
    result = c.c_size_t()
    if not u.SendMessageTimeoutW(buttons[0], 0xF5, 0, 0, 2, 15000, c.byref(result)):
        raise ValueError('Commande native expirée ; résultat inconnu, aucune répétition automatique')
    return hwnd


def run(copy, snapshot, controller, output, port):
    exe, originals = lab(copy, snapshot, require_closed=False)
    copy, output = Path(copy).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(copy) or output.is_relative_to(Path(snapshot).resolve()):
        raise ValueError('Nouveau dossier de preuves hors des fichiers natifs requis')
    if any(path.read_bytes() != loopback_config(port) for path, _ in originals.values()):
        raise ValueError('Les deux configurations doivent être redirigées vers cette sonde')
    control = load_controller(controller)
    if Path(control.TARGET).resolve() != exe:
        raise ValueError('Le contrôleur sélectionné vise une autre application')
    update_window(control)
    target = copy / 'res' / 'wcf_probe.txt'
    if target.exists() and target.read_bytes() != SYNTHETIC_PAYLOAD:
        raise ValueError('Un fichier non fictif occupe la cible ; aucune modification')
    output.mkdir(parents=True, exist_ok=False)
    if target.exists():
        target.rename(output / 'fixture-before.txt')
    results = []
    with make_fixture_server(port) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for name in ('raw', 'repeat', 'zlib', 'bad_checksum', 'missing_eof'):
                if name == 'zlib':
                    if target.read_bytes() != SYNTHETIC_PAYLOAD:
                        raise ValueError('Fixture précédente incohérente')
                    target.rename(output / 'fixture-before-zlib.txt')
                manifest, payload = synthetic_fixture(port, compressed=name == 'zlib')
                if name == 'bad_checksum':
                    parsed = parse_manifest(manifest)
                    fields = list(parsed.files[0].fields)
                    fields[2] = hashlib.md5(b'EXPECTED_NEW_FIXTURE', usedforsecurity=False).hexdigest().encode()
                    manifest = build_manifest(NativeManifest(tuple(FileRecord(tuple(fields)) if isinstance(e, FileRecord) else e for e in parsed.entries)))
                    payload = b'CORRUPTED_SYNTHETIC_PAYLOAD'
                elif name == 'missing_eof':
                    manifest = manifest.replace(b'EOF\r\n', b'')
                server.manifest, server.payload = manifest, payload
                initial = len(server.observations)
                begin = time.perf_counter()
                hwnd = click_update(control)
                # Refresh and bind capture to a freshly enumerated exact target.
                if hwnd != update_window(control):
                    raise ValueError('Dialogue natif remplacé ; arrêter avant capture')
                from PIL import ImageGrab
                with (output / (name + '.png')).open('xb') as image:
                    ImageGrab.grab(window=hwnd).save(image, format='PNG')
                exact = target.exists() and target.read_bytes() == SYNTHETIC_PAYLOAD
                if not exact:
                    raise ValueError('Le fichier fictif est absent ou différent après ' + name)
                result = {'case': name, 'requests': server.observations[initial:],
                          'elapsed_seconds': time.perf_counter() - begin,
                          'fixture_preserved': exact, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
                results.append(result)
                expected = ['manifest'] if name in ('repeat', 'missing_eof') else ['manifest', 'payload']
                result['expected_request_sequence'] = result['requests'] == expected
                (output / (name + '-result.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
                if not result['expected_request_sequence']:
                    raise ValueError('Séquence réseau inattendue pour ' + name)
                print(json.dumps(result), flush=True)
        finally:
            server.shutdown(); thread.join(timeout=3)
    report = {'format': 'wcf-native-update-probe-v1', 'executable_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
              'cases': results, 'annotations_imported': False, 'firewall_policy_unchanged': True}
    (output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--copy', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--controller', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    try:
        run(args.copy, args.snapshot, args.controller, args.output, args.port)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, 'Essai arrêté : ' + str(error) + '\n')


if __name__ == '__main__':
    main()
