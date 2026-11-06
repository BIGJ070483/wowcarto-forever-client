"""Synthetic data only: no WoW assets or original executable are required."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "diagnostic.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("diagnostic", SCRIPT)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def jpeg(path, size=(32, 32), color=(30, 60, 100)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, quality=100, subsampling=0)


def snapshot(root):
    return {path.relative_to(root).as_posix(): d.digest(path) for path in root.rglob("*") if path.is_file()}


def make_fixture(root, count=53):
    maps, wow, carto, documents = (root / name for name in ("maps", "wow", "carto", "documents"))
    carto.mkdir(parents=True)
    documents.mkdir()
    (carto / "WowCartographe.exe").write_bytes(b"SYNTHETIC FIXTURE, NOT AN EXECUTABLE")
    (wow / "_classic_era_").mkdir(parents=True)
    (wow / "Data" / "data").mkdir(parents=True)
    (wow / ".build.info").write_text(
        "Product!STRING:0|Active!DEC:1|Version!STRING:0|Build Key!HEX:16\n"
        "wow|1|retail-fixture|retail-key\n"
        "wow_classic_era|1|1.15.7.00001|era-fixture-key\n", encoding="utf-8")
    (wow / "_classic_era_" / ".flavor.info").write_text(
        "Product!STRING:0\nwow_classic_era\n", encoding="utf-8")
    for index in range(count):
        color = (index * 5 % 256, index * 7 % 256, index * 11 % 256)
        jpeg(maps / f"kalimdor{index:02}.jpg", color=color)
        jpeg(maps / "high" / f"kalimdor{index:02}.jpg", (128, 128), color)
    for relative in (Path("azeroth00.jpg"), Path("high/azeroth00.jpg")):
        jpeg(maps / relative)
        jpeg(documents / "WoWCarto_Test_Originals" / relative)
    return argparse.Namespace(maps=str(maps), wow_root=str(wow), carto_root=str(carto),
                              documents=str(documents), output=str(root / "output"), layout=None)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_typed_build_headers_and_shared_products(self):
        args = make_fixture(self.root, 1)
        info = d.classic_build(args.wow_root)
        self.assertEqual(info["status"], "identified_from_metadata")
        self.assertEqual(info["selected"]["version"], "1.15.7.00001")
        self.assertEqual(info["selected"]["product"], "wow_classic_era")

    def test_retail_is_never_a_fallback(self):
        args = make_fixture(self.root, 1)
        (Path(args.wow_root) / ".build.info").write_text("Product|Active|Version\nwow|1|99\n")
        info = d.classic_build(args.wow_root)
        self.assertEqual(info["status"], "unresolved")
        self.assertNotIn("selected", info)

    def test_multiple_active_era_rows_rejected(self):
        args = make_fixture(self.root, 1)
        (Path(args.wow_root) / ".build.info").write_text(
            "Product|Active|Version\nwow_classic_era|1|1\nwow_classic_era|1|2\n")
        self.assertEqual(d.classic_build(args.wow_root)["status"], "unresolved")

    def test_flavor_product_conflict_rejected(self):
        args = make_fixture(self.root, 1)
        (Path(args.wow_root) / "_classic_era_" / ".flavor.info").write_text("Product\nwow\n")
        self.assertEqual(d.classic_build(args.wow_root)["status"], "unresolved")

    def test_product_flavor_header_and_unknown_optional_schema(self):
        args = make_fixture(self.root, 1)
        flavor = Path(args.wow_root) / "_classic_era_" / ".flavor.info"
        flavor.write_text("Product Flavor!STRING:0\nwow_classic_era\n")
        self.assertEqual(d.classic_build(args.wow_root)["flavor_status"], "matching_product")
        flavor.write_text("UnknownColumn\nunknown-value\n")
        info = d.classic_build(args.wow_root)
        self.assertEqual(info["status"], "identified_from_metadata")
        self.assertIn("unrecognized", info["flavor_status"])

    def test_malformed_metadata_is_reported(self):
        args = make_fixture(self.root, 1)
        (Path(args.wow_root) / ".build.info").write_text("Product|Active|Version\nwow_classic_era|1\n")
        self.assertIn("illisibles", d.classic_build(args.wow_root)["reason"])

    def test_virtualstore_uses_localappdata_and_no_fixed_user(self):
        value = d.virtual_maps(r"C:\Program Files (x86)\WoWCartographe", self.root / "User X" / "Local")
        self.assertEqual(value, self.root / "User X" / "Local/VirtualStore/Program Files (x86)/WoWCartographe/maps")
        with self.assertRaises(ValueError):
            d.virtual_maps("relative", self.root)

    def test_two_populated_map_sources_need_explicit_selection(self):
        app = self.root / "carto"
        jpeg(app / "maps/kalimdor00.jpg")
        virtual = self.root / "virtual"
        jpeg(virtual / "azeroth00.jpg")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), patch.object(d, "virtual_maps", return_value=virtual):
            maps, _, status = d.find_maps(app)
            self.assertIsNone(maps)
            self.assertIn("ambiguous", status)
            self.assertEqual(d.find_maps(app, virtual)[0], virtual)

    def test_real_dimensions_and_missing_hd_are_reported(self):
        jpeg(self.root / "kalimdor02.jpg", (45, 30))
        jpeg(self.root / "high/kalimdor02.jpg", (180, 120))
        jpeg(self.root / "kalimdor09.jpg")
        result = d.inventory(self.root)
        self.assertEqual(result["kalimdor"]["kalimdor02.jpg"]["standard"]["width"], 45)
        self.assertEqual(result["kalimdor"]["kalimdor02.jpg"]["scale_x"], 4)
        self.assertIn("kalimdor09.jpg", result["only_standard"])
        self.assertTrue(result["issues"])

    def test_reported_179_virtual_and_36_physical_need_explicit_choice(self):
        args = make_fixture(self.root)
        maps = Path(args.maps)
        # 53 Kalimdor + azeroth00 + 125 other JPGs = the reported 179 files.
        for index in range(125):
            jpeg(maps / f"other{index:03}.jpg")
        physical = Path(args.carto_root) / "maps"
        for index in range(36):
            jpeg(physical / f"physical{index:02}.jpg")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), patch.object(d, "virtual_maps", return_value=maps):
            selected, candidates, _ = d.find_maps(args.carto_root)
            self.assertIsNone(selected)
            self.assertEqual([candidate["jpg_count"] for candidate in candidates], [179, 36])
            selected, _, status = d.find_maps(args.carto_root, maps)
            self.assertEqual(status, "explicit_override")
            inv = d.inventory(selected)
            self.assertEqual(inv["counts"]["standard"], 179)
            self.assertEqual(inv["kalimdor_counts"], {"standard": 53, "high": 53})

    def test_unselected_current_files_are_unknown_not_missing(self):
        args = make_fixture(self.root, 1)
        state = d.test_image_state(None, Path(args.documents) / "WoWCarto_Test_Originals")
        self.assertEqual(state["status"], "not_established")
        for row in state["files"]:
            self.assertIsNone(row["current"]["exists"])
            self.assertEqual(row["current"]["inspection_status"], "not_inspected_no_maps_selected")
            self.assertTrue(row["original"]["exists"])

    def test_mission_default_inventories_virtualstore_with_two_sources(self):
        args = make_fixture(self.root)
        virtual = Path(args.maps)
        jpeg(Path(args.carto_root) / "maps/physical00.jpg")
        args.maps = None
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), patch.object(d, "virtual_maps", return_value=virtual), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.run(args), 0)
        report = json.loads((Path(args.output) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["tool_version"], "0.1.4")
        self.assertEqual(report["maps"]["selected"], str(virtual))
        self.assertEqual(report["maps"]["requested_source"], "virtualstore")
        self.assertIsNone(report["maps"]["explicit_path"])
        self.assertEqual(report["inventory"]["kalimdor_counts"], {"standard": 53, "high": 53})

    def test_virtualstore_request_never_silently_uses_physical_maps(self):
        app = self.root / "carto"
        jpeg(app / "maps/kalimdor00.jpg")
        missing = self.root / "missing-virtual"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), patch.object(d, "virtual_maps", return_value=missing):
            selected, _, status = d.find_maps(app, prefer_virtualstore=True)
            self.assertEqual(selected, missing)
            self.assertEqual(status, "requested_virtualstore_missing_no_physical_fallback")

    def test_explicit_maps_override_mission_virtualstore_default(self):
        app = self.root / "carto"
        virtual, explicit = self.root / "virtual", self.root / "chosen"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}), patch.object(d, "virtual_maps", return_value=virtual):
            selected, _, status = d.find_maps(app, explicit, prefer_virtualstore=True)
        self.assertEqual(selected, explicit)
        self.assertEqual(status, "explicit_override")

    def test_corrupt_jpeg_does_not_pass_inventory(self):
        (self.root / "kalimdor00.jpg").write_bytes(b"broken jpeg")
        result = d.inventory(self.root)
        self.assertIn("error", result["kalimdor"]["kalimdor00.jpg"]["standard"])
        self.assertTrue(result["issues"])

    def test_jpg_extension_is_not_enough(self):
        file = self.root / "kalimdor00.jpg"
        Image.new("RGB", (32, 32)).save(file, format="PNG")
        self.assertIn("non JPEG", d.image_record(file)["error"])

    def test_flat_ocean_edges_never_create_links(self):
        args = make_fixture(self.root, 3)
        records = d.inventory(args.maps)["kalimdor"]
        candidates, links = d.seam_candidates(records)
        self.assertTrue(candidates)
        self.assertFalse(links)
        self.assertTrue(all(row["min_texture"] == 0 for row in candidates))

    def test_textured_mutual_border_matches_independently_of_names(self):
        rng = random.Random(12345)
        files = ["kalimdor40.jpg", "kalimdor03.jpg", "kalimdor17.jpg"]
        images = [Image.new("RGB", (64, 64)) for _ in files]
        for image in images:
            image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(4096)])
        for y in range(64):
            color = tuple(rng.randrange(256) for _ in range(3))
            images[0].putpixel((63, y), color)
            images[1].putpixel((0, y), color)
        for name, image in zip(files, images):
            image.save(self.root / name, quality=100, subsampling=0)
        records = {name: {"standard": d.image_record(self.root / name)} for name in files}
        _, links = d.seam_candidates(records)
        self.assertTrue(any(link["source"] == files[0] and link["target"] == files[1] and link["direction"] == "right" for link in links))
        self.assertTrue(all(link["status"] == "hypothesis_not_geographic_proof" for link in links))

    def test_inconsistent_cycle_is_not_hidden(self):
        names = ["a", "b", "c"]
        records = {name: {"standard": {}} for name in names}
        links = [{"source": "a", "target": "b", "direction": "right"},
                 {"source": "b", "target": "c", "direction": "down"},
                 {"source": "a", "target": "c", "direction": "down"}]
        self.assertTrue(d.provisional_layouts(records, links)[0]["conflicts"])

    def observed_graph(self):
        fixture = Path(__file__).parent / "fixtures/kalimdor-edges.json"
        links = json.loads(fixture.read_text(encoding="utf-8"))["links"]
        records = {name: {"standard": {}} for link in links for name in (link["source"], link["target"])}
        return records, links

    def test_reported_graph_retains_53_tiles_and_logs_two_constraints(self):
        records, links = self.observed_graph()
        self.assertTrue(d.provisional_layouts(records, links)[0]["conflicts"])
        result = d.consistent_hypotheses(records, links)
        self.assertEqual(result["status"], "consistent_hypothesis_not_geographic_proof")
        self.assertEqual(len(result["accepted_links"]), 77)
        self.assertEqual(len(result["rejected_links"]), 2)
        self.assertEqual(len(result["components"]), 1)
        component = result["components"][0]
        self.assertEqual(len(component["tiles"]), 53)
        self.assertFalse(component["conflicts"])
        self.assertEqual({(row["source"], row["target"]) for row in result["rejected_links"]},
                         {("kalimdor34.jpg", "kalimdor12.jpg"), ("kalimdor46.jpg", "kalimdor24.jpg")})
        for row in result["rejected_links"]:
            self.assertEqual(row["reason"], "inconsistent_cycle")
            self.assertEqual(row["independent_square_support"], 0)
            self.assertNotEqual(row["requested_delta"], row["delta_in_retained_graph"])
        self.assertEqual(len(result["accepted_links"]) + len(result["rejected_links"]), len(links))

    def test_consistent_graph_does_not_depend_on_numeric_tile_names(self):
        records, links = self.observed_graph()
        expected = d.consistent_hypotheses(records, links)["components"][0]["tiles"]
        names = list(records)
        rng = random.Random(47291)
        shuffled = names.copy()
        rng.shuffle(shuffled)
        rename = {old: f"arbitrary-{index:02}.jpg" for index, old in enumerate(shuffled)}
        renamed_records = {rename[name]: pair for name, pair in records.items()}
        renamed_links = [{**row, "source": rename[row["source"]], "target": rename[row["target"]]} for row in links]
        rng.shuffle(renamed_links)
        actual = d.consistent_hypotheses(renamed_records, renamed_links)["components"][0]["tiles"]
        self.assertEqual({rename[tile["file"]]: (tile["x"], tile["y"]) for tile in expected},
                         {tile["file"]: (tile["x"], tile["y"]) for tile in actual})

    def test_consistency_rejection_keeps_original_cycle_constraints(self):
        records = {name: {"standard": {}} for name in ("a", "b", "c")}
        links = [{"source": "a", "target": "b", "direction": "right", "mae": 1, "best_second_gap": 10},
                 {"source": "b", "target": "c", "direction": "down", "mae": 1, "best_second_gap": 9},
                 {"source": "a", "target": "c", "direction": "down", "mae": 5, "best_second_gap": 1}]
        original = json.loads(json.dumps(links))
        result = d.consistent_hypotheses(records, links)
        self.assertEqual(links, original)
        self.assertEqual(len(result["accepted_links"]), 2)
        self.assertEqual(len(result["rejected_links"]), 1)
        self.assertEqual(result["rejected_links"][0]["reason"], "inconsistent_cycle")
        self.assertFalse(result["components"][0]["conflicts"])

    def test_consistency_merge_refuses_overlapping_tiles(self):
        records = {name: {"standard": {}} for name in ("a", "b", "c", "d")}
        links = [{"source": "a", "target": "b", "direction": "right", "best_second_gap": 100},
                 {"source": "c", "target": "d", "direction": "right", "best_second_gap": 90},
                 {"source": "a", "target": "c", "direction": "right", "best_second_gap": 80}]
        result = d.consistent_hypotheses(records, links)
        self.assertEqual(len(result["components"]), 2)
        self.assertEqual(result["rejected_links"][0]["reason"], "tile_collision_between_components")

    def test_diagnostic_renders_labelled_candidate_from_real_synthetic_seams(self):
        args = make_fixture(self.root, 0)
        rng = random.Random(12345)
        names = ["kalimdor40.jpg", "kalimdor03.jpg", "kalimdor17.jpg"]
        images = [Image.new("RGB", (64, 64)) for _ in names]
        for image in images:
            image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(4096)])
        for y in range(64):
            color = tuple(rng.randrange(256) for _ in range(3))
            images[0].putpixel((63, y), color)
            images[1].putpixel((0, y), color)
        for name, image in zip(names, images):
            image.save(Path(args.maps) / name, quality=100, subsampling=0)
            image.resize((256, 256), Image.Resampling.NEAREST).save(Path(args.maps) / "high" / name, quality=100, subsampling=0)
        before = snapshot(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            # Expected issue: this small synthetic sector is not the full 53-tile set.
            self.assertEqual(d.run(args), 2)
        output = Path(args.output)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["layout_review"]["accepted_links"]), 1)
        self.assertEqual(report["layout_review"]["status"], "consistent_hypothesis_not_geographic_proof")
        with Image.open(output / "consistent-hypotheses/component-00.png") as image:
            self.assertEqual(image.size, (128, 96))
            with Image.open(Path(args.maps) / "kalimdor40.jpg") as original:
                self.assertEqual(image.getpixel((10, 20)), original.getpixel((10, 20)))
        with Image.open(output / "consistent-hypotheses/component-00-labelled.png") as image:
            self.assertEqual(image.size, (128, 96))
            self.assertEqual(image.getpixel((0, 20)), (255, 255, 0))
        self.assertEqual(before, {k: v for k, v in snapshot(self.root).items() if not k.startswith("output/")})

    def test_output_cannot_overwrite_source_or_git_checkout(self):
        protected = self.root / "maps"
        protected.mkdir()
        with self.assertRaises(ValueError):
            d.safe_output(protected / "output", [protected])
        repo = self.root / "repo"
        repo.mkdir()
        (repo / ".git").write_text("gitdir: elsewhere")
        with self.assertRaises(ValueError):
            d.safe_output(repo / "output", [])
        output = d.safe_output(self.root / "new", [protected])
        with self.assertRaises(FileExistsError):
            d.safe_output(output, [protected])

    def test_backup_difference_is_not_misreported_as_test_banner(self):
        args = make_fixture(self.root, 1)
        jpeg(Path(args.maps) / "azeroth00.jpg", color=(250, 0, 200))
        state = d.test_image_state(args.maps, Path(args.documents) / "WoWCarto_Test_Originals")
        self.assertEqual(state["status"], "different_from_existing_original_backup_inspect_visually")
        self.assertIn("ne prouve pas", state["note"])

    def test_verified_layout_requires_both_hashes_and_evidence(self):
        args = make_fixture(self.root, 1)
        records = d.inventory(args.maps)["kalimdor"]
        tile = {"file": "kalimdor00.jpg", "x": 0, "y": 0,
                "sha256": {res: records["kalimdor00.jpg"][res]["sha256"] for res in ("standard", "high")}}
        layout = {"schema_version": 1, "tiles": [tile]}
        with self.assertRaises(ValueError):
            d.validate_layout(layout, records)
        tile["evidence"] = "SYNTHETIC observed position, not WoWCarto"
        self.assertEqual(d.validate_layout(layout, records)["status"], "user_observations_not_independently_verified")
        tile["sha256"]["high"] = "wrong"
        with self.assertRaises(ValueError):
            d.validate_layout(layout, records)

    def test_layout_rejects_overlap_and_path_traversal(self):
        args = make_fixture(self.root, 2)
        records = d.inventory(args.maps)["kalimdor"]
        tiles = [{"file": name, "x": 0, "y": 0, "evidence": "fixture",
                  "sha256": {res: pair[res]["sha256"] for res in ("standard", "high")}}
                 for name, pair in records.items()]
        with self.assertRaisesRegex(ValueError, "Chevauchement"):
            d.validate_layout({"schema_version": 1, "tiles": tiles}, records)
        tiles[0]["file"] = "../outside.jpg"
        with self.assertRaisesRegex(ValueError, "inconnue"):
            d.validate_layout({"schema_version": 1, "tiles": tiles[:1]}, records)

    def test_native_pixel_assembly_no_resizing(self):
        jpeg(self.root / "kalimdor40.jpg", (45, 30), (255, 0, 0))
        jpeg(self.root / "kalimdor03.jpg", (45, 30), (0, 255, 0))
        records = {name: {"standard": d.image_record(self.root / name)} for name in ("kalimdor40.jpg", "kalimdor03.jpg")}
        output = self.root / "assembled.png"
        d.assemble([{"file": "kalimdor40.jpg", "x": 0, "y": 0},
                    {"file": "kalimdor03.jpg", "x": 1, "y": 0}], records, "standard", output, "HYPOTHESIS")
        with Image.open(output) as image:
            self.assertEqual(image.size, (90, 62))  # 30 native pixels + 32-pixel caption.
            self.assertGreater(image.getpixel((22, 15))[0], 240)
            self.assertGreater(image.getpixel((67, 15))[1], 240)

    def test_heterogeneous_dimensions_not_forced_into_grid(self):
        jpeg(self.root / "a.jpg", (32, 32))
        jpeg(self.root / "b.jpg", (40, 32))
        records = {name: {"standard": d.image_record(self.root / name)} for name in ("a.jpg", "b.jpg")}
        with self.assertRaisesRegex(ValueError, "heterogenes"):
            d.assemble([{"file": "a.jpg", "x": 0, "y": 0}, {"file": "b.jpg", "x": 1, "y": 0}], records, "standard", self.root / "no.png", "test")

    def test_full_53_pair_run_leaves_all_inputs_unchanged(self):
        args = make_fixture(self.root)
        before = snapshot(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.run(args), 0)
        after = snapshot(self.root)
        self.assertEqual(before, {key: value for key, value in after.items() if not key.startswith("output/")})
        report = json.loads((Path(args.output) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["inventory"]["kalimdor_counts"], {"standard": 53, "high": 53})
        self.assertEqual(report["completion"], "local_inventory_only_geography_unverified")
        self.assertEqual(report["hypotheses"]["links"], 0)
        self.assertTrue((Path(args.output) / "contact-high.png").is_file())
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(FileExistsError):
            d.run(args)

    def test_cli_returns_nonzero_and_report_for_missing_maps(self):
        args = make_fixture(self.root, 1)
        result = subprocess.run([sys.executable, str(SCRIPT), "--maps", str(self.root / "missing"),
                                 "--wow-root", args.wow_root, "--carto-root", args.carto_root,
                                 "--documents", args.documents, "--output", args.output], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads((Path(args.output) / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["inventory_run_status"], "issues_detected")

    def test_layout_end_to_end_writes_both_native_resolutions(self):
        args = make_fixture(self.root)
        records = d.inventory(args.maps)["kalimdor"]
        names = ["kalimdor40.jpg", "kalimdor03.jpg"]
        layout = {"schema_version": 1, "tiles": [
            {"file": name, "x": x, "y": 0, "evidence": "SYNTHETIC local observation",
             "sha256": {res: records[name][res]["sha256"] for res in ("standard", "high")}}
            for x, name in enumerate(names)]}
        layout_file = self.root / "layout.json"
        layout_file.write_text(json.dumps(layout), encoding="utf-8")
        args.layout = str(layout_file)
        before = snapshot(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.run(args), 0)
        for res, size in (("standard", (64, 64)), ("high", (256, 160))):
            with Image.open(Path(args.output) / f"assembled-observed-{res}.png") as image:
                self.assertEqual(image.size, size)
        report = json.loads((Path(args.output) / "report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["layout"]["covers_all_inventoried_kalimdor"])
        self.assertEqual(report["layout"]["status"], "user_observations_not_independently_verified")
        self.assertEqual(before, {k: v for k, v in snapshot(self.root).items() if not k.startswith("output/")})

    def test_invalid_layout_type_is_rejected_without_traceback(self):
        with self.assertRaises(ValueError):
            d.validate_layout([], {})
        with self.assertRaises(ValueError):
            d.validate_layout({"schema_version": 1, "tiles": [None]}, {})


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--make-fixture":
        root = Path(sys.argv[2])
        root.mkdir(parents=True, exist_ok=False)
        make_fixture(root)
    else:
        unittest.main()
