"""Read experimentally established native tile positions without native writes.

Only explicitly observed identifiers are supported. Coordinates are local to a
256x256 standard tile; 256 stored units per standard pixel. This does not
establish a geographic projection or WoW in-game percentage coordinates.
"""
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from tools.inspect_native_additions import MAX_BYTES
from tools.native_additions import Addition, parse_additions

OBSERVED_TILES = {
    7: 'azeroth07.jpg', 16: 'azeroth16.jpg', 17: 'azeroth17.jpg',
    18: 'azeroth18.jpg', 27: 'azeroth27.jpg',
    106: 'elf06.jpg', 107: 'elf07.jpg',
}


@dataclass(frozen=True)
class TilePosition:
    tile_identifier: int
    tile_basename: str
    raw_x: int
    raw_y: int
    pixel_x: float
    pixel_y: float
    standard_tile_size: int = 256
    experimental: bool = True


def observed_position(record: Addition) -> TilePosition:
    values = []
    for index in (2, 4, 5):
        field = record.fields[index]
        if not field or len(field) > 5 or not field.isdigit():
            raise ValueError('Champ de position non pris en charge')
        values.append(int(field))
    identifier, x, y = values
    if identifier not in OBSERVED_TILES:
        raise ValueError('Identifiant de tuile non observe')
    # Supported software domain; endpoints are not a claim of native validation.
    if not (0 <= x < 65536 and 0 <= y < 65536):
        raise ValueError('Coordonnee hors du domaine experimental')
    return TilePosition(identifier, OBSERVED_TILES[identifier], x, y, x / 256, y / 256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        with args.file.open('rb') as source:
            parsed = parse_additions(source.read(MAX_BYTES + 1))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Lecture impossible : {exc}\n')
    results = []
    for index, record in enumerate(parsed.records, 1):
        try:
            item = {'record_position': index, 'status': 'observed_mapping', **asdict(observed_position(record))}
        except ValueError:
            item = {'record_position': index, 'status': 'unsupported'}
        results.append(item)
    print(json.dumps({'positions': results, 'names_and_comments_redacted': True}, indent=2))


if __name__ == '__main__':
    main()
