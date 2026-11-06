"""Reference-bound data model for the independent WoWCartographe reconstruction."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
import unicodedata

from tools.map_packs import load_pack, local_file, sha256
from tools.native_database import load_database
from tools.reference_database import ReferenceIndex
from tools.clone_translations import CODE, load_translation
from tools.clone_regions import point_in_polygon, polygon_bounds


def folded(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


class Model:
    def __init__(self, root, store):
        self.root, self.store = Path(root), store
        self.catalog = json.loads((self.root / 'catalog.json').read_text(encoding='utf-8'))
        if self.catalog.get('format') != 'wcf-clone-catalog-v1':
            raise ValueError('Catalogue de données inconnu.')
        self.sets = self.catalog['sets']
        self.loaded = {}
        self.community = []
        self.reference_corrections = {}
        self.index = None
        self.index_error = None
        self.translation_errors = {}
        self.translation_coverage = {}
        self.translation_paths = {}

    def available_languages(self):
        """List only source-bound, successfully validated optional language packs."""
        directory = self.root / 'translations'
        if not directory.is_dir():
            return ['Francais', 'US']
        files = [p for p in sorted(directory.glob('*.json')) if CODE.fullmatch(p.stem)]
        if not files:
            return ['Francais', 'US']
        reference = next(s for s in self.sets if s['id'] == 'reference120')
        spec = reference['databases']['US']
        path = local_file(self.root, spec['path'])
        if sha256(path) != spec['sha256']:
            raise ValueError('La base EN diffère de son empreinte enregistrée.')
        if ('reference120', 'US') not in self.loaded:
            self.loaded[('reference120', 'US')] = load_database(path)
        native = self.loaded[('reference120', 'US')]
        valid = ['Francais', 'US']
        for file in files:
            try:
                checked = local_file(self.root, 'translations/' + file.name)
                _, coverage = load_translation(checked, file.stem, spec['sha256'], native)
                self.translation_paths[file.stem] = checked
                self.translation_coverage[file.stem] = coverage
                valid.append(file.stem)
            except (OSError, ValueError) as error:
                self.translation_errors[file.stem] = str(error)
        return valid

    def open(self, map_set, database):
        self.map_set = next(s for s in self.sets if s['id'] == map_set)
        self.database_name = database
        key = map_set, database
        if key not in self.loaded:
            if map_set == 'reference120' and database in self.translation_paths:
                # Keep at most one translated copy in memory. Original FR/EN
                # remain cached and translation text is always source-bound.
                for old in list(self.loaded):
                    if old[0] == 'reference120' and old[1] not in ('Francais', 'US'):
                        del self.loaded[old]
                source = self.map_set['databases']['US']
                translation = local_file(self.root, 'translations/' + self.translation_paths[database].name)
                translated, coverage = load_translation(translation, database,
                                                         source['sha256'], self.loaded[('reference120', 'US')])
                self.loaded[key] = translated
                self.translation_coverage[database] = coverage
            else:
                record = self.map_set['databases'][database]
                path = local_file(self.root, record['path'])
                if sha256(path) != record['sha256']:
                    raise ValueError('La base source diffère de son empreinte enregistrée.')
                self.loaded[key] = load_database(path, expected_version=self.map_set.get('native_version', 1200))
        self.db = self.loaded[key]
        if self.index:
            self.index.close()
            self.index = None
        self.index_error = None
        index_path = self.root / 'databases' / 'reference-120.sqlite3'
        if index_path.is_file() and map_set == 'reference120' and database in ('Francais', 'US'):
            try:
                self.index = ReferenceIndex(index_path, database, self.map_set['databases'][database]['sha256'])
            except (OSError, ValueError, sqlite3.Error) as error:
                self.index_error = str(error)
        assets = {}
        for name in self.map_set['packs']:
            manifest = local_file(self.root, name)
            pack = load_pack(manifest)
            for row in pack['maps']:
                filename = row['id'].casefold()
                if filename in assets:
                    raise ValueError('Deux images portent le même nom dans ce jeu de cartes.')
                assets[filename] = dict(root=manifest.parent, standard=row['standard'], high=row.get('high'))
        self.tiles = {}
        for native in self.db['tiles']:
            tile = dict(native, width=256., height=256.)
            tile.update(assets.get(tile['filename'].casefold(), dict(root=self.root, standard=None, high=None)))
            self.tiles[tile['id']] = tile
        self.groups = {r['id']: r for r in self.db['groups']}
        self.categories = self.db['categories']
        self.native_prefix = f'native:{map_set}:{database}:'
        self.personal_prefix = f'personal:{map_set}:{database}:'
        self.rebuild()

    def projected(self, row, *, key, source, note_id=None):
        tile = self.tiles.get(row['map_id'])
        if not tile:
            return None
        result = dict(row, key=key, source=source, note_id=note_id, group_id=tile['group_id'],
                      wx=tile['x'] + row['x'] / 256, wy=tile['y'] + row['y'] / 256)
        result['region'] = self.groups[tile['group_id']]['name']
        if isinstance(result.get('tags'), str):
            result['property_names'] = [r.strip() for r in result['tags'].split(';') if r.strip()]
        result['search_name'] = folded(result['name'])
        return result

    def rebuild(self):
        notes = self.store.notes(include_deleted=True)
        hidden = {r['source_ref'] for r in notes if r['source_ref']}
        community_refs = ({row['source_ref'] for row in self.community if row.get('source_ref')}
                          if self.map_set.get('community_coordinate_system') == 'wowcartographe-120'
                          and self.database_name == 'Francais' else set())
        result = []
        for row in self.db['annotations']:
            key = self.native_prefix + str(row['id'])
            if key not in hidden:
                record = self.projected(self.reference_row(row), key=key, source='native')
                if record:
                    result.append(record)
        for row in notes:
            ref = row['source_ref']
            relevant = ref and (ref.startswith(self.native_prefix) or ref.startswith(self.personal_prefix) or ref in community_refs)
            if not row['deleted'] and relevant:
                record = self.projected(row, key='note:' + row['id'], source='personal', note_id=row['id'])
                if record:
                    result.append(record)
        # Community rows retain their source coordinate system. The current
        # endpoint transports native 1.20 records, so only that reference accepts it.
        if self.map_set.get('community_coordinate_system') == 'wowcartographe-120' and self.database_name == 'Francais':
            for row in self.community:
                if row.get('source_ref') not in hidden:
                    record = self.projected(row, key=row['source_ref'], source='community')
                    if record:
                        result.append(record)
        self.annotations = result
        self.by_key = {r['key']: r for r in result}
        self.by_group = {}
        for row in result:
            self.by_group.setdefault(row['group_id'], []).append(row)
        self.category_counts = Counter(r['category'] for r in result)

    def reference_row(self, row):
        from tools.clone_reference_community import SOURCES
        locale = getattr(self, 'database_name', None)
        if getattr(self, 'map_set', {}).get('id') != 'reference120' or self.db.get('source_sha256') != SOURCES.get(locale):
            return row
        correction = self.reference_corrections.get(locale, {}).get(row['id'])
        return dict(row, name=correction['name'], reference_revision=correction['revision']) if correction else row

    def geographic_regions(self):
        """Return the reference database's geographic labels, excluding user notes and instances."""
        # The 1.20 databases use the same native category ID but localize its
        # label ("Région" in FR, "Zone" in US/EN).
        categories = {ident for ident, category in self.categories.items()
                      if ident == 18 or folded(category['name']) in ('region', 'zone')}
        result = []
        for row in self.db['annotations']:
            if row['category'] not in categories:
                continue
            projected = self.projected(self.reference_row(row), key=self.native_prefix + str(row['id']), source='native')
            if not projected:
                continue
            group = self.groups.get(projected['group_id'], {})
            if projected['name'].startswith('[') or group.get('name', '').startswith('['):
                continue
            result.append(projected)
        return sorted(result, key=lambda row: folded(row['name']))

    def search(self, query='', *, name=True, comment=False, categories=None, group=None, minimum=None, maximum=None, properties=None, continents=None, regions=None, local_only=False):
        terms = folded(query).split()
        region_bounds = [(ident, polygon_bounds(points), points) for ident, points in regions] if regions else []
        candidates = None
        if getattr(self, 'index', None) and terms:
            try:
                candidates = self.index.candidates(terms)
            except sqlite3.Error:
                # A damaged or unavailable optional index must never hide records.
                candidates = None
        result = []
        for row in self.annotations:
            if candidates is not None and row['source'] == 'native' and not row.get('reference_revision') and row['id'] not in candidates:
                continue
            if categories is not None and row['category'] not in categories:
                continue
            if group is not None and row['group_id'] != group:
                continue
            if continents is not None and self.groups.get(row['group_id'], {}).get('continent_id') not in continents:
                continue
            if local_only and row.get('source') != 'personal':
                continue
            if region_bounds:
                x, y = row.get('wx'), row.get('wy')
                if x is None or y is None or not any(row['group_id'] == ident and bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] and point_in_polygon(x,y,points)
                                                     for ident,bounds,points in region_bounds):
                    continue
            if minimum is not None and row.get('max_level', 0) < minimum:
                continue
            if maximum is not None and row.get('min_level', 0) > maximum:
                continue
            tags = row.get('property_names', [])
            if properties and not all(prop in tags for prop in properties):
                continue
            haystack = (row['search_name'] if name else '') + (' ' + folded(row.get('comment', '')) if comment else '')
            if all(term in haystack for term in terms):
                result.append(row)
        return result
