#!/usr/bin/env python3
"""Read-only inspector for the master.loc layout observed in the 1.20 installer.

No annotation conversion, file rewriting, binary execution, or raw data export.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib


def inspect_bytes(data, section_limit=16 * 1024 * 1024, total_limit=64 * 1024 * 1024, *, expected_version=1200):
    if len(data) < 72 or len(data) > 64 * 1024 * 1024:
        raise ValueError('Taille native invalide : de 72 octets a 64 Mio.')
    header = struct.unpack_from('<18I', data)
    if expected_version not in (1062, 1200) or header[0] != expected_version or header[1] != 72:
        raise ValueError('En-tete non reconnu ; ne pas appliquer le format ancien de 2006.')
    offsets = header[1:11]
    if list(offsets) != sorted(set(offsets)) or any(offset < 72 or offset >= len(data) for offset in offsets):
        raise ValueError('Offsets absents, dupliques ou hors fichier.')
    result = {'file_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
              'header_words_uint32_le': list(header), 'header_bytes': 72,
              'layout': f'observed_{expected_version}_header_ten_zlib_streams',
              'annotation_records_decoded': False, 'modified': False, 'sections': []}
    total = 0
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        compressed = data[offset:end]
        decoder = zlib.decompressobj()
        try:
            decoded = decoder.decompress(compressed, section_limit + 1)
        except zlib.error as error:
            raise ValueError(f'Flux zlib {index + 1} invalide.') from error
        if len(decoded) > section_limit:
            raise ValueError('Budget de decompression par section depasse.')
        total += len(decoded)
        if total > total_limit:
            raise ValueError('Budget total de decompression depasse.')
        if not decoder.eof or decoder.unconsumed_tail:
            raise ValueError(f'Flux zlib {index + 1} tronque ou incomplet.')
        expected_padding = b'\0' * 8 if index + 1 < len(offsets) else b''
        if decoder.unused_data != expected_padding:
            raise ValueError(f'Intervalle apres flux {index + 1} non reconnu.')
        kind = 'bitmap' if decoded.startswith(b'BM') else 'wowcv101' if decoded.startswith(b'WOWCV101') else 'uninterpreted'
        section = {'index': index + 1, 'offset': offset,
                   'compressed_bytes': len(compressed) - len(decoder.unused_data),
                   'padding_bytes': len(decoder.unused_data), 'decoded_bytes': len(decoded),
                   'decoded_sha256': hashlib.sha256(decoded).hexdigest(), 'kind': kind}
        if kind == 'bitmap' and len(decoded) >= 54:
            width, height = struct.unpack_from('<ii', decoded, 18)
            section['bitmap_header'] = {'width': width, 'height': height,
                                        'bits_per_pixel': struct.unpack_from('<H', decoded, 28)[0]}
        result['sections'].append(section)
    result['decoded_bytes_total'] = total
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, help='Nouveau JSON de metadonnees, jamais un fichier .loc')
    args = parser.parse_args()
    try:
        if args.source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Fichier trop volumineux.')
        if args.output and (args.output.resolve() == args.source.resolve() or args.output.suffix.lower() != '.json'):
            raise ValueError('La sortie doit etre un nouveau fichier JSON distinct.')
        result = inspect_bytes(args.source.read_bytes())
        result['source_basename'] = args.source.name
        text = json.dumps(result, indent=2, ensure_ascii=False) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(text)
        else:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError) as error:
        print(f'Inspection refusee : {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
