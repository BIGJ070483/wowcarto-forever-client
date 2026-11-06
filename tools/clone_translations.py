"""Optional, source-bound language overlays for the existing reference database.

Translations share map positions with their verified native source. Missing text
falls back to the source language, so an incomplete pack is never a complete base.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

MAX_PACK_BYTES = 32 * 1024 * 1024
CODE = re.compile(r'[a-z]{2}(?:-[A-Z]{2})?\Z')
FORMAT = 'wcf-translation-v1'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Clé de traduction répétée : ' + key)
        result[key] = value
    return result


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError('Texte de traduction invalide.')
    return value


def load_translation(path, code, source_sha, native):
    """Validate a pack against its native source and return a translated copy."""
    if not CODE.fullmatch(code):
        raise ValueError('Code de langue invalide.')
    path = Path(path)
    if path.stat().st_size > MAX_PACK_BYTES:
        raise ValueError('Fichier de traduction trop volumineux.')
    with path.open('r', encoding='utf-8') as stream:
        pack = json.load(stream, object_pairs_hook=_unique_object)
    if (not isinstance(pack, dict) or pack.get('format') != FORMAT or
            pack.get('language') != code or pack.get('source') != 'US' or
            pack.get('source_sha256') != source_sha):
        raise ValueError('Traduction incompatible avec la base EN installée.')
    if set(pack) - {'format', 'language', 'source', 'source_sha256', 'annotations', 'categories', 'groups'}:
        raise ValueError('Champ de traduction inconnu.')
    rows = {str(row['id']): row for row in native['annotations']}
    categories = {str(k): v for k, v in native['categories'].items()}
    groups = {str(row['id']): row for row in native['groups']}
    translated = dict(native)
    updates = pack.get('annotations', {})
    if not isinstance(updates, dict) or not set(updates) <= rows.keys():
        raise ValueError('Annotation traduite inconnue.')
    for key, values in updates.items():
        if not isinstance(values, dict) or not values or set(values) - {'name', 'comment'}:
            raise ValueError('Champs d’annotation traduite invalides.')
        for field, value in values.items():
            _text(value, 200 if field == 'name' else 65536)
        if 'name' in values and not values['name'].strip():
            raise ValueError('Nom traduit vide.')
    translated['annotations'] = [dict(row, **updates[str(row['id'])]) if str(row['id']) in updates else row
                                 for row in native['annotations']]
    for key, source, target in (('categories', categories, 'categories'), ('groups', groups, 'groups')):
        names = pack.get(key, {})
        if not isinstance(names, dict) or not set(names) <= source.keys():
            raise ValueError('Référence traduite inconnue : ' + key)
        for name in names.values():
            if not _text(name, 200).strip():
                raise ValueError('Libellé traduit vide.')
        if key == 'categories':
            translated[target] = {ident: dict(row, name=names.get(str(ident), row['name']))
                                  for ident, row in native[target].items()}
        else:
            translated[target] = [dict(row, name=names.get(str(row['id']), row['name']))
                                  for row in native[target]]
    return translated, {'translated_annotations': len(updates), 'total_annotations': len(rows)}
