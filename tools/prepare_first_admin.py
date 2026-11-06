"""Prepare private first-admin SQL locally; never connects to a database."""
import argparse
import getpass
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def admin_sql(username, password_hash):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', username):
        raise ValueError('Identifiant invalide')
    if not re.fullmatch(rb'\$2[aby]\$12\$[./A-Za-z0-9]{53}', password_hash):
        raise ValueError('Hachage bcrypt coût 12 attendu')
    # Named lock serializes separate executions on MySQL 5.7. Existing admins,
    # including disabled admins, require a separate recovery procedure.
    return f"""-- PRIVATE: run only in the intended schema after checking a backup.
-- No account is created if an admin or the requested username already exists.
SET @wcf_bootstrap_lock = GET_LOCK('wcf_first_admin', 10);
START TRANSACTION;
INSERT INTO wcf_members (username,password_hash,role,enabled)
SELECT '{username}', X'{password_hash.hex()}', 'admin', 1
WHERE @wcf_bootstrap_lock = 1
AND (SELECT MAX(version) FROM wcf_schema_versions) = 2
AND NOT EXISTS (SELECT 1 FROM wcf_members WHERE role='admin' OR username='{username}');
SELECT ROW_COUNT() AS accounts_created;
COMMIT;
SELECT RELEASE_LOCK('wcf_first_admin') AS bootstrap_lock_released;
-- Expect accounts_created=1 once, then 0. Verify role and enabled without displaying the hash.
SELECT id,username,role,enabled FROM wcf_members WHERE username='{username}';
"""


def prepare(php, username, output, password):
    output = Path(output).resolve()
    if output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Choisir un nouveau fichier privé hors du dépôt')
    raw = password.encode('utf-8')
    if not 12 <= len(raw) <= 72 or b'\0' in raw:
        raise ValueError('Mot de passe : 12 à 72 octets UTF-8 sans NUL')
    script = 'echo password_hash(stream_get_contents(STDIN), PASSWORD_BCRYPT, ["cost"=>12]);'
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    result = subprocess.run([str(php), '-n', '-r', script], input=raw, capture_output=True,
                            timeout=20, check=True, creationflags=flags)
    sql = admin_sql(username, result.stdout)
    # Exclusive creation prevents replacing any pre-existing private file.
    with output.open('x', encoding='utf-8', newline='\n') as target:
        target.write(sql)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--php', type=Path, required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if not sys.stdin.isatty():
            raise ValueError('Exécuter dans un terminal interactif privé ; aucun mot de passe en argument')
        password = getpass.getpass('Mot de passe du premier administrateur : ')
        if password != getpass.getpass('Confirmer : '):
            raise ValueError('Les mots de passe diffèrent')
        output = prepare(args.php, args.username, args.output, password)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # Never print subprocess diagnostics, hashes or passwords.
        parser.exit(1, 'Préparation refusée : ' + (str(error) if isinstance(error, ValueError) else type(error).__name__) + '\n')
    print('SQL privé préparé : ' + str(output))
    print('Aucune connexion ni création de compte effectuée. Exécuter manuellement après sauvegarde.')


if __name__ == '__main__':
    main()
