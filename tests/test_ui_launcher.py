"""Test de ui_launcher (sans Eel, sans vrai navigateur) : python3 tests/test_ui_launcher.py"""
import os, stat, sys, tempfile, time, unittest, warnings
from pathlib import Path
warnings.simplefilter("ignore", ResourceWarning)      # les faux navigateurs restent lancés en arrière-plan
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ui_launcher


def fake_bin(directory: Path, name: str, log: Path):
    p = directory / name
    p.write_text(f'#!/bin/sh\necho "{name} $@" >> "{log}"\n', encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


class OpenUi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.log = self.tmp / "calls.log"
        self.old_path, self.old_wsl = os.environ["PATH"], ui_launcher.is_wsl
        os.environ["PATH"] = str(self.tmp) + os.pathsep + "/nonexistent"
        ui_launcher.is_wsl = lambda: False

    def tearDown(self):
        os.environ["PATH"] = self.old_path; ui_launcher.is_wsl = self.old_wsl

    def calls(self):
        for _ in range(20):                                   # les faux navigateurs sont lancés en arrière-plan
            if self.log.exists() and self.log.read_text(): break
            time.sleep(0.1)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_chromium_en_fenetre_application(self):
        fake_bin(self.tmp, "chromium", self.log)
        how = ui_launcher.open_ui("http://localhost:1234/index.html")
        self.assertTrue(how.startswith("chromium:"))
        line = self.calls()[0]
        self.assertIn("--app=http://localhost:1234/index.html", line)
        self.assertIn("--window-size=1440,820", line)

    def test_chromium_prefere_a_firefox(self):
        fake_bin(self.tmp, "google-chrome", self.log); fake_bin(self.tmp, "firefox", self.log)
        self.assertTrue(ui_launcher.open_ui("http://x/").startswith("chromium:"))
        self.assertTrue(self.calls()[0].startswith("google-chrome"))

    def test_firefox_sans_chromium(self):
        fake_bin(self.tmp, "firefox", self.log)
        self.assertEqual(ui_launcher.open_ui("http://x/"), "firefox")
        self.assertEqual(self.calls(), ["firefox http://x/"])

    def test_wsl_ouvre_le_navigateur_de_windows(self):
        ui_launcher.is_wsl = lambda: True
        fake_bin(self.tmp, "explorer.exe", self.log); fake_bin(self.tmp, "firefox", self.log)
        self.assertEqual(ui_launcher.open_ui("http://x/"), "windows")
        self.assertEqual(self.calls(), ["explorer.exe http://x/"])

    def test_wsl_utilise_wslview_en_priorite(self):
        ui_launcher.is_wsl = lambda: True
        fake_bin(self.tmp, "wslview", self.log); fake_bin(self.tmp, "explorer.exe", self.log)
        self.assertEqual(ui_launcher.open_ui("http://x/"), "windows")
        self.assertTrue(self.calls()[0].startswith("wslview"))

    def test_epiphany_evite(self):
        class Epi:                                            # navigateur par défaut simulé
            name = "epiphany"
        orig = ui_launcher.webbrowser.get
        ui_launcher.webbrowser.get = lambda *a, **k: Epi()
        opened = []
        orig_open = ui_launcher.webbrowser.open
        ui_launcher.webbrowser.open = lambda u: opened.append(u) or True
        try:
            self.assertEqual(ui_launcher.open_ui("http://x/"), "none")
            self.assertEqual(opened, [])
        finally:
            ui_launcher.webbrowser.get, ui_launcher.webbrowser.open = orig, orig_open

    def test_rien_de_disponible(self):
        orig_get, orig_open = ui_launcher.webbrowser.get, ui_launcher.webbrowser.open
        ui_launcher.webbrowser.get = lambda *a, **k: (_ for _ in ()).throw(ui_launcher.webbrowser.Error("none"))
        ui_launcher.webbrowser.open = lambda u: False
        try:
            self.assertEqual(ui_launcher.open_ui("http://x/"), "none")
        finally:
            ui_launcher.webbrowser.get, ui_launcher.webbrowser.open = orig_get, orig_open

    def test_root_ajoute_no_sandbox(self):
        fake_bin(self.tmp, "chromium", self.log)
        ui_launcher.open_ui("http://x/")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.assertIn("--no-sandbox", self.calls()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
