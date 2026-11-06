"""Small Windows bootstrap. No PowerShell execution-policy changes or elevation."""
from pathlib import Path
import os
import subprocess
import sys

PILLOW_VERSION = "12.3.0"


def main():
    if sys.version_info < (3, 10):
        print("Python 3.10 ou plus requis. Installation : https://www.python.org/downloads/windows/", file=sys.stderr)
        return 1
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        print("Ce lanceur est destine a Windows. Autres systemes : python tools/diagnostic.py --help", file=sys.stderr)
        return 1
    repo = Path(__file__).resolve().parents[1]
    env = Path(os.environ["LOCALAPPDATA"]) / "WoWCartoDiagnostic" / f"python{sys.version_info.major}{sys.version_info.minor}-pillow{PILLOW_VERSION}"
    python = env / "Scripts" / "python.exe"
    try:
        if not python.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
        probe = subprocess.run([str(python), "-c",
                                f"import importlib.metadata as m, sys; sys.exit(0 if any(d.metadata['Name'].casefold() == 'pillow' and d.version == '{PILLOW_VERSION}' for d in m.distributions()) else 1)"])
        if probe.returncode:
            print("Installation initiale de Pillow via HTTPS (PyPI). Aucune donnee du jeu n est envoyee.", flush=True)
            subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                            "--only-binary=:all:", "--requirement", str(repo / "requirements.txt")], check=True)
        return subprocess.run([str(python), str(repo / "tools" / "diagnostic.py"), *sys.argv[1:]]).returncode
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Preparation impossible : {exc}. Verifiez Python et l acces HTTPS a PyPI.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
