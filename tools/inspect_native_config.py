#!/usr/bin/env python3
"""Inspect a WoWCartographe 1.20 url.bin locally, without disclosing its contents."""
import argparse
import hashlib
import json
import zlib
from pathlib import Path
from urllib.parse import urlsplit

MAX_INPUT = 1024 * 1024
MAX_DECODED = 64 * 1024
KEY = hashlib.md5(b"cartokey", usedforsecurity=False).hexdigest().encode("ascii")  # historical obfuscation, not security


def inspect(data):
    if len(data) > MAX_INPUT:
        raise ValueError("fichier trop volumineux")
    stream = zlib.decompressobj()
    try:
        decoded = stream.decompress(data, MAX_DECODED + 1)
    except zlib.error as exc:
        raise ValueError("flux zlib invalide") from exc
    if len(decoded) > MAX_DECODED or stream.unconsumed_tail:
        raise ValueError("contenu décompressé trop volumineux")
    if not stream.eof or stream.unused_data:
        raise ValueError("flux tronqué ou données supplémentaires")
    plain = bytes(value ^ KEY[index % len(KEY)] for index, value in enumerate(decoded))
    lines = plain.splitlines()
    if len(lines) != 9 or lines[0] != b"1200":
        raise ValueError("structure 1.20 non reconnue")
    schemes = []
    for index in (2, 5, 6):
        try:
            parsed = urlsplit(lines[index].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("adresse mal formée") from exc
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise ValueError("adresse non reconnue")
        schemes.append(parsed.scheme.lower())
    if lines[7].lower() not in (b"true", b"false"):
        raise ValueError("indicateur non reconnu")
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "compressed_bytes": len(data),
        "decoded_bytes": len(plain),
        "version_marker": "1200",
        "line_count": len(lines),
        "url_schemes_at_lines_3_6_7": schemes,
        "boolean_at_line_8": lines[7].decode("ascii").lower(),
        "contents_redacted": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Chemin local vers url.bin")
    args = parser.parse_args()
    try:
        with args.file.open("rb") as source:
            data = source.read(MAX_INPUT + 1)
        result = inspect(data)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Inspection impossible : {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
