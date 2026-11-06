"""Read bounded Delphi DFM resources without executing or extracting the client.

Only design-time component identifiers, image references and event names are
reported. This does not prove the displayed labels, enabled commands or runtime
image loading. PE input requires the optional research dependency ``pefile``.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

MAX_FILE = 16 * 1024 * 1024
MAX_DFM = 1024 * 1024
MAX_VALUES = 20000
MAX_DEPTH = 64


class DfmReader:
    def __init__(self, data):
        if len(data) > MAX_DFM or not data.startswith(b'TPF0'):
            raise ValueError('Ressource DFM binaire absente ou trop volumineuse')
        self.data, self.pos, self.values = data, 4, 0

    def take(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise ValueError('Ressource DFM tronquée')
        result = self.data[self.pos:self.pos + count]
        self.pos += count
        return result

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def peek(self):
        if self.pos >= len(self.data):
            raise ValueError('Ressource DFM tronquée')
        return self.data[self.pos]

    def short(self):
        return self.take(self.number('<B')).decode('cp1252', errors='replace')

    def budget(self, depth):
        self.values += 1
        if depth > MAX_DEPTH or self.values > MAX_VALUES:
            raise ValueError('Budget de structure DFM dépassé')

    def properties(self, depth):
        result = {}
        while self.peek():
            name = self.short()
            if name in result:
                raise ValueError('Propriété DFM répétée')
            result[name] = self.value(depth + 1)
        self.take(1)
        return result

    def value(self, depth):
        self.budget(depth)
        kind = self.number('<B')
        if kind in (0, 13):
            return None
        if kind == 1:
            items = []
            while self.peek():
                items.append(self.value(depth + 1))
            self.take(1)
            return items
        if kind in (2, 3, 4, 19):
            return self.number({2: '<b', 3: '<h', 4: '<i', 19: '<q'}[kind])
        if kind in (6, 7):
            return self.short()
        if kind in (8, 9):
            return kind == 9
        if kind in (5, 15, 16, 17, 21):
            self.take({5: 10, 15: 4, 16: 8, 17: 8, 21: 8}[kind])
            return {'numeric_type': kind}
        if kind in (10, 12, 18, 20):
            count = self.number('<I')
            payload = self.take(count * 2 if kind == 18 else count)
            if kind == 10:
                return {'binary_bytes': count}
            return payload.decode({12: 'cp1252', 18: 'utf-16le', 20: 'utf-8'}[kind], errors='replace')
        if kind == 11:
            items = []
            while self.peek():
                self.budget(depth + 1)
                items.append(self.short())
            self.take(1)
            return items
        if kind == 14:
            items = []
            while self.peek():
                self.budget(depth + 1)
                if self.number('<B') != 1:
                    raise ValueError('Collection DFM non prise en charge')
                items.append(self.properties(depth + 1))
            self.take(1)
            return items
        raise ValueError('Type DFM non pris en charge')

    def component(self, depth=0):
        self.budget(depth)
        flags = 0
        if self.peek() & 0xf0 == 0xf0:
            flags = self.number('<B') & 0x0f
            if flags & ~7:
                raise ValueError('Préfixe DFM non pris en charge')
            if flags & 2:
                position = self.value(depth + 1)
                if type(position) is not int:
                    raise ValueError('Position DFM invalide')
        result = {'class': self.short(), 'name': self.short(),
                  'properties': self.properties(depth + 1), 'children': []}
        if not result['class'] or not result['name']:
            raise ValueError('Composant DFM sans identité')
        while self.peek():
            result['children'].append(self.component(depth + 1))
        self.take(1)
        return result


def parse_dfm(data):
    reader = DfmReader(data)
    root = reader.component()
    if reader.pos != len(data):
        raise ValueError('Octets supplémentaires après la ressource DFM')
    return root


def summarize_dfm(data):
    root = parse_dfm(data)
    result = {'form_class': root['class'], 'form_name': root['name'],
              'pixels_per_inch': root['properties'].get('PixelsPerInch'),
              'image_lists': [], 'toolbars': [], 'commands': [],
              'runtime_loading_verified': False, 'visual_meaning_verified': False}

    def visit(node, owner=None, images=None, hot=None):
        props = node['properties']
        if node['class'] == 'TImageList':
            result['image_lists'].append({'component': node['name'],
                'explicit_width': props.get('Width'), 'explicit_height': props.get('Height'),
                'drawing_style': props.get('DrawingStyle')})
        if node['class'] == 'TToolBar':
            owner, images, hot = node['name'], props.get('Images'), props.get('HotImages')
            result['toolbars'].append({'component': owner, 'images': images, 'hot_images': hot,
                'explicit_button_width': props.get('ButtonWidth'),
                'explicit_button_height': props.get('ButtonHeight')})
        elif node['class'] in ('TPopupMenu', 'TMainMenu'):
            owner, images, hot = node['name'], props.get('Images'), None
        elif props.get('SubMenuImages'):
            images = props['SubMenuImages']
        if node['class'] in ('TToolButton', 'TMenuItem') and 'ImageIndex' in props:
            result['commands'].append({'component': node['name'], 'class': node['class'],
                'owner': owner, 'images': images, 'hot_images': hot,
                'image_index_zero_based': props['ImageIndex'],
                'event': props.get('OnClick'), 'tag': props.get('Tag'),
                'separator': props.get('Style') == 'tbsSeparator',
                'explicit_visible': props.get('Visible'), 'explicit_enabled': props.get('Enabled')})
        for child in node['children']:
            visit(child, owner, images, hot)

    visit(root)
    return result


def inspect_pe(data):
    if len(data) > MAX_FILE or not data.startswith(b'MZ'):
        raise ValueError('Image PE absente ou trop volumineuse')
    try:
        import pefile
    except ImportError as exc:
        raise ValueError('Dépendance de recherche facultative requise : pip install pefile') from exc
    try:
        pe = pefile.PE(data=data, fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
        candidates = []
        examined = 0
        for resource_type in getattr(pe, 'DIRECTORY_ENTRY_RESOURCE', ()).entries:
            for name in resource_type.directory.entries:
                for language in name.directory.entries:
                    examined += 1
                    if examined > 512:
                        raise ValueError('Budget de ressources PE dépassé')
                    if resource_type.id == 10 and name.name and str(name.name).upper() == 'TMAINFORM':
                        entry = language.data.struct
                        offset = pe.get_offset_from_rva(entry.OffsetToData)
                        size = entry.Size
                        if size > MAX_DFM or offset < 0 or offset + size > len(data):
                            raise ValueError('Ressource DFM hors limites')
                        candidates.append((offset, data[offset:offset + size]))
        if len(candidates) != 1:
            raise ValueError('Ressource TMAINFORM absente ou ambiguë')
        offset, dfm = candidates[0]
        return {'executable_sha256': hashlib.sha256(data).hexdigest(),
                'resource_offset': offset, 'resource_bytes': len(dfm),
                'resource_sha256': hashlib.sha256(dfm).hexdigest(), **summarize_dfm(dfm)}
    except (pefile.PEFormatError, AttributeError, struct.error) as exc:
        raise ValueError('Image PE ou ressources non prises en charge') from exc
    finally:
        if 'pe' in locals():
            pe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--dfm', action='store_true', help='Lire directement une ressource TPF0')
    args = parser.parse_args()
    try:
        with args.source.open('rb') as stream:
            data = stream.read((MAX_DFM if args.dfm else MAX_FILE) + 1)
        result = summarize_dfm(data) if args.dfm else inspect_pe(data)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Inspection impossible : {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
