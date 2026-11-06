"""Per-user Windows launcher for the locally owned native client.

The launcher does not ship the native executable or maps. It prepares a fresh
copy and verified backup, disables known network endpoints, and refuses to
launch until a targeted outbound Windows Firewall block is present.
"""
import base64
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tools.local_install import EXE, EXPECTED_SHA256, checked_path, digest, offline_config, prepare
from tools.windows_icons import change_icons, recover as recover_icons

def application_dir(frozen, executable, localappdata):
    """Keep state next to the installed launcher even across Windows redirection."""
    executable = Path(executable)
    if frozen and executable.name.lower() == 'wowcartoforever.exe' and executable.parent.name.lower() == 'wowcartoforever':
        return executable.parent
    return Path(localappdata) / 'WoWCartoForever'


APP_DIR = application_dir(getattr(sys, 'frozen', False), sys.executable, os.environ.get('LOCALAPPDATA', ''))
STATE = APP_DIR / 'installation.json'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# Only an exact previously published project launcher may be upgraded in place.
UPGRADEABLE_LAUNCHERS = frozenset({'6465817c8f01d013b105673fc6f4d429e3e12e451394a2bdf16ac1c77aee5892',
                                   '39a8505f37c33d168e916ad4bf0f6cc11a1e38b79acc573bd1198dd661cc3d3d'})


def bundled_icons():
    root = getattr(sys, '_MEIPASS', None)
    path = Path(root) / 'wcf_icons' if root else None
    return path if path and path.is_dir() else None


def installed_client(target, *, allow_pending=False):
    target = checked_path(target)
    if not allow_pending and (target / 'wcf-icones-en-cours.json').exists():
        raise ValueError('Récupération des icônes nécessaire avant le lancement.')
    manifest = target / 'install-manifest.json'
    if (target / 'INCOMPLETE').exists() or not manifest.is_file() or manifest.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Installation absente ou incomplète.')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('format') != 'wcf-local-install-v1' or data.get('offline_endpoints') is not True:
        raise ValueError('Cette installation ne dispose pas de la configuration locale attendue.')
    client = checked_path(target / 'client')
    exe = checked_path(client / EXE)
    if not exe.is_file() or digest(exe) != EXPECTED_SHA256:
        raise ValueError('Le client original attendu est absent ou a été modifié.')
    configs = list((client / 'database').glob('*/url.bin'))
    if len(configs) < 2 or any(checked_path(p).read_bytes() != offline_config() for p in configs):
        raise ValueError('Une configuration réseau n’est plus en mode local.')
    return exe


def firewall_blocked(executable):
    script = r"""
$ErrorActionPreference='Stop'
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
if ($profiles.Count -ne 3 -or @($profiles | Where-Object { $_.Enabled -ne 'True' }).Count) { exit 2 }
foreach ($rule in Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Outbound -Action Block) {
  $app = $rule | Get-NetFirewallApplicationFilter
  if ($app.Program -ne $env:WCF_OFFLINE_EXECUTABLE -or [string]$rule.Profile -ne 'Any') { continue }
  $addr = $rule | Get-NetFirewallAddressFilter; $port = $rule | Get-NetFirewallPortFilter
  $iface = $rule | Get-NetFirewallInterfaceFilter; $type = $rule | Get-NetFirewallInterfaceTypeFilter
  if ([string]$addr.RemoteAddress -eq 'Any' -and [string]$addr.LocalAddress -eq 'Any' -and
      [string]$port.Protocol -eq 'Any' -and [string]$port.RemotePort -eq 'Any' -and
      [string]$port.LocalPort -eq 'Any' -and [string]$iface.InterfaceAlias -eq 'Any' -and
      [string]$type.InterfaceType -eq 'Any') { exit 0 }
}
exit 3
"""
    env = dict(os.environ, WCF_OFFLINE_EXECUTABLE=str(executable))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=30, env=env, creationflags=NO_WINDOW)
    return result.returncode == 0


def request_firewall_block(executable):
    # Only a narrowly scoped outbound BLOCK is added, after the user clicks the
    # button and confirms the standard Windows administrator prompt.
    path = str(executable).replace("'", "''")
    identity = hashlib.sha256(str(executable).encode('utf-8')).hexdigest()[:16]
    script = ("$ErrorActionPreference='Stop'; New-NetFirewallRule -Name 'WCF-Offline-" + identity +
              "' -DisplayName 'WoWCarto Forever - hors ligne' -Direction Outbound -Action Block "
              "-Profile Any -Program '" + path + "' | Out-Null")
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    args = '-NoProfile -NonInteractive -EncodedCommand ' + encoded
    result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', 'powershell.exe', args, None, 0)
    if result <= 32:
        raise OSError('La demande Windows n’a pas abouti ; aucune protection n’a été désactivée.')


def save_state(target):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    checked_path(APP_DIR)
    temp = APP_DIR / ('installation-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f') + '.tmp')
    with temp.open('x', encoding='utf-8') as file:
        json.dump({'format': 'wcf-windows-launcher-v1', 'target': str(target)}, file)
    os.replace(temp, STATE)


def install_launcher(source, destination_dir, create_shortcuts):
    """Install our launcher without replacing a pre-existing different file."""
    source, destination_dir = checked_path(source), checked_path(destination_dir)
    if not source.is_file():
        raise ValueError('L’exécutable du lanceur est introuvable.')
    destination_dir.mkdir(parents=True, exist_ok=True)
    installed = checked_path(destination_dir / 'WoWCartoForever.exe')
    if installed.exists():
        if not installed.is_file():
            raise ValueError('Un autre lanceur existe déjà ; aucune version n’a été écrasée.')
        old_hash, new_hash = digest(installed), digest(source)
        if old_hash != new_hash:
            if old_hash not in UPGRADEABLE_LAUNCHERS:
                raise ValueError('Un autre lanceur existe déjà ; aucune version n’a été écrasée.')
            backup = checked_path(destination_dir / ('WoWCartoForever-sauvegarde-' + old_hash[:16] + '.exe'))
            if backup.exists():
                if not backup.is_file() or digest(backup) != old_hash:
                    raise ValueError('La sauvegarde du lanceur est incohérente.')
            else:
                with installed.open('rb') as previous, backup.open('xb') as saved:
                    shutil.copyfileobj(previous, saved)
                if digest(backup) != old_hash or digest(installed) != old_hash:
                    raise ValueError('Sauvegarde du lanceur non vérifiée.')
            staged = destination_dir / ('WoWCartoForever-nouveau-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f') + '.tmp')
            with source.open('rb') as current, staged.open('xb') as candidate:
                shutil.copyfileobj(current, candidate)
            if digest(staged) != new_hash or digest(installed) != old_hash:
                raise ValueError('Mise à jour du lanceur incohérente.')
            os.replace(staged, installed)
            if digest(installed) != new_hash:
                rollback = destination_dir / ('WoWCartoForever-retour-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f') + '.tmp')
                with backup.open('rb') as saved, rollback.open('xb') as target:
                    shutil.copyfileobj(saved, target)
                if digest(rollback) == old_hash:
                    os.replace(rollback, installed)
                raise ValueError('Mise à jour du lanceur non vérifiée.')
    else:
        with source.open('rb') as original, installed.open('xb') as destination:
            shutil.copyfileobj(original, destination)
        if digest(source) != digest(installed):
            raise ValueError('La copie du lanceur ne correspond pas à l’original.')
    create_shortcuts(installed)
    return installed


def create_windows_shortcuts(executable):
    script = r"""
$ErrorActionPreference='Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('DesktopDirectory'))) {
  if ([string]::IsNullOrWhiteSpace($folder) -or !(Test-Path -LiteralPath $folder -PathType Container)) { throw 'Dossier de raccourcis indisponible' }
  $linkPath = Join-Path $folder 'WoWCarto Forever.lnk'
  if (Test-Path -LiteralPath $linkPath) {
    $old = $shell.CreateShortcut($linkPath)
    if ($old.TargetPath -ne $env:WCF_LAUNCHER_TARGET) { throw 'Un raccourci différent existe déjà ; aucun écrasement' }
    continue
  }
  $link = $shell.CreateShortcut($linkPath)
  $link.TargetPath = $env:WCF_LAUNCHER_TARGET
  $link.WorkingDirectory = Split-Path -Parent $env:WCF_LAUNCHER_TARGET
  $link.Description = 'WoWCarto Forever — version locale'
  $link.Save()
  if (!(Test-Path -LiteralPath $linkPath -PathType Leaf)) { throw 'Création de raccourci échouée' }
}
"""
    env = dict(os.environ, WCF_LAUNCHER_TARGET=str(executable))
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=30, env=env, creationflags=NO_WINDOW)
    if result.returncode:
        raise OSError('Impossible de créer les raccourcis sans écraser un fichier existant.')


def load_state():
    if not STATE.exists():
        return None
    state = json.loads(checked_path(STATE).read_text(encoding='utf-8'))
    if state.get('format') != 'wcf-windows-launcher-v1' or not isinstance(state.get('target'), str):
        raise ValueError('La configuration locale du lanceur est invalide.')
    return checked_path(state['target'])


def source_overlay(source, default_source, localappdata):
    """A different selected client must not inherit another installation's data."""
    if checked_path(source) != checked_path(default_source):
        return None
    overlay = Path(localappdata) / 'VirtualStore' / 'Program Files (x86)' / 'WoWCartographe'
    return overlay if overlay.is_dir() else None


class Window:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('WoWCarto Forever — version locale')
        self.root.resizable(False, False)
        self.target = None
        self.source = tk.StringVar(value=str(Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'WoWCartographe'))
        self.status = tk.StringVar(value='Préparation de la version locale.')
        box = ttk.Frame(self.root, padding=18); box.grid(sticky='nsew')
        ttk.Label(box, text='WoWCarto Forever', font=('Segoe UI', 16, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(box, text='Client original 1.20 · cartes et annotations locales', wraplength=510).grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 14))
        ttk.Label(box, text='Installation originale :').grid(row=2, column=0, columnspan=2, sticky='w')
        ttk.Entry(box, textvariable=self.source, width=57).grid(row=3, column=0, sticky='ew', pady=4)
        ttk.Button(box, text='Choisir…', command=self.browse).grid(row=3, column=1, padx=(8, 0))
        self.install_button = ttk.Button(box, text='Préparer ma copie locale', command=self.install)
        self.install_button.grid(row=4, column=0, sticky='w', pady=8)
        self.block_button = ttk.Button(box, text='Activer le blocage réseau', command=self.block, state='disabled')
        self.block_button.grid(row=5, column=0, sticky='w', pady=4)
        self.launch_button = ttk.Button(box, text='Ouvrir WoWCartographe', command=self.launch, state='disabled')
        self.launch_button.grid(row=6, column=0, sticky='w', pady=4)
        self.icon_button = ttk.Button(box, text='Activer les icônes compactes expérimentales', command=self.apply_icons, state='disabled')
        self.icon_button.grid(row=7, column=0, sticky='w', pady=4)
        self.restore_icon_button = ttk.Button(box, text='Restaurer les icônes d’origine', command=self.restore_icons, state='disabled')
        self.restore_icon_button.grid(row=8, column=0, sticky='w', pady=4)
        ttk.Label(box, textvariable=self.status, wraplength=510).grid(row=9, column=0, columnspan=2, sticky='w', pady=(12, 0))
        ttk.Label(box, text='Aucun envoi communautaire. Icônes compactes : version expérimentale.', wraplength=510).grid(row=10, column=0, columnspan=2, sticky='w', pady=(8, 0))
        try:
            self.target = load_state()
            if self.target:
                installed_client(self.target, allow_pending=True)
                recover_icons(self.target)
                installed_client(self.target)
                if getattr(sys, 'frozen', False):
                    install_launcher(Path(sys.executable), APP_DIR, create_windows_shortcuts)
                self.ready()
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self.status.set(str(exc))

    def browse(self):
        path = filedialog.askdirectory(title='Choisir le dossier WoWCartographe original')
        if path:
            self.source.set(path)

    def ready(self):
        self.block_button.config(state='normal'); self.launch_button.config(state='normal')
        self.icon_button.config(state='normal' if bundled_icons() else 'disabled')
        self.restore_icon_button.config(state='normal')
        self.status.set('Copie prête. Active le blocage réseau avant la première ouverture.')

    def apply_icons(self):
        self.set_icons(False)

    def restore_icons(self):
        self.set_icons(True)

    def set_icons(self, restore):
        try:
            installed_client(self.target)
            assets = bundled_icons()
            if not restore and not assets:
                raise ValueError('Paquet d’icônes non disponible dans cet installateur.')
            outcome = change_icons(self.target, assets or self.target, restore=restore)
            self.status.set({'applied': 'Icônes expérimentales activées ; originaux sauvegardés.',
                             'restored': 'Icônes d’origine restaurées.',
                             'already': 'Les icônes demandées sont déjà actives.'}[outcome])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.status.set(str(exc))

    def install(self):
        source = self.source.get()
        self.install_button.config(state='disabled')
        self.status.set('Sauvegarde et préparation en cours…')
        def worker():
            try:
                tag = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                target = APP_DIR / ('client-' + tag)
                backup = Path(os.environ['USERPROFILE']) / 'Documents' / 'WoWCarto-sauvegardes' / ('installation-' + tag)
                default_source = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'WoWCartographe'
                overlay = source_overlay(source, default_source, os.environ['LOCALAPPDATA'])
                prepare(source, target, backup, overlay, offline=True)
                installed_client(target)
                save_state(target)
                if getattr(sys, 'frozen', False):
                    install_launcher(Path(sys.executable), APP_DIR, create_windows_shortcuts)
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): self.failed(error))
                return
            self.target = target
            self.root.after(0, self.ready)
        threading.Thread(target=worker, daemon=True).start()

    def failed(self, error):
        self.install_button.config(state='normal')
        self.status.set('Préparation arrêtée : ' + error)

    def block(self):
        try:
            executable = installed_client(self.target)
            if firewall_blocked(executable):
                self.status.set('Blocage réseau vérifié. Tu peux ouvrir WoWCartographe.')
                return
            if not messagebox.askokcancel('Blocage réseau', 'Windows va demander les droits administrateur pour bloquer les connexions sortantes de cette copie uniquement.'):
                return
            request_firewall_block(executable)
            self.status.set('Après validation de Windows, clique sur Ouvrir WoWCartographe.')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.status.set(str(exc))

    def launch(self):
        try:
            executable = installed_client(self.target)
            if not firewall_blocked(executable):
                self.status.set('Blocage réseau non vérifié. Active-le avant de lancer la copie.')
                return
            subprocess.Popen([str(executable)], cwd=str(executable.parent))
            self.status.set('WoWCartographe est ouvert en mode local.')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.status.set(str(exc))


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows est requis.')
    Window().root.mainloop()


if __name__ == '__main__':
    main()
