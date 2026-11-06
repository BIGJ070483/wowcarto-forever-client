"""Compare two additions snapshots without printing private annotation contents.

Records are compared by position only; insertions, deletions and reordering make
that correspondence unreliable. This is an experimental read-only research tool.
"""
import argparse
import json
from pathlib import Path
import sys

from tools.inspect_native_additions import MAX_BYTES
from tools.native_additions import parse_additions


def compare(before: bytes, after: bytes) -> dict:
    left, right = parse_additions(before), parse_additions(after)
    if len(left.records) != len(right.records):
        raise ValueError('Nombre de lignes différent : correspondance inconnue')
    changes = []
    for number, (a, b) in enumerate(zip(left.records, right.records), 1):
        # Comment segments are a single logical field, not fixed columns.
        changed = [i + 1 for i, (x, y) in enumerate(zip(a.fields[:15], b.fields[:15])) if x != y]
        if a.comment_lines != b.comment_lines:
            changed.append(16)
        if changed:
            changes.append({'record_position': number, 'changed_fields': changed})
    return {'headers_changed': [a != b for a, b in zip(left.headers, right.headers)],
            'record_count': len(left.records), 'changed_records': changes,
            'matching': 'position only; reordering is not detected',
            'contents_redacted': True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    args = parser.parse_args()
    try:
        data = []
        for path in (args.before, args.after):
            with path.open('rb') as source:
                data.append(source.read(MAX_BYTES + 1))
        result = compare(*data)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Comparaison impossible : {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
