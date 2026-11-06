"""Build a source-only installation kit. Never includes the native client/assets."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
README = """WoWCarto Forever — kit de préparation locale expérimental

Ce kit contient uniquement le préparateur écrit pour ce projet, pas le client,
ses DLL, ses cartes ou des annotations. Il nécessite Windows et Python 3.10+.
Ce n'est pas encore la livraison complète : synchronisation native, interface
hors ligne, icônes et performances restent soumis à leur recette Windows.

Fermer WoWCartographe. Dans un terminal, utiliser des dossiers NOUVEAUX :

python local_install.py prepare --offline --source "C:\\Program Files (x86)\\WoWCartographe" --overlay "%LOCALAPPDATA%\\VirtualStore\\Program Files (x86)\\WoWCartographe" --target "%LOCALAPPDATA%\\WoWCartoForever\\version-test" --backup "%USERPROFILE%\\Documents\\WoWCarto-sauvegardes\\installation-test"

Les variables %...% de cet exemple sont développées par cmd.exe. Dans PowerShell,
utiliser $env:LOCALAPPDATA et $env:USERPROFILE, ou des chemins absolus.
Omettre --overlay seulement si le VirtualStore n'existe pas.

La préparation vérifie l'empreinte du client de référence, refuse les liens,
les dossiers imbriqués et tout écrasement. Une sauvegarde distincte précède la
copie. Les fichiers VirtualStore sont appliqués à la copie après sauvegarde des
deux versions ; aucune conversion des données ou fusion d'annotations.
Le logiciel n'est jamais lancé automatiquement, aucun pare-feu n'est modifié.
--offline redirige les fichiers de configuration réseau des bases vers la
boucle locale ; ce n'est pas une preuve d'isolation de tout le processus.
Vérifier une règle de blocage ciblée avant de lancer la copie pour les essais.

Vérification : python local_install.py verify "CHEMIN_COPIE"
Un code 2 signale des fichiers ajoutés/manquants/modifiés, pas leur suppression.

Retrait conservateur (client fermé, destination nouvelle sur le même volume) :
python local_install.py archive "CHEMIN_COPIE" "CHEMIN_CONSERVATION"
Tout le dossier est déplacé, annotations modifiées comprises. Aucun fichier
natif n'est supprimé. La sauvegarde distincte et l'installation source restent.

Mise à niveau : préparer une NOUVELLE version, conserver l'ancienne, utiliser sa
copie comme source de données seulement après vérification et sauvegarde.
Une préparation échouée conserve son dossier INCOMPLETE pour diagnostic. Ne pas
le lancer ; recommencer dans un autre dossier. Ce kit ne répare pas les bases.

Aucun compte, mot de passe ou service public n'est créé par ce kit.
"""


def build(output):
    output = Path(output)
    # Fixed allowlist: never traverse native/user/output directories.
    payload = (ROOT / 'tools' / 'local_install.py').read_bytes()
    with output.open('xb') as handle, zipfile.ZipFile(handle, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('local_install.py', payload)
        archive.writestr('LIRE-MOI.txt', README.encode('utf-8'))
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        checksum = build(args.output)
    except OSError as error:
        parser.exit(1, 'Création refusée : ' + str(error) + '\n')
    print('Kit expérimental créé ; SHA-256 ' + checksum)


if __name__ == '__main__':
    main()
