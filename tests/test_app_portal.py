"""Tests du branchement du portail dans app.py (avec un faux module `eel`) et du paramètre `inputs` du moteur.

Lancement (depuis la racine) :
    py -m unittest discover -s tests -p "test_*.py" -v
"""

import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portal  # noqa: E402
from engine import WorkflowEngine  # noqa: E402

WF = """name: mini
description: Petit test.
inputs:
  nom: Monde
steps:
  - id: ecrire
    type: file_write
    description: Écrit
    path: tmp/mini.txt
    content: "Bonjour {{nom}}"
  - id: fin
    type: end
    message: fini
"""


class FakeEel(types.ModuleType):
    """Remplace `eel` : enregistre les fonctions exposées et collecte les événements envoyés à l'interface."""

    def __init__(self):
        super().__init__("eel")
        self.exposed, self.events = {}, []

    def init(self, *a, **k):
        pass

    def expose(self, f=None, name=None):
        if callable(f):
            self.exposed[f.__name__] = f
        return f

    def start(self, *a, **k):
        pass

    def sleep(self, t):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a: self.events.append((name, a))


class EngineInputsTests(unittest.TestCase):

    def make(self, text=WF, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "w.yaml"
        path.write_text(text, encoding="utf-8")
        return WorkflowEngine(str(path), **kw)

    def test_default_inputs(self):
        self.assertEqual(self.make().ctx["nom"]["output"], "Monde")

    def test_inputs_override_declared(self):
        eng = self.make(inputs={"nom": "Salvatore"})
        self.assertEqual(eng.ctx["nom"]["output"], "Salvatore")

    def test_unknown_inputs_are_ignored(self):
        eng = self.make(inputs={"autre": "x"})
        self.assertNotIn("autre", eng.ctx)
        self.assertEqual(eng.ctx["nom"]["output"], "Monde")

    def test_workflow_without_inputs_block(self):
        eng = self.make("name: x\nsteps:\n  - id: a\n    type: end\n    message: m\n", inputs={"nom": "x"})
        self.assertNotIn("nom", eng.ctx)


class AppPortalTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fake = FakeEel()
        cls._patch = mock.patch.dict(sys.modules, {"eel": cls.fake})
        cls._patch.start()
        sys.modules.pop("app", None)
        cls._cwd = os.getcwd()
        import app
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()
        sys.modules.pop("app", None)
        os.chdir(cls._cwd)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "workflows").mkdir()
        (self.root / "workflows" / "mini.yaml").write_text(WF, encoding="utf-8")
        self.fake.events.clear()
        self.portal = portal.Portal(self.root / "workflows", self.root / "runs.jsonl", base_dir=self.root)
        self.patches = [mock.patch.object(self.app, "PORTAL", self.portal),
                        mock.patch.object(self.app, "APP_DIR", self.root),
                        mock.patch.object(self.app, "WORKFLOWS_DIR", self.root / "workflows")]
        for p in self.patches:
            p.start()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    def wait_idle(self, timeout=10):
        end = time.time() + timeout
        while self.app._run_busy.is_set() and time.time() < end:
            time.sleep(0.02)
        time.sleep(0.1)
        self.assertFalse(self.app._run_busy.is_set())

    def test_start_page_default_is_portal(self):
        self.assertEqual(self.app._start_page([]), "portal.html")
        self.assertEqual(self.app._start_page(["--portal"]), "portal.html")

    def test_start_page_classic(self):
        self.assertEqual(self.app._start_page(["--classic"]), "index.html")

    def test_portal_functions_are_exposed(self):
        for name in ("portal_list", "portal_read", "portal_check", "portal_save", "portal_runs",
                     "portal_run_detail", "portal_run", "portal_file_preview", "portal_open_file", "run_workflow"):
            self.assertIn(name, self.fake.exposed, name)

    def test_portal_list(self):
        data = self.app.portal_list()
        self.assertEqual([c["filename"] for c in data["cards"]], ["mini.yaml"])
        self.assertFalse(data["busy"])

    def test_portal_run_with_inputs_is_recorded(self):
        res = self.app.portal_run("mini.yaml", {"nom": "Salvatore", "inconnu": "x"})
        self.assertTrue(res["ok"], res)
        self.wait_idle()
        self.assertEqual((self.root / "tmp" / "mini.txt").read_text(encoding="utf-8"), "Bonjour Salvatore")
        saved = [a[0] for n, a in self.fake.events if n == "receiveEvent" and a[0].get("type") == "portal_run_saved"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["status"], "ok")
        run = self.app.portal_run_detail(saved[0]["run_id"])["run"]
        self.assertEqual(run["inputs"], {"nom": "Salvatore"})
        self.assertEqual(run["files"], [{"path": "tmp/mini.txt", "size": 17}])
        self.assertEqual(self.app.portal_list()["cards"][0]["last_run"]["status"], "ok")

    def test_portal_run_refused_while_busy(self):
        self.app._run_busy.set()
        try:
            res = self.app.portal_run("mini.yaml", {})
        finally:
            self.app._run_busy.clear()
        self.assertFalse(res["ok"])
        self.assertIn("déjà en cours", res["error"])

    def test_portal_run_unknown_or_bad_name(self):
        self.assertFalse(self.app.portal_run("absent.yaml", {})["ok"])
        self.assertFalse(self.app.portal_run("../x.yaml", {})["ok"])

    def test_portal_run_refused_when_prerequisites_missing(self):
        (self.root / "workflows" / "m.yaml").write_text(
            "name: m\nsteps:\n  - id: a\n    type: mcp_tools\n    server: nulle-part\n", encoding="utf-8")
        res = self.app.portal_run("m.yaml", {})
        self.assertFalse(res["ok"])
        self.assertIn("Prérequis", res["error"])

    def test_classic_run_workflow_is_recorded_too(self):
        self.app.run_workflow(str(self.root / "workflows" / "mini.yaml"))
        self.wait_idle()
        self.assertEqual(len(self.portal.history.load()), 1)

    def test_failed_run_is_recorded_as_error(self):
        (self.root / "workflows" / "bad.yaml").write_text(
            "name: bad\nsteps:\n  - id: a\n    type: shell\n    cmd: \"exit 3\"\n", encoding="utf-8")
        self.assertTrue(self.app.portal_run("bad.yaml", {})["ok"])
        self.wait_idle()
        self.assertEqual(self.portal.history.load()[-1]["status"], "error")

    def wait_event(self, kind, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if any(n == "receiveEvent" and a[0].get("type") == kind for n, a in self.fake.events):
                return True
            time.sleep(0.02)
        return False

    def test_stop_without_run(self):
        res = self.app.portal_stop()
        self.assertFalse(res["ok"])

    def test_stop_interrupts_a_running_step(self):
        (self.root / "workflows" / "dort.yaml").write_text(
            "name: dort\nsteps:\n  - id: dort\n    type: python\n    code: \"import time; time.sleep(30)\"\n"
            "    timeout: 60\n  - id: b\n    type: end\n    message: fin\n", encoding="utf-8")
        self.assertTrue(self.app.portal_run("dort.yaml", {})["ok"])
        self.assertTrue(self.wait_event("step_start"))
        time.sleep(0.5)
        t0 = time.time()
        self.assertTrue(self.app.portal_stop()["ok"])
        self.wait_idle()
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(self.portal.history.load()[-1]["status"], "cancelled")
        self.assertTrue(self.wait_event("workflow_cancelled"))

    def test_stop_releases_a_pending_approval(self):
        (self.root / "workflows" / "ok.yaml").write_text(
            "name: ok\nsteps:\n  - id: v\n    type: human_approval\n    message: ok ?\n    on_approve: f\n"
            "    on_reject: f\n  - id: f\n    type: end\n    message: fin\n", encoding="utf-8")
        self.assertTrue(self.app.portal_run("ok.yaml", {})["ok"])
        self.assertTrue(self.wait_event("human_approval_required"))
        self.assertTrue(self.app.portal_stop()["ok"])
        self.wait_idle()
        self.assertEqual(self.portal.history.load()[-1]["status"], "cancelled")

    def test_save_and_check(self):
        self.assertFalse(self.app.portal_check("name: x\nsteps:\n  - id: a\n    type: nope\n")["ok"])
        self.assertTrue(self.app.portal_save("mini.yaml", WF.replace("Petit test", "Autre"))["ok"])
        self.assertIn("Autre", self.app.portal_read("mini.yaml")["yaml"])

    def test_file_preview_text_binary_and_confinement(self):
        (self.root / "tmp").mkdir()
        (self.root / "tmp" / "a.txt").write_text("bonjour", encoding="utf-8")
        (self.root / "tmp" / "b.bin").write_bytes(b"\x00\x01\x02")
        outside = self.root.parent / "dehors.txt"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(lambda: outside.exists() and outside.unlink())
        ok = self.app.portal_file_preview("tmp/a.txt")
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["text"], "bonjour")
        self.assertTrue(self.app.portal_file_preview("tmp/b.bin")["binary"])
        self.assertFalse(self.app.portal_file_preview("../dehors.txt")["ok"])
        self.assertFalse(self.app.portal_file_preview(str(outside))["ok"])
        self.assertFalse(self.app.portal_file_preview("tmp")["ok"])

    def test_open_file_refuses_executables_and_outside(self):
        (self.root / "tmp").mkdir()
        (self.root / "tmp" / "run.bat").write_text("echo x", encoding="utf-8")
        (self.root / "tmp" / "ok.txt").write_text("x", encoding="utf-8")
        self.assertFalse(self.app.portal_open_file("tmp/run.bat")["ok"])
        self.assertFalse(self.app.portal_open_file("../x.txt")["ok"])
        with mock.patch("subprocess.Popen") as popen, mock.patch.object(self.app.sys, "platform", "linux"):
            self.assertTrue(self.app.portal_open_file("tmp/ok.txt")["ok"])
            self.assertTrue(popen.called)


if __name__ == "__main__":
    unittest.main()