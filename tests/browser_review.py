"""Real Chromium, file://, synthetic images only. Separate optional QA dependency."""
import contextlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from test_diagnostic import d, make_fixture, snapshot


class OfflineReviewBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.args = make_fixture(cls.root, 3)
        cls.records = d.inventory(cls.args.maps)["kalimdor"]
        names = list(cls.records)
        links = [{"source": names[0], "target": names[1], "direction": "right", "mae": 0, "best_second_gap": 10},
                 {"source": names[1], "target": names[2], "direction": "down", "mae": 0, "best_second_gap": 10}]
        review = d.consistent_hypotheses(cls.records, links)
        cls.output = cls.root / "review"
        cls.output.mkdir()
        d.write_review_page(cls.output, cls.records, review, {"product": "wow_classic_era", "version": "SYNTHETIC"}, "test")
        cls.pw = sync_playwright().start()
        browser_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        cls.browser = cls.pw.chromium.launch(headless=True, **({"executable_path": browser_path} if browser_path else {}))
        cls.server = None
        cls.url = (cls.output / "validation.html").as_uri()
        if os.environ.get("REVIEW_QA_HTTP") == "1":
            # Some managed cloud browsers forbid file://. Test identical bytes
            # through a loopback-only fixture server; CI still tests file://.
            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *args):
                    pass
            cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(cls.output)))
            cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
            cls.thread.start()
            cls.url = f"http://127.0.0.1:{cls.server.server_port}/validation.html"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(accept_downloads=True)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors, self.requests = [], []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("request", lambda request: self.requests.append(request.url))
        self.page.goto(self.url)
        self.page.locator("#grid button").first.wait_for()

    def save_tile(self, index):
        self.page.locator("#grid button").nth(index).click(position={"x": 10.5, "y": 20.5})
        self.page.locator("#tile-evidence").fill(f"SYNTHETIC original observation {index}")
        self.page.locator("#original-confirmed").check()
        self.page.locator("#save-tile").click()

    def download(self):
        with self.page.expect_download() as received:
            self.page.locator("#download").click()
        return json.loads(Path(received.value.path()).read_text(encoding="utf-8"))

    def test_file_page_loads_images_without_network_and_without_preapproval(self):
        self.assertEqual(self.page.locator("#grid button").count(), 3)
        self.assertTrue(self.page.locator("#count").inner_text().startswith("0 positions"))
        self.page.locator("#download").click()
        self.assertIn("au moins une position", self.page.locator("#message").inner_text())
        self.assertTrue(self.page.locator("#grid img").evaluate_all("images => images.every(image => image.complete && image.naturalWidth === 32)"))
        self.assertFalse([url for url in self.requests if urlsplit(url).scheme in ("http", "https") and urlsplit(url).netloc != urlsplit(self.url).netloc])
        self.assertEqual(self.errors, [])

    def test_positions_landmarks_and_seams_round_trip_to_native_assemblies(self):
        before = snapshot(self.root)
        self.save_tile(0)
        # Pointer events are quantized to CSS pixels; fractional page layout
        # can move a clicked boundary by one native pixel. A user can refine it.
        self.assertLessEqual(abs(int(self.page.locator("#pixel-x").input_value()) - 10), 1)
        self.assertLessEqual(abs(int(self.page.locator("#pixel-y").input_value()) - 20), 1)
        self.page.locator("#pixel-x").fill("10")
        self.page.locator("#pixel-y").fill("20")
        self.page.locator("#landmark-evidence").fill("SYNTHETIC landmark observed in original")
        self.page.locator("#save-landmark").click()
        self.save_tile(1)
        self.page.locator("#seam-result").select_option("matches")
        self.page.locator("#seam-evidence").fill("SYNTHETIC matching seam")
        self.page.locator("#save-seam").click()
        layout = self.download()
        self.assertEqual(len(layout["tiles"]), 2)
        self.assertEqual(layout["landmarks"][0]["standard_pixel"], {"x": 10, "y": 20})
        self.assertEqual(layout["seam_observations"][0]["result"], "matches")
        self.assertFalse(d.validate_layout(layout, self.records)["geography_verified"])
        self.assertEqual(snapshot(self.root), before)
        saved_file = self.root / "downloaded-observations.json"
        saved_file.write_text(json.dumps(layout), encoding="utf-8")
        self.page.reload()
        self.page.locator("#resume").set_input_files(saved_file)
        self.page.get_by_text("2 positions", exact=False).first.wait_for()
        self.assertEqual(self.download(), layout)
        self.args.layout = str(saved_file)
        self.args.output = str(self.root / "validated-run")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(d.run(self.args), 2)  # Three pairs, not 53.
        for resolution in ("standard", "high"):
            self.assertTrue((Path(self.args.output) / f"assembled-observed-{resolution}.png").is_file())
        self.assertEqual(self.errors, [])

    def test_no_save_without_original_confirmation_and_no_overlap(self):
        self.page.locator("#grid button").first.click()
        self.page.locator("#tile-evidence").fill("SYNTHETIC note")
        self.page.locator("#save-tile").click()
        self.assertIn("confirmer", self.page.locator("#message").inner_text())
        self.save_tile(0)
        self.page.locator("#grid button").nth(1).click()
        self.page.locator("#tile-x").fill("0")
        self.page.locator("#tile-y").fill("0")
        self.page.locator("#tile-evidence").fill("SYNTHETIC overlapping claim")
        self.page.locator("#original-confirmed").check()
        self.page.locator("#save-tile").click()
        self.assertIn("même case", self.page.locator("#message").inner_text())
        self.assertEqual(len(self.download()["tiles"]), 1)

    def test_hash_mismatch_import_is_atomic_and_text_cannot_execute(self):
        self.save_tile(0)
        self.page.locator("#tile-evidence").fill("<img src=x onerror='window.injected=true'>")
        self.page.locator("#save-tile").click()
        layout = self.download()
        self.assertIsNone(self.page.evaluate("window.injected"))
        self.assertEqual(self.page.locator("#saved img").count(), 0)
        corrupted = json.loads(json.dumps(layout))
        corrupted["tiles"][0]["sha256"]["high"] = "wrong"
        file = self.root / "bad-observations.json"
        file.write_text(json.dumps(corrupted), encoding="utf-8")
        self.page.locator("#resume").set_input_files(file)
        self.page.get_by_text("Import refusé", exact=False).first.wait_for()
        self.assertEqual(self.download(), layout)
        self.assertEqual(self.errors, [])

    def test_pixel_bounds_zoom_and_removal_of_dependent_observations(self):
        self.page.locator("#scale").focus()
        self.page.locator("#scale").press("End")
        self.page.locator("#grid button").first.click(position={"x": 21, "y": 41})
        self.page.locator("#tile-evidence").fill("SYNTHETIC observed position")
        self.page.locator("#original-confirmed").check()
        self.page.locator("#save-tile").click()
        self.page.locator("#landmark-evidence").fill("SYNTHETIC landmark")
        self.page.locator("#save-landmark").click()
        self.assertEqual(self.download()["landmarks"][0]["standard_pixel"], {"x": 10, "y": 20})
        self.page.locator("#pixel-x").fill("32")
        self.page.locator("#save-landmark").click()
        self.assertIn("dans la tuile", self.page.locator("#message").inner_text())
        self.page.locator("#remove-tile").click()
        self.assertIn("0 positions déclarées ; 0 repères", self.page.locator("#count").inner_text())


if __name__ == "__main__":
    unittest.main()
