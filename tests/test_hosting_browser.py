"""Anonymous contribution and corrections in a real local browser."""
from contextlib import closing
import importlib.util
import os
import sqlite3
import unittest
from tests import test_hosting_http as http
from tests.test_community_http import CommunityHttpTests

@unittest.skipUnless(http.PHP and importlib.util.find_spec("playwright"), "PHP pdo_sqlite and Playwright required")
class HostingBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): CommunityHttpTests.setUpClass()
    @classmethod
    def tearDownClass(cls): CommunityHttpTests.tearDownClass()
    def test_anonymous_upload_and_correction_preserve_other_bytes(self):
        from playwright.sync_api import sync_playwright
        fixture = CommunityHttpTests("test_anonymous_upload_edit_history_conflict_and_closed_mode")
        fixture.setUp(); h = fixture.h
        data, row = http.native_fixture()
        data = data.replace(b"TEST \xe9", b"<img src=x onerror=alert(1)>")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=os.environ.get("WCF_CHROMIUM_BIN") or None)
            page = browser.new_page(); dialogs=[]
            page.on("dialog", lambda dialog: dialogs.append(dialog.message))
            page.goto(h.url, wait_until="networkidle")
            page.locator("#community").wait_for(state="visible")
            self.assertEqual(page.locator("input[type=password]").count(), 0)
            page.locator("#file").set_input_files({"name":"fixture.txt", "mimeType":"text/plain", "buffer":data})
            page.get_by_role("button", name="Partager les annotations").click()
            page.get_by_text("2 annotations partagées.").wait_for()
            self.assertEqual(page.locator("#items form").count(), 2)
            self.assertEqual(page.locator("#items img").count(), 0)
            form = page.locator("#items form").first
            form.get_by_label("Nom").fill("Corrigé")
            form.get_by_role("button", name="Enregistrer la correction").click()
            page.get_by_text("Correction enregistrée.").wait_for()
            with closing(sqlite3.connect(h.database)) as db:
                db.text_factory=bytes
                current=db.execute("SELECT raw_record,revision FROM wcf_community_annotations ORDER BY id").fetchall()
            original=row.replace(b"TEST \xe9", b"<img src=x onerror=alert(1)>")
            self.assertEqual(current[0], (original.replace(b"<img src=x onerror=alert(1)>", b"Corrig\xe9"), 2))
            self.assertEqual(current[1], (original, 1)); self.assertEqual(dialogs, [])
            page.locator('#items form').first.get_by_role('button', name='Voir les versions précédentes').click()
            page.get_by_role('button', name='Restaurer cette version').first.click()
            page.get_by_text('Version précédente restaurée.').wait_for()
            with closing(sqlite3.connect(h.database)) as db:
                db.text_factory=bytes
                self.assertEqual(db.execute('SELECT raw_record,revision FROM wcf_community_annotations WHERE id=1').fetchone(), (original,3))
            browser.close()
    def test_closed_preview_hides_editing(self):
        from playwright.sync_api import sync_playwright
        fixture = CommunityHttpTests("test_anonymous_upload_edit_history_conflict_and_closed_mode")
        fixture.setUp(); h=fixture.h; h.write_config(False)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True, executable_path=os.environ.get("WCF_CHROMIUM_BIN") or None)
            page=browser.new_page(); page.goto(h.url, wait_until="networkidle")
            page.get_by_text("Le service communautaire est en préparation.").wait_for()
            self.assertTrue(page.locator("#community").is_hidden()); browser.close()
