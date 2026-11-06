"""Read-only decoder for the observed WoWCartographe 1.20 master.loc.

The native resource tables and WOWCV101 fields are preserved separately from
user edits. No native writer or game-version conversion is provided here.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import struct
import zlib

from tools.inspect_native_loc import inspect_bytes

NUMERIC_WIDTHS = {2: 2, 3: 1, 4: 2, 5: 2, 6: 2, 7: 2, 11: 4, 14: 1, 15: 1, 20: 4, 21: 4, 22: 4}
TEXT_TAGS = {1, 12, 13}
MAX_RECORDS = 500_000
NATIVE_UNITS_PER_PIXEL = 256


def _text(data):
    return data.decode('cp1252', errors='replace')


def _short(data, offset, capacity):
    if offset >= len(data) or data[offset] > capacity or offset + capacity + 1 > len(data):
        raise ValueError('Chaîne native bornée invalide.')
    return _text(data[offset + 1:offset + 1 + data[offset]])


def decode_flags(data):
    """Decode the authored property ID/parent/color/label table."""
    flags = {}
    for line in _text(data).splitlines():
        if not line or line.startswith(('#', '@')):
            continue
        parts = line.split('|', 3)
        # Some native rows use the fourth special-color character as separator.
        if len(parts) == 3 and len(parts[2]) >= 7:
            parts = [parts[0], parts[1], parts[2][:6], parts[2][7:]]
        if len(parts) != 4 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError('Ligne de propriété native inconnue.')
        key = int(parts[0])
        if key in flags or not 0 <= key < 96:
            raise ValueError('Identifiant de propriété natif invalide.')
        flags[key] = dict(id=key, parent=int(parts[1]), color=parts[2], name=parts[3].strip())
    return flags


def decode_categories(data):
    if not data or len(data) % 188:
        raise ValueError('Taille de table des catégories inconnue.')
    categories = {}
    for start in range(0, len(data), 188):
        block = data[start:start + 188]
        key = block[21]
        if key in categories:
            raise ValueError('Catégorie native dupliquée.')
        color = struct.unpack_from('<I', block, 24)[0]
        categories[key] = dict(id=key, name=_short(block, 0, 20), icon_index=block[22],
                               native_class=block[23], color=color,
                               color_rgb=f'#{color & 255:02x}{(color >> 8) & 255:02x}{(color >> 16) & 255:02x}',
                               raw_hex=block.hex())
    return categories


def decode_topology(data):
    if len(data) < 8:
        raise ValueError('Table des cartes tronquée.')
    count = struct.unpack_from('<I', data)[0]
    if not 1 <= count <= 100 or 4 + count * 64 + 4 > len(data):
        raise ValueError('Nombre de continents invalide.')
    continents = []
    p = 4
    for _ in range(count):
        block = data[p:p + 64]
        continents.append(dict(name=_short(block, 0, 40), id=struct.unpack_from('<I', block, 44)[0],
                               region_id=struct.unpack_from('<I', block, 48)[0], raw_hex=block.hex()))
        p += 64
    count = struct.unpack_from('<I', data, p)[0]
    p += 4
    if not 1 <= count <= 1000 or p + count * 80 > len(data):
        raise ValueError('Nombre de groupes invalide.')
    groups = []
    for _ in range(count):
        block = data[p:p + 80]
        group_id, continent_id, region_id, default_id, reserved = struct.unpack_from('<HBBII', block)
        groups.append(dict(id=group_id, continent_id=continent_id, region_id=region_id,
                           default_tile_id=default_id, name=_short(block, 12, 40), raw_hex=block.hex()))
        p += 80
    if not (len(data) - p) or (len(data) - p) % 92:
        raise ValueError('Taille de table des tuiles inconnue.')
    tiles = []
    for start in range(p, len(data), 92):
        block = data[start:start + 92]
        tile_id, group_id, continent_id, region_id = struct.unpack_from('<HHBB', block, 48)
        x_raw, y_raw = struct.unpack_from('<ii', block, 56)
        filename = _short(block, 31, 16)
        if not filename or any(char in filename for char in '/\\:'):
            raise ValueError('Nom de carte natif invalide.')
        tiles.append(dict(id=tile_id, group_id=group_id, continent_id=continent_id, region_id=region_id,
                          name=_short(block, 0, 30), filename=filename + '.jpg', basename=filename,
                          x_raw=x_raw, y_raw=y_raw,
                          x=x_raw / NATIVE_UNITS_PER_PIXEL, y=-y_raw / NATIVE_UNITS_PER_PIXEL,
                          width=None, height=None, raw_hex=block.hex()))
    if len({t['id'] for t in tiles}) != len(tiles) or len({g['id'] for g in groups}) != len(groups):
        raise ValueError('Identifiant de carte ou de groupe dupliqué.')
    return dict(continents=continents, groups=groups, tiles=tiles)


def decode_annotations(data, flags=None):
    if not data.startswith(b'WOWCV101'):
        raise ValueError('Version des annotations non reconnue.')
    p, fields, records = 8, {}, []
    flags = flags or {}
    while p < len(data):
        start = p
        tag = data[p]
        p += 1
        if tag == 0xC8 and not fields:
            trailer = data[start:]
            if len(trailer) != 20 or trailer[5] != 0xC9 or trailer[10] != 0xCA or trailer[19] != 0xFF:
                raise ValueError('Fin du flux natif non reconnue.')
            max_id = struct.unpack_from('<I', trailer, 1)[0]
            record_count = struct.unpack_from('<I', trailer, 6)[0]
            if record_count != len(records) or max_id < max((r['id'] for r in records), default=0):
                raise ValueError('Compteur final des annotations incohérent.')
            return records, dict(record_count=record_count, max_id=max_id,
                                 native_timestamp=struct.unpack_from('<d', trailer, 11)[0], raw_hex=trailer.hex())
        if tag == 0xC7:
            if not {1, 2, 3, 4, 5, 11}.issubset(fields):
                raise ValueError('Annotation native incomplète.')
            words = tuple(fields.get(k, 0) for k in (20, 21, 22))
            property_ids = [i for i in range(96) if words[i // 32] & (1 << (i % 32))]
            records.append(dict(id=fields[11], name=fields[1], map_id=fields[2], category=fields[3],
                                x=fields[4], y=fields[5], x_raw=fields[4], y_raw=fields[5],
                                min_level=fields.get(6, 0), max_level=fields.get(7, 0),
                                comment=fields.get(12, '').replace('|', '\n'), links=fields.get(13, ''),
                                flags=words, property_ids=property_ids,
                                property_names=[flags[i]['name'] for i in property_ids if i in flags],
                                raw_fields=fields))
            fields = {}
            if len(records) > MAX_RECORDS:
                raise ValueError('Budget du nombre d’annotations dépassé.')
            continue
        if tag in fields:
            raise ValueError('Champ natif dupliqué.')
        if tag in TEXT_TAGS:
            if p + 2 > len(data):
                raise ValueError('Longueur de chaîne tronquée.')
            size = struct.unpack_from('<H', data, p)[0]
            p += 2
            if p + size > len(data):
                raise ValueError('Chaîne native tronquée.')
            fields[tag] = _text(data[p:p + size])
        elif tag in NUMERIC_WIDTHS:
            size = NUMERIC_WIDTHS[tag]
            if p + size > len(data):
                raise ValueError('Champ numérique tronqué.')
            fields[tag] = int.from_bytes(data[p:p + size], 'little')
        else:
            raise ValueError(f'Champ natif inconnu 0x{tag:02x} à {start}.')
        p += size
    raise ValueError('Fin du flux natif absente.')


def load_database(path, *, expected_version=1200):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Base native absente ou trop volumineuse.')
    data = path.read_bytes()
    inventory = inspect_bytes(data, expected_version=expected_version)
    offsets = struct.unpack_from('<18I', data)[1:11]
    sections = []
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index < 9 else len(data)
        sections.append(zlib.decompress(data[offset:end]))
    flags = decode_flags(sections[1])
    annotations, trailer = decode_annotations(sections[8], flags)
    identifiers = Counter(r['id'] for r in annotations)
    if any(n > 1 for n in identifiers.values()):
        raise ValueError('Identifiant d’annotation natif dupliqué.')
    topology = decode_topology(sections[2])
    return dict(format='wcf-native-database-readonly-v1', source=str(path), source_sha256=inventory['sha256'],
                source_bytes=len(data), annotations=annotations, categories=decode_categories(sections[3]),
                flags=flags, **topology, trailer=trailer,
                icons_bmp=sections[7], region_icons_bmp=sections[6], flag_icons_bmp=sections[9],
                generator_config=_text(sections[4]), generation_table=_text(sections[5]),
                native_version=expected_version,
                validation='Structural decode; not full native behavioral equivalence')
