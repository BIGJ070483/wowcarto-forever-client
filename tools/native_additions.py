"""Read the observed v001 additions structure without changing native files.

This is a research reader, not a complete validator or an importer. Bytes are
kept intact so callers must choose an encoding and handle private data explicitly.
"""
from dataclasses import dataclass

from tools.inspect_native_additions import HEADER, MAX_BYTES, MAX_LINE, SECOND_HEADER


@dataclass(frozen=True)
class Addition:
    fields: tuple[bytes, ...]

    @property
    def name(self) -> bytes:
        return self.fields[0]

    @property
    def category_code(self) -> int:
        return int(self.fields[3])

    @property
    def classification(self) -> bytes:
        return self.fields[12]

    @property
    def comment_lines(self) -> tuple[bytes, ...]:
        return self.fields[15:]


@dataclass(frozen=True)
class AdditionsFile:
    headers: tuple[bytes, bytes]
    records: tuple[Addition, ...]


def parse_additions(data: bytes) -> AdditionsFile:
    if len(data) > MAX_BYTES or b'\x00' in data:
        raise ValueError('Fichier trop volumineux ou contenant un octet nul')
    lines = data.splitlines()
    if any(len(line) > MAX_LINE for line in lines):
        raise ValueError('Ligne trop longue')
    if len(lines) < 3:
        raise ValueError('En-têtes ou annotations absents')
    header = HEADER.fullmatch(lines[0])
    if not header or header.group(1) != b'001' or not SECOND_HEADER.fullmatch(lines[1]):
        raise ValueError('En-têtes v001 non reconnus')
    records = []
    for line in lines[2:]:
        fields = tuple(line.split(b'|'))
        if len(fields) < 16 or not fields[0] or (len(fields[3]) > 10 or not fields[3].isdigit()):
            raise ValueError('Annotation de structure non reconnue')
        records.append(Addition(fields))
    return AdditionsFile((lines[0], lines[1]), tuple(records))
