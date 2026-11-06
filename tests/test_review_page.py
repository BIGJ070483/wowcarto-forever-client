"""Observation workflow and bounds; all images and metadata are synthetic."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from test_diagnostic import d, make_fixture, snapshot


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = make_fixture(self.root, 2)
        self.records = d.inventory(self.args.maps)["kalimdor"]

    def layout(self):
        return {"schema_version": 1, "tiles": [
            {"file": name, "x": index, "y": 0, "evidence": "SYNTHETIC observation",
             "sha256": {res: pair[res]["sha256"] for res in ("standard", "high")}}
            for index, (name, pair) in enumerate(self.records.items())]}

    def test_offline_page_uses_native_copies_and_no_installation_paths(self):
        before = snapshot(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.run(self.args), 2)  # Only two synthetic pairs, not 53.
        output = Path(self.args.output)
        html = (output / "validation.html").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), html)
        self.assertNotIn("/*__REVIEW_DATA__*/", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("const positions = new Map()", html)
        for name in self.records:
            with Image.open(output / "review-tiles" / f"{name}.png") as generated, Image.open(Path(self.args.maps) / name) as original:
                self.assertEqual(generated.size, original.size)
                self.assertEqual(generated.tobytes(), original.convert("RGB").tobytes())
        self.assertEqual(before, {key: value for key, value in snapshot(self.root).items() if not key.startswith("output/")})
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["local_review"]["tiles"], 2)
        self.assertNotIn("layout", report)

    def test_embedded_build_text_cannot_close_json_script(self):
        output = self.root / "review"
        output.mkdir()
        review = d.consistent_hypotheses(self.records, [])
        d.write_review_page(output, self.records, review, {"version": "</script><script>alert(1)</script>"}, "test")
        html = (output / "validation.html").read_text(encoding="utf-8")
        payload = html.split('<script id="review-data" type="application/json">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("<", payload)
        self.assertEqual(json.loads(payload)["build"]["version"], "</script><script>alert(1)</script>")

    def test_pixel_landmark_is_bounded_and_requires_textual_evidence(self):
        layout = self.layout()
        mark = {"name": "SYNTHETIC landmark", "file": layout["tiles"][0]["file"],
                "standard_pixel": {"x": 31, "y": 0}, "evidence": "SYNTHETIC capture reference"}
        layout["landmarks"] = [mark]
        accepted = d.validate_layout(layout, self.records)
        self.assertFalse(accepted["geography_verified"])
        for point in ({"x": 32, "y": 0}, {"x": True, "y": 0}, {"x": -1, "y": 0}, []):
            mark["standard_pixel"] = point
            with self.assertRaisesRegex(ValueError, "pixel"):
                d.validate_layout(layout, self.records)
        mark["standard_pixel"] = {"x": 0, "y": 0}
        mark["evidence"] = {"claim": "not a textual observation"}
        with self.assertRaisesRegex(ValueError, "preuve"):
            d.validate_layout(layout, self.records)

    def test_matching_seam_must_agree_with_declared_positions(self):
        layout = self.layout()
        source, target = [tile["file"] for tile in layout["tiles"]]
        seam = {"source": source, "target": target, "direction": "right", "result": "matches", "evidence": "SYNTHETIC seam checked"}
        layout["seam_observations"] = [seam]
        self.assertEqual(d.validate_layout(layout, self.records)["seam_observations"], [seam])
        seam["direction"] = "down"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            d.validate_layout(layout, self.records)
        seam["result"] = "contradicts"
        self.assertFalse(d.validate_layout(layout, self.records)["geography_verified"])
        seam["source"] = []
        with self.assertRaises(ValueError):
            d.validate_layout(layout, self.records)

    def test_bool_schema_version_is_not_an_integer_version(self):
        layout = self.layout()
        layout["schema_version"] = True
        with self.assertRaises(ValueError):
            d.validate_layout(layout, self.records)

    def test_hd_failure_preserves_accepted_layout_and_standard_assembly(self):
        layout = self.layout()
        layout_file = self.root / "observations.json"
        layout_file.write_text(json.dumps(layout), encoding="utf-8")
        self.args.layout = str(layout_file)
        original = d.assemble

        def bounded(tiles, records, resolution, *args, **kwargs):
            if resolution == "high":
                raise ValueError("Assemblage superieur a 80 millions de pixels ; reduire la composante.")
            return original(tiles, records, resolution, *args, **kwargs)

        with patch.object(d, "assemble", side_effect=bounded), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.run(self.args), 2)
        output = Path(self.args.output)
        self.assertEqual(json.loads((output / "layout-input.json").read_text(encoding="utf-8")), layout)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["layout"]["assemblies"]["standard"]["status"], "written_native_pixels")
        self.assertEqual(report["layout"]["assemblies"]["high"]["status"], "not_written")
        self.assertFalse(any("Disposition refusee" in issue for issue in report["issues"]))
        self.assertTrue((output / "assembled-observed-standard.png").is_file())
        self.assertFalse((output / "assembled-observed-high.png").exists())

    def test_real_hd_area_limit_is_checked_before_allocation(self):
        tiles = [{"file": name, "x": index * 100, "y": 0} for index, name in enumerate(self.records)]
        with self.assertRaisesRegex(ValueError, "80 millions"):
            # Synthetic metadata, deliberately beyond bound; no canvas allocated.
            records = {name: {"high": {**pair["high"], "width": 1024, "height": 1024}} for name, pair in self.records.items()}
            d.assemble(tiles, records, "high", self.root / "impossible.png", "test")
        self.assertFalse((self.root / "impossible.png").exists())

    def test_large_metadata_and_empty_headers_are_rejected(self):
        metadata = self.root / "metadata"
        metadata.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "2 Mio"):
            d.read_metadata(metadata)
        metadata.write_text("|Active\nproduct|1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            d.read_metadata(metadata)


if __name__ == "__main__":
    unittest.main()
