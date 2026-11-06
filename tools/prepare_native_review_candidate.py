"""Prepare one-source v001 research candidate from an authenticated review export.

The export JSON is private review evidence, not a signature or identity proof.
Never installs, publishes, contacts a server or merges different source headers.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path

from tools.native_additions import parse_additions


def compose_candidate(original, selection):
    parsed = parse_additions(original)
    if (selection.get('format') != 'wcf-server-review-v1' or selection.get('native_update') is not False
            or not isinstance(selection.get('records'), list) or not 1 <= len(selection['records']) <= 2000):
        raise ValueError('Sélection privée de revue non reconnue ou vide')
    source_hash = hashlib.sha256(original).hexdigest()
    chosen = set()
    for record in selection['records']:
        ordinal = record.get('ordinal')
        if (type(ordinal) is not int or not 1 <= ordinal <= len(parsed.records)
                or ordinal in chosen or record.get('source_sha256') != source_hash
                or type(record.get('event_id')) is not int or record['event_id'] < 1):
            raise ValueError('Provenance ou position de revue incohérente')
        raw = b'|'.join(parsed.records[ordinal - 1].fields)
        try:
            reviewed = base64.b64decode(record['raw_base64'], validate=True)
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError('Octets de revue non reconnus') from error
        if reviewed != raw or hashlib.sha256(raw).hexdigest() != record.get('sha256'):
            raise ValueError('Intégrité de la ligne sélectionnée incohérente')
        chosen.add(ordinal)
    lines = original.splitlines(keepends=True)
    candidate = b''.join(lines[:2] + [lines[n + 1] for n in sorted(chosen)])
    if len(parse_additions(candidate).records) != len(chosen):
        raise ValueError('Structure de la sélection incohérente')
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('review', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        if args.output.resolve().is_relative_to(root) or args.output.exists():
            raise ValueError('Nouveau fichier privé hors du dépôt requis')
        if args.source.stat().st_size > 8 * 1024 * 1024 or args.review.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('Budget de lecture dépassé')
        candidate = compose_candidate(args.source.read_bytes(), json.loads(args.review.read_text(encoding='utf-8')))
        with args.output.open('xb') as output:
            output.write(candidate)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'Préparation refusée : ' + str(error) + '\n')
    print(json.dumps({'bytes': len(candidate), 'sha256': hashlib.sha256(candidate).hexdigest(),
                      'installed': False, 'published': False}))


if __name__ == '__main__':
    main()
