#!/usr/bin/env python3
"""Delivery A: read-only source inspection; generated evidence is local only."""
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import statistics
import sys
from datetime import datetime, timezone

from PIL import Image, ImageChops, ImageDraw, ImageStat

from review_page import write_review_page

TOOL_VERSION = "0.1.4"
FAMILY = re.compile(r"^kalimdor\d+\.jpg$", re.I)
PRODUCT = "wow_classic_era"
SAMPLE = 64
DIRECTIONS = {"right": (1, 0), "down": (0, 1), "left": (-1, 0), "up": (0, -1)}
OPPOSITE = {"right": "left", "down": "up", "left": "right", "up": "down"}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def file_record(path):
    path = Path(path)
    result = {"path": str(path), "exists": path.is_file()}
    if result["exists"]:
        try:
            result.update(bytes=path.stat().st_size, sha256=digest(path))
        except OSError as exc:
            result["error"] = str(exc)
    return result


def image_record(path):
    result = file_record(path)
    try:
        with Image.open(path) as image:
            result.update(width=image.width, height=image.height, format=image.format)
            if image.width * image.height > 16_777_216:
                raise ValueError("Image superieure a 16 millions de pixels : inspection manuelle requise.")
            image.verify()
        # verify() is not a substitute for decoding all JPEG data.
        with Image.open(path) as image:
            image.load()
        if result["format"] != "JPEG":
            result["error"] = "Extension JPG mais contenu non JPEG."
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        result["error"] = str(exc)
    return result


def read_metadata(path):
    """Blizzard pipe-separated metadata, with typed headers (Product!STRING:0)."""
    if Path(path).stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Metadonnees superieures a 2 Mio : lecture refusee.")
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream, delimiter="|")
        header = [column.split("!", 1)[0].strip().casefold() for column in next(rows)]
        if not header or any(not column for column in header) or len(set(header)) != len(header):
            raise ValueError("Colonnes de metadonnees dupliquees.")
        result = []
        for line in rows:
            if not any(cell.strip() for cell in line):
                continue
            if len(line) != len(header):
                raise ValueError("Nombre de colonnes incoherent dans les metadonnees.")
            result.append(dict(zip(header, (cell.strip() for cell in line))))
        return result


def classic_build(root):
    root = Path(root)
    product_dir = root / "_classic_era_"
    build_info = root / ".build.info"
    result = {
        "root": str(root), "product_directory": str(product_dir),
        "product_directory_exists": product_dir.is_dir(),
        "data_directory": str(root / "Data" / "data"),
        "data_directory_exists": (root / "Data" / "data").is_dir(),
        "build_info": file_record(build_info), "status": "unresolved", "candidates": [],
    }
    flavor = product_dir / ".flavor.info"
    try:
        if flavor.is_file():
            flavors = read_metadata(flavor)
            result["flavor_columns"] = sorted(set().union(*(row.keys() for row in flavors)))
            products = {row[key].casefold() for row in flavors
                        for key in ("product", "product flavor") if row.get(key)}
            result["flavor_products"] = sorted(products)
            if products and products != {PRODUCT}:
                result["reason"] = "Produit .flavor.info ambigu ou different de Classic Era."
                return result
            result["flavor_status"] = "matching_product" if products else "unrecognized_schema_using_build_info_product_only"
        if not build_info.is_file():
            result["reason"] = ".build.info absent : ne pas choisir un autre client par defaut."
            return result
        rows = read_metadata(build_info)
        candidates = [row for row in rows if row.get("product", "").casefold() == PRODUCT]
        # Product matching is mandatory. The sole active Retail row is not a fallback.
        result["candidates"] = [
            {key: row.get(key) for key in ("product", "version", "active", "branch", "build key", "cdn key")}
            for row in candidates
        ]
        active = [row for row in candidates if row.get("active") == "1"]
        if len(active) != 1:
            result["reason"] = "Classic Era : aucune ligne active unique dans .build.info."
        elif not active[0].get("version"):
            result["reason"] = "Numero de version absent."
        elif not product_dir.is_dir() or not result["data_directory_exists"]:
            result["reason"] = "Metadonnees trouvees mais repertoire produit ou stockage CASC absent."
        else:
            result.update(status="identified_from_metadata", selected=result["candidates"][candidates.index(active[0])])
            result["reason"] = "Identification des metadonnees seulement ; lecture CASC non testee."
    except (OSError, UnicodeError, ValueError, StopIteration) as exc:
        result["reason"] = f"Metadonnees illisibles : {exc}"
    return result


def virtual_maps(carto_root, local_appdata):
    # PureWindowsPath also makes this logic testable from Linux.
    carto = PureWindowsPath(str(carto_root))
    if not carto.is_absolute() or not carto.drive or str(carto.drive).startswith("\\\\"):
        raise ValueError("Le repertoire WoWCarto doit etre un chemin Windows absolu sur un lecteur local.")
    return Path(local_appdata) / "VirtualStore" / Path(*carto.parts[1:]) / "maps"


def documents_folder():
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PERSONAL honours the redirected Windows Documents folder.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value)
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"


def find_maps(carto_root, explicit=None, prefer_virtualstore=False):
    candidates = []
    virtual_candidate = None
    if os.environ.get("LOCALAPPDATA"):
        try:
            virtual_candidate = virtual_maps(carto_root, os.environ["LOCALAPPDATA"])
            candidates.append(virtual_candidate)
        except ValueError:
            pass
    candidates.append(Path(carto_root) / "maps")
    observed = []
    for candidate in candidates:
        row = {"path": str(candidate), "exists": candidate.is_dir()}
        if row["exists"]:
            try:
                row["jpg_count"] = len(jpg_files(candidate))
            except OSError as exc:
                row["error"] = str(exc)
        observed.append(row)
    if explicit:
        return Path(explicit), observed, "explicit_override"
    if prefer_virtualstore:
        # Mission-specific reference: VirtualStore was already demonstrated by
        # the user's substitution test. This selects an analysis source only.
        if virtual_candidate is None:
            return None, observed, "requested_virtualstore_unresolvable"
        return virtual_candidate, observed, (
            "requested_virtualstore_reference_not_runtime_proof" if virtual_candidate.is_dir()
            else "requested_virtualstore_missing_no_physical_fallback")
    populated = [row for row in observed if row.get("jpg_count", 0) > 0]
    if len(populated) == 1:
        return Path(populated[0]["path"]), observed, "single_populated_candidate_not_runtime_proof"
    # A partially virtualized tree can read files from both locations. Never silently
    # prefer one location when both contain maps, including unrelated map families.
    return None, observed, "ambiguous_or_missing_use_maps_option"


def jpg_files(folder):
    result = {}
    if not Path(folder).is_dir():
        return result
    for path in sorted(Path(folder).iterdir()):
        if path.is_file() and path.suffix.casefold() == ".jpg":
            key = path.name.casefold()
            if key in result:
                raise ValueError(f"Noms JPG identiques sans tenir compte de la casse : {key}")
            result[key] = path
    return result


def thumbnail(path, size=(SAMPLE, SAMPLE)):
    with Image.open(path) as image:
        return image.convert("RGB").resize(size, Image.Resampling.LANCZOS)


def inventory(maps):
    files = {"standard": jpg_files(maps), "high": jpg_files(Path(maps) / "high")}
    names = {resolution: set(items) for resolution, items in files.items()}
    result = {
        "counts": {resolution: len(items) for resolution, items in files.items()},
        "all_names_identical": names["standard"] == names["high"],
        "only_standard": sorted(names["standard"] - names["high"]),
        "only_high": sorted(names["high"] - names["standard"]),
        "kalimdor": {}, "issues": [],
    }
    family = sorted(name for name in names["standard"] | names["high"] if FAMILY.fullmatch(name))
    for name in family:
        pair = {}
        for resolution, items in files.items():
            if name in items:
                pair[resolution] = image_record(items[name])
            else:
                result["issues"].append(f"{name} : {resolution} absent")
        if all(resolution in pair and "error" not in pair[resolution] for resolution in files):
            std, hd = pair["standard"], pair["high"]
            pair["scale_x"] = hd["width"] / std["width"]
            pair["scale_y"] = hd["height"] / std["height"]
            pair["same_aspect_ratio"] = hd["width"] * std["height"] == hd["height"] * std["width"]
            difference = ImageChops.difference(thumbnail(std["path"]), thumbnail(hd["path"]))
            pair["preview_rgb_mean_absolute_error_0_255"] = round(statistics.mean(ImageStat.Stat(difference).mean), 4)
            if not pair["same_aspect_ratio"] or pair["scale_x"] <= 1 or pair["scale_y"] <= 1:
                result["issues"].append(f"{name} : dimensions standard/HD incoherentes")
        for resolution in files:
            if pair.get(resolution, {}).get("error"):
                result["issues"].append(f"{name} : {resolution} : {pair[resolution]['error']}")
        result["kalimdor"][name] = pair
    result["kalimdor_counts"] = {
        resolution: sum(resolution in pair for pair in result["kalimdor"].values())
        for resolution in files
    }
    if result["kalimdor_counts"] != {"standard": 53, "high": 53}:
        result["issues"].append("Reference attendue : 53 tuiles Kalimdor par resolution ; ecart observe.")
    if not result["all_names_identical"]:
        result["issues"].append("Les noms standard et HD ne sont pas identiques.")
    return result


def contact_sheet(records, resolution, output):
    items = [(name, pair[resolution]) for name, pair in records.items()
             if resolution in pair and "error" not in pair[resolution]]
    if not items:
        return
    cell_w, cell_h, columns = 192, 218, 8
    sheet = Image.new("RGB", (columns * cell_w, math.ceil(len(items) / columns) * cell_h), "#252525")
    draw = ImageDraw.Draw(sheet)
    for index, (name, record) in enumerate(items):
        x, y = (index % columns) * cell_w, (index // columns) * cell_h
        with Image.open(record["path"]) as image:
            preview = image.convert("RGB")
            preview.thumbnail((184, 184))
            sheet.paste(preview, (x + 4, y + 4))
        draw.text((x + 4, y + 190), f"{name}\n{record['width']} x {record['height']}", fill="white")
    sheet.save(output)


def edge_vectors(path):
    image = thumbnail(path)
    return {
        "left": [image.getpixel((0, y)) for y in range(SAMPLE)],
        "right": [image.getpixel((SAMPLE - 1, y)) for y in range(SAMPLE)],
        "up": [image.getpixel((x, 0)) for x in range(SAMPLE)],
        "down": [image.getpixel((x, SAMPLE - 1)) for x in range(SAMPLE)],
    }


def edge_texture(vector):
    # Spatial variation per channel, not variation between the RGB channels of
    # a constant ocean-coloured edge. Flat edges cannot constrain adjacency.
    return statistics.mean(statistics.pstdev(pixel[channel] for pixel in vector) for channel in range(3))


def seam_candidates(records):
    edges = {name: edge_vectors(pair["standard"]["path"])
             for name, pair in records.items()
             if "standard" in pair and "error" not in pair["standard"]}
    textures = {(name, side): edge_texture(vector)
                for name, vectors in edges.items() for side, vector in vectors.items()}
    rankings = {}
    for source, vectors in edges.items():
        for direction, first in vectors.items():
            ranked = []
            for target, other in edges.items():
                if target == source:
                    continue
                second = other[OPPOSITE[direction]]
                error = statistics.mean(abs(a - b) for left, right in zip(first, second) for a, b in zip(left, right))
                ranked.append({"target": target, "mae": round(error, 4),
                               "min_texture": round(min(textures[source, direction], textures[target, OPPOSITE[direction]]), 4)})
            rankings[source, direction] = sorted(ranked, key=lambda row: (row["mae"], row["target"]))
    candidates, links = [], []
    for (source, direction), ranked in rankings.items():
        for index, entry in enumerate(ranked[:3]):
            reverse = rankings[entry["target"], OPPOSITE[direction]]
            gap = ranked[1]["mae"] - ranked[0]["mae"] if len(ranked) > 1 else 0
            reverse_gap = reverse[1]["mae"] - reverse[0]["mae"] if len(reverse) > 1 else 0
            mutual = bool(reverse and reverse[0]["target"] == source and index == 0)
            usable = mutual and entry["mae"] <= 20 and entry["min_texture"] >= 5 and gap >= 1 and reverse_gap >= 1
            row = {"source": source, "direction": direction, "rank": index + 1, **entry,
                   "best_second_gap": round(gap, 4), "mutual_best": mutual,
                   "provisional_link": usable, "status": "hypothesis_not_geographic_proof"}
            candidates.append(row)
            if usable and direction in ("right", "down"):
                links.append(row)
    return candidates, links


def provisional_layouts(records, links):
    """Place only heuristic links. Inconsistent cycles/collisions remain explicit."""
    adjacency = {name: [] for name, pair in records.items()
                 if "standard" in pair and "error" not in pair["standard"]}
    for link in links:
        dx, dy = DIRECTIONS[link["direction"]]
        adjacency[link["source"]].append((link["target"], dx, dy))
        adjacency[link["target"]].append((link["source"], -dx, -dy))
    remaining, components = set(adjacency), []
    while remaining:
        root = min(remaining)
        positions, queue, conflicts = {root: (0, 0)}, [root], []
        while queue:
            current = queue.pop(0)
            for target, dx, dy in adjacency[current]:
                proposed = (positions[current][0] + dx, positions[current][1] + dy)
                if target in positions:
                    if positions[target] != proposed:
                        conflicts.append(f"cycle incoherent : {current} -> {target}")
                else:
                    positions[target] = proposed
                    queue.append(target)
        if len(set(positions.values())) != len(positions):
            conflicts.append("Plusieurs tuiles occupent la meme case.")
        remaining -= positions.keys()
        min_x, min_y = min(x for x, y in positions.values()), min(y for x, y in positions.values())
        components.append({"status": "hypothesis_not_verified", "conflicts": sorted(set(conflicts)),
                           "tiles": [{"file": name, "x": x - min_x, "y": y - min_y}
                                     for name, (x, y) in sorted(positions.items())]})
    return components


def consistent_hypotheses(records, links):
    """Greedy geometric consistency, NOT proof of cartographic correctness.

    Short, independently closing squares take precedence over edge colour alone.
    Every discarded constraint is retained with its conflicting displacement.
    """
    nodes = {name for name, pair in records.items() if "standard" in pair and "error" not in pair["standard"]}
    adjacency = {node: [] for node in nodes}
    for index, link in enumerate(links):
        dx, dy = DIRECTIONS[link["direction"]]
        adjacency[link["source"]].append((link["target"], dx, dy, index))
        adjacency[link["target"]].append((link["source"], -dx, -dy, index))

    def square_support(index, link):
        wanted = DIRECTIONS[link["direction"]]

        def visit(current, x, y, remaining, seen):
            if remaining == 0:
                return int(current == link["target"] and (x, y) == wanted)
            return sum(visit(target, x + dx, y + dy, remaining - 1, seen | {target})
                       for target, dx, dy, edge in adjacency[current]
                       if edge != index and target not in seen)

        return visit(link["source"], 0, 0, 3, {link["source"]})

    ranked = []
    for index, link in enumerate(links):
        item = {**link, "independent_square_support": square_support(index, link)}
        item["ambiguity_margin_ratio"] = round(link.get("best_second_gap", 0) / (1 + link.get("mae", 0)), 6)
        ranked.append(item)
    ranked.sort(key=lambda link: (-link["independent_square_support"], -link["ambiguity_margin_ratio"],
                                  link.get("mae", 0), link["source"], link["direction"], link["target"]))
    groups = {name: {name: (0, 0)} for name in nodes}
    accepted, rejected = [], []
    for link in ranked:
        source, target = link["source"], link["target"]
        left, right = groups[source], groups[target]
        x, y = left[source]
        tx, ty = right[target]
        dx, dy = DIRECTIONS[link["direction"]]
        if left is right:
            actual = tx - x, ty - y
            if actual != (dx, dy):
                rejected.append({**link, "reason": "inconsistent_cycle", "requested_delta": [dx, dy],
                                 "delta_in_retained_graph": list(actual)})
                continue
        else:
            offset_x, offset_y = x + dx - tx, y + dy - ty
            moved = {name: (px + offset_x, py + offset_y) for name, (px, py) in right.items()}
            if set(left.values()) & set(moved.values()):
                rejected.append({**link, "reason": "tile_collision_between_components"})
                continue
            left.update(moved)
            for name in right:
                groups[name] = left
        accepted.append(link)
    components = provisional_layouts(records, accepted)
    return {
        "status": "consistent_hypothesis_not_geographic_proof",
        "policy": "Priorite aux cycles carres coherents, puis marge/(1+MAE). Glouton, pas un optimum global. Aucun ordre numerique utilise.",
        "accepted_links": accepted, "rejected_links": rejected, "components": components,
    }


def assemble(tiles, records, resolution, destination, label, tile_labels=False):
    """Native pixel dimensions only. No cartographic rescaling or guessed fill."""
    if not tiles:
        raise ValueError("Assemblage vide.")
    sizes = {(records[tile["file"]][resolution]["width"], records[tile["file"]][resolution]["height"]) for tile in tiles}
    if len(sizes) != 1:
        raise ValueError("Dimensions heterogenes : une grille reguliere n est pas etablie.")
    width, height = next(iter(sizes))
    min_x, min_y = min(tile["x"] for tile in tiles), min(tile["y"] for tile in tiles)
    columns = max(tile["x"] for tile in tiles) - min_x + 1
    rows = max(tile["y"] for tile in tiles) - min_y + 1
    if width * columns * height * rows > 80_000_000:
        raise ValueError("Assemblage superieur a 80 millions de pixels ; reduire la composante.")
    canvas = Image.new("RGB", (columns * width, rows * height + 32), "#ff00ff")
    draw = ImageDraw.Draw(canvas)
    for tile in tiles:
        record = records[tile["file"]][resolution]
        if "error" in record:
            raise ValueError(f"Image illisible : {tile['file']}")
        with Image.open(record["path"]) as image:
            canvas.paste(image.convert("RGB"), ((tile["x"] - min_x) * width, (tile["y"] - min_y) * height))
        if tile_labels:
            x, y = (tile["x"] - min_x) * width, (tile["y"] - min_y) * height
            draw.rectangle((x, y, x + width - 1, y + height - 1), outline="yellow")
            draw.rectangle((x, y, x + min(130, width - 1), y + 15), fill="black")
            draw.text((x + 3, y + 2), tile["file"], fill="yellow")
    draw.rectangle((0, rows * height, columns * width, rows * height + 32), fill="black")
    draw.text((4, rows * height + 8), label, fill="white")
    canvas.save(destination)


def validate_layout(layout, records):
    """User-supplied observations, not automatic proof. Pin all placements to hashes."""
    if not isinstance(layout, dict) or type(layout.get("schema_version")) is not int or layout.get("schema_version") != 1 or not isinstance(layout.get("tiles"), list) or not layout["tiles"]:
        raise ValueError("Disposition : schema_version=1 et liste tiles non vide requis.")
    used_names, used_positions = set(), set()
    for tile in layout["tiles"]:
        if not isinstance(tile, dict) or not isinstance(tile.get("file"), str):
            raise ValueError("Chaque tuile doit etre un objet avec un nom de fichier.")
        name = tile.get("file")
        if name not in records or name in used_names:
            raise ValueError(f"Tuile inconnue ou dupliquee : {name}")
        if any(type(tile.get(key)) is not int or abs(tile[key]) > 100 for key in ("x", "y")):
            raise ValueError("Positions de grille entieres requises, entre -100 et 100.")
        position = tile["x"], tile["y"]
        if position in used_positions:
            raise ValueError("Chevauchement dans la disposition.")
        if not isinstance(tile.get("evidence"), str) or not tile["evidence"].strip():
            raise ValueError(f"Observation de positionnement requise pour {name}.")
        if not isinstance(tile.get("sha256"), dict):
            raise ValueError(f"Empreintes des deux resolutions requises pour {name}.")
        for resolution in ("standard", "high"):
            record = records[name].get(resolution, {})
            if "error" in record or not record.get("sha256") or tile["sha256"].get(resolution) != record["sha256"]:
                raise ValueError(f"Empreinte absente ou differente : {name} / {resolution}")
        used_names.add(name)
        used_positions.add(position)
    landmarks = layout.get("landmarks", [])
    if not isinstance(landmarks, list):
        raise ValueError("landmarks doit etre une liste.")
    for mark in landmarks:
        if (not isinstance(mark, dict) or not isinstance(mark.get("file"), str)
                or mark["file"] not in used_names
                or any(not isinstance(mark.get(key), str) or not mark[key].strip() for key in ("name", "evidence"))):
            raise ValueError("Repere : tuile positionnee, nom et preuve requis.")
        if "standard_pixel" in mark:
            point, record = mark["standard_pixel"], records[mark["file"]]["standard"]
            if (not isinstance(point, dict) or any(type(point.get(key)) is not int for key in ("x", "y"))
                    or not 0 <= point["x"] < record["width"] or not 0 <= point["y"] < record["height"]):
                raise ValueError("Repere : pixel standard hors de la tuile.")
    seams = layout.get("seam_observations", [])
    if not isinstance(seams, list):
        raise ValueError("seam_observations doit etre une liste.")
    for seam in seams:
        if (not isinstance(seam, dict) or any(not isinstance(seam.get(key), str) for key in ("source", "target", "direction", "result"))
                or seam.get("source") not in used_names or seam.get("target") not in used_names
                or seam["source"] == seam["target"] or seam.get("direction") not in DIRECTIONS
                or seam.get("result") not in ("matches", "contradicts", "uncertain")
                or not isinstance(seam.get("evidence"), str) or not seam["evidence"].strip()):
            raise ValueError("Raccord observe : deux tuiles positionnees, direction, resultat et preuve requis.")
        first, second = (next(tile for tile in layout["tiles"] if tile["file"] == seam[key]) for key in ("source", "target"))
        delta = second["x"] - first["x"], second["y"] - first["y"]
        if seam["result"] == "matches" and delta != DIRECTIONS[seam["direction"]]:
            raise ValueError("Raccord declare concordant mais incompatible avec les positions fournies.")
    return {"status": "user_observations_not_independently_verified", "tiles": len(used_names),
            "covers_all_inventoried_kalimdor": used_names == set(records), "landmarks": landmarks,
            "seam_observations": seams,
            "geography_verified": False}


def test_image_state(maps, originals):
    result = {"status": "not_established", "note": "Aucun OCR : une difference de hash ne prouve pas la presence du bandeau TEST 00.", "files": []}
    for relative in (Path("azeroth00.jpg"), Path("high") / "azeroth00.jpg"):
        current = file_record(Path(maps) / relative) if maps else {
            "exists": None, "inspection_status": "not_inspected_no_maps_selected"}
        original = file_record(Path(originals) / relative)
        row = {"file": str(relative), "current": current, "original": original}
        if current.get("sha256") and original.get("sha256"):
            row["same_bytes"] = current["sha256"] == original["sha256"]
        result["files"].append(row)
    if all(row.get("same_bytes") is True for row in result["files"]):
        result["status"] = "matches_existing_original_backup_not_authenticated"
    elif any(row.get("same_bytes") is False for row in result["files"]):
        result["status"] = "different_from_existing_original_backup_inspect_visually"
    return result


def safe_output(output, protected):
    destination = Path(output).resolve()
    for directory in protected:
        directory = Path(directory).resolve()
        if destination == directory or directory in destination.parents:
            raise ValueError(f"Sortie interdite dans une source ou sauvegarde : {directory}")
    if any((parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
           for parent in (destination, *destination.parents)):
        raise ValueError("Les sorties contenant des cartes doivent rester hors de tout depot Git.")
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def write_summary(report, output):
    inv = report.get("inventory", {})
    review = report.get("layout_review")
    review_notes = []
    if review:
        review_notes = [
            f"- Graphe brut : {report['hypotheses']['links']} raccords heuristiques.",
            f"- Alternative coherente : {len(review['accepted_links'])} raccords conserves, {len(review['rejected_links'])} contraintes ecartees (journal conserve).",
            "- Ouvrir consistent-hypotheses/component-00-labelled.png si produit : noms des tuiles visibles, positions NON VALIDEES.",
        ]
    lines = ["# Diagnostic WoWCarto — livraison A", "", f"Date UTC : {report['generated_utc']}",
             "", "## Observations", "", f"- Source choisie : {report['maps']['selected'] or 'aucune (inventaire non execute)'}",
             f"- Source demandee : {report['maps']['requested_source']} ; chemin explicite : {report['maps']['explicit_path'] or 'non fourni'}.",
             f"- Selection : {report['maps']['selection_status']} (pas une preuve du chargement dans WoWCarto).",
             *[f"- Emplacement detecte : {candidate['path']} ; existe : {candidate['exists']} ; JPG : {candidate.get('jpg_count', 'non compte')}."
               for candidate in report['maps']['candidates']],
             f"- Build Classic Era : {report['classic']['status']}.",
             f"- Detail build : {report['classic'].get('selected', report['classic'].get('reason'))}.",
             f"- JPG par resolution : {inv.get('counts', 'inventaire non execute')}.",
             f"- Kalimdor par resolution : {inv.get('kalimdor_counts', 'inventaire non execute')}.",
             f"- Noms standard/HD identiques : {inv.get('all_names_identical', 'inconnu')}.",
             f"- Ancien test azeroth00 : {report['previous_test']['status']}.",
             "- validation.html : formulaire local sans reseau, sans position pre-validee ; telecharger les observations avant fermeture.",
             *review_notes,
             "- Les sauvegardes existantes sont seulement inventoriees ; leur authenticite n est pas presumée.",
             "", "## Hypotheses et limites", "",
             "- contact-*.png : ordre alphabetique de presentation, PAS une disposition geographique.",
             "- hypotheses/ : composantes provisoires issues des raccords de bord ; aucune n est une carte validee.",
             "- consistent-hypotheses/ : propositions rendues geometriquement coherentes ; les contraintes ecartees sont conservees dans le JSON.",
             "- Meme une grille sans contradiction ne prouve pas les positions reelles ; comparer les raccords et reperes dans WoWCarto.",
             "- Le score est une erreur RGB sur apercus 64 x 64 ; il ne prouve ni la projection ni la position.",
             "- Bords uniformes, ambiguïtés et cycles contradictoires sont exclus de l assemblage automatique.",
             "- Magenta dans un assemblage : case absente, jamais une tuile generee ou corrigee.",
             "- Aucun acces CASC, aucune substitution, aucune restauration, aucun lancement de WoWCarto.",
             "- Les bases et les annotations ne sont pas interpretees et leur compatibilite n est pas etablie.",
             "- L absence du bandeau TEST 00 exige une inspection visuelle ou une reference authentifiee.",
             "", "## Problemes et controles restants", ""]
    lines += [f"- {issue}" for issue in report["issues"]] or ["- Aucun probleme d inventaire detecte."]
    lines += ["", "## A transmettre", "",
              "Transmettre report.json et report.md apres verification des chemins personnels.",
              "Conserver les images assemblees, planches et fichiers originaux sur le PC.",
              "Ouvrir validation.html pour relever les positions, pixels des reperes et raccords. Telecharger layout-observed.json.",
              "Glisser ce JSON sur valider-disposition.cmd pour recontroler les hashes et produire les assemblages natifs d un sous-ensemble.",
              "Indiquer les tuiles ou la Croisee et la Grande fracture sont visibles, puis les confirmer dans WoWCarto.",
              "Recueillir trois reperes communs et les raccords observes ; ne pas les inventer depuis les numeros de fichiers.",
              "La livraison A geographique reste incomplete tant que ces observations ne sont pas etablies.", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    documents = Path(args.documents) if args.documents else documents_folder()
    requested_source = getattr(args, "maps_source", "virtualstore")
    maps, candidates, selection = find_maps(args.carto_root, args.maps, requested_source == "virtualstore")
    print(f"Diagnostic {TOOL_VERSION} - source demandee : {requested_source}", flush=True)
    print(f"Dossier a analyser : {maps or 'aucun'} - {selection}", flush=True)
    protected = [Path(args.carto_root), Path(args.wow_root), documents / "WoWCarto_Maps_120", documents / "WoWCarto_Test_Originals"]
    protected.extend(Path(candidate["path"]) for candidate in candidates)
    if maps:
        protected.append(maps)
    output = safe_output(args.output or documents / "WoWCarto_Diagnostics" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ"), protected)
    report = {
        "schema_version": 1, "tool_version": TOOL_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {"os": platform.system(), "python": platform.python_version()},
        "maps": {"selected": str(maps) if maps else None, "selection_status": selection,
                 "requested_source": requested_source, "explicit_path": args.maps, "candidates": candidates},
        "classic": classic_build(args.wow_root),
        "application": {relative: file_record(Path(args.carto_root) / relative)
                        for relative in ("WowCartographe.exe", "StormLib.dll", "blp2decode.dll", "pref.ini", "catpref.bin",
                                         "database/Francais/master.loc", "database/US/master.loc")},
        "existing_backups": {
            "maps": str(documents / "WoWCarto_Maps_120"),
            "maps_directory_exists": (documents / "WoWCarto_Maps_120").is_dir(),
            "list": file_record(documents / "WoWCarto_Maps_120_list.txt"),
            "originals_directory_exists": (documents / "WoWCarto_Test_Originals").is_dir(),
        },
        "previous_test": test_image_state(maps, documents / "WoWCarto_Test_Originals"),
        "issues": [], "completion": "local_inventory_only_geography_unverified",
    }
    if report["classic"]["status"] != "identified_from_metadata":
        report["issues"].append("Build Classic Era non identifie : " + report["classic"].get("reason", "inconnu"))
    if not report["application"]["WowCartographe.exe"]["exists"]:
        report["issues"].append("Executable original introuvable au chemin fourni.")
    for relative, record in report["application"].items():
        if record.get("error"):
            report["issues"].append(f"{relative} : {record['error']}")
    if not maps or not maps.is_dir():
        report["issues"].append("Cartes absentes ou selection ambigue : relancer avec --maps. Pour le VirtualStore deja confirme sur le PC utilisateur : diagnostic-virtualstore.cmd.")
    else:
        inv = inventory(maps)
        report["inventory"] = inv
        report["issues"].extend(inv["issues"])
        records = inv["kalimdor"]
        for resolution in ("standard", "high"):
            contact_sheet(records, resolution, output / f"contact-{resolution}.png")
        seams, links = seam_candidates(records)
        write_json(output / "seams.json", {"status": "hypotheses_only", "sample_size": SAMPLE,
                                           "thresholds": {"mae_max": 20, "texture_min": 5, "best_second_gap_min": 1},
                                           "candidates": seams})
        components = provisional_layouts(records, links)
        report["hypotheses"] = {"components": len(components), "links": len(links), "status": "not_verified",
                                "proposed_components": components, "seam_candidates": seams}
        write_json(output / "hypotheses.json", {"components": components})
        consistent = consistent_hypotheses(records, links)
        write_json(output / "consistent-hypotheses.json", consistent)
        report["layout_review"] = consistent
        review_folder = output / "consistent-hypotheses"
        review_folder.mkdir()
        for index, component in enumerate(consistent["components"]):
            if component["conflicts"] or len(component["tiles"]) < 2:
                continue
            try:
                for labelled in (False, True):
                    suffix = "-labelled" if labelled else ""
                    assemble(component["tiles"], records, "standard", review_folder / f"component-{index:02}{suffix}.png",
                             "HYPOTHESE COHERENTE NON VALIDEE - verifier les raccords et reperes dans WoWCarto", tile_labels=labelled)
            except (KeyError, ValueError, OSError) as exc:
                report["issues"].append(f"Assemblage coherent provisoire {index} : {exc}")
        hypotheses = output / "hypotheses"
        hypotheses.mkdir()
        for index, component in enumerate(components):
            if component["conflicts"] or len(component["tiles"]) < 2:
                continue
            try:
                assemble(component["tiles"], records, "standard", hypotheses / f"component-{index:02}.png", "HYPOTHESE NON VALIDEE - positions et raccords a verifier")
            except (KeyError, ValueError, OSError) as exc:
                report["issues"].append(f"Assemblage provisoire {index} : {exc}")
        # An unfilled template deliberately has no positions or claims of evidence.
        write_json(output / "layout-template.json", {
            "schema_version": 1, "tiles": [], "landmarks": [],
            "inventory_hashes": {name: {res: pair.get(res, {}).get("sha256") for res in ("standard", "high")} for name, pair in records.items()},
            "instructions": "Ajouter uniquement les positions observees. Voir docs/LIVRAISON_A.md pour le schema et les preuves requises.",
        })
        report["local_review"] = write_review_page(output, records, consistent, report["classic"].get("selected"), TOOL_VERSION)
        if args.layout:
            try:
                if Path(args.layout).stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("Disposition superieure a 2 Mio : lecture refusee.")
                with Path(args.layout).open(encoding="utf-8-sig") as stream:
                    layout = json.load(stream)
                evidence = validate_layout(layout, records)
                # Preserve accepted declarations even when a whole-continent HD
                # assembly exceeds the memory bound. Never call that a bad layout.
                write_json(output / "layout-input.json", layout)
                report["layout"] = evidence
                evidence["assemblies"] = {}
                for resolution in ("standard", "high"):
                    try:
                        assemble(layout["tiles"], records, resolution, output / f"assembled-observed-{resolution}.png", "POSITIONNEMENT DECLARE PAR L UTILISATEUR - voir les preuves dans layout-input.json")
                        evidence["assemblies"][resolution] = {"status": "written_native_pixels"}
                    except (OSError, ValueError, KeyError) as exc:
                        evidence["assemblies"][resolution] = {"status": "not_written", "reason": str(exc)}
                        report["issues"].append(f"Disposition acceptee, assemblage {resolution} non produit : {exc}")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report["issues"].append(f"Disposition refusee : {exc}")
    # Recheck all hashed source inputs after processing, including original backups.
    def audit(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                try:
                    if digest(value["path"]) != value["sha256"]:
                        report["issues"].append(f"Source modifiee pendant le diagnostic : {value['path']}")
                except OSError as exc:
                    report["issues"].append(f"Source inaccessible au controle final : {exc}")
            for child in value.values():
                audit(child)
        elif isinstance(value, list):
            for child in value:
                audit(child)
    audit(report)
    report["inventory_run_status"] = "issues_detected" if report["issues"] else "completed_geography_unverified"
    write_json(output / "report.json", report)
    write_summary(report, output)
    print(f"Rapport local : {output}")
    if (output / "consistent-hypotheses").is_dir():
        for review_image in sorted((output / "consistent-hypotheses").glob("*-labelled.png")):
            print(f"Carte candidate a examiner localement (NON VALIDEE) : {review_image}")
    for issue in report["issues"]:
        print(f"A verifier : {issue}")
    print("Geographie non validee ; aucune carte du jeu ou de WoWCarto n a ete modifiee.")
    if report.get("local_review"):
        print(f"Verification locale des tuiles et reperes : {output / 'validation.html'}")
    print("A transmettre : report.json et report.md (images et assets a conserver localement).")
    return 2 if report["issues"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--carto-root", default=r"C:\Program Files (x86)\WoWCartographe")
    parser.add_argument("--wow-root", default=r"E:\World of Warcraft")
    parser.add_argument("--maps", help="Repertoire maps effectif, contenant le sous-dossier high")
    parser.add_argument("--maps-source", choices=("virtualstore", "auto"), default="virtualstore",
                        help="Reference de la mission : virtualstore par defaut ; auto conserve le refus en cas de sources multiples. --maps est prioritaire.")
    parser.add_argument("--documents", help="Remplacement explicite du dossier Documents pour tests ou installation atypique")
    parser.add_argument("--output", help="Nouveau repertoire local, hors des sources et depots Git")
    parser.add_argument("--layout", help="Disposition observee, avec preuves et SHA-256")
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Diagnostic impossible : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
