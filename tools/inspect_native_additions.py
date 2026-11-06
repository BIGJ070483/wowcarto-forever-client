#!/usr/bin/env python3
"""Describe an observed WoWCartographe additions file without revealing records."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

MAX_BYTES = 8 * 1024 * 1024
MAX_LINE = 64 * 1024
HEADER = re.compile(rb'^#v([0-9]{3})-id=[0-9]+-.+$')
SECOND_HEADER = re.compile(rb'^#[0-9]+-[0-9a-fA-F]+$')


def inspect(data):
    if len(data) > MAX_BYTES:
        raise ValueError('fichier trop volumineux')
    if b'\x00' in data:
        raise ValueError('octet nul : format non reconnu')
    lines = data.splitlines()
    if any(len(line) > MAX_LINE for line in lines):
        raise ValueError('ligne trop longue')
    if len(lines) < 3:
        raise ValueError('en-têtes ou annotations absents')
    header = HEADER.fullmatch(lines[0])
    if not header or not SECOND_HEADER.fullmatch(lines[1]):
        raise ValueError('en-têtes non reconnus')
    if any(not line or line.startswith(b'#') or b'|' not in line for line in lines[2:]):
        raise ValueError('ligne de données non reconnue')
    fields = Counter(len(line.split(b'|')) for line in lines[2:])
    return {
        'sha256': hashlib.sha256(data).hexdigest(),
        'bytes': len(data),
        'header_version': header.group(1).decode('ascii'),
        'record_lines': len(lines) - 2,
        'field_counts': dict(sorted(fields.items())),
        'crlf_count': data.count(b'\r\n'),
        'other_lf_count': data.count(b'\n') - data.count(b'\r\n'),
        'records_redacted': True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        with args.file.open('rb') as source:
            data = source.read(MAX_BYTES + 1)
        result = inspect(data)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Inspection impossible : {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
