"""Tests de la suppression d'un workflow (déplacement dans workflows/.corbeille/)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portal  # noqa: E402

WF = "name: x\nsteps:\n  - id: fin\n    type: end\n    message: ok\n"


class DeleteTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.wd = self.root / "workflows"
        self.wd.mkdir()
        (self.wd / "a.yaml").write_text(WF, encoding="utf-8")
        self.portal = portal.Portal(self.wd, self.root / "runs.jsonl", base_dir=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_moves_to_trash_and_leaves_the_list(self):
        res = self.portal.delete_workflow("a.yaml")
        self.assertTrue(res["ok"], res)
        self.assertFalse((self.wd / "a.yaml").exists())
        moved = list((self.wd / ".corbeille").glob("a.*.yaml"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_text(encoding="utf-8"), WF)
        self.assertEqual([c["filename"] for c in self.portal.list_cards()], [])

    def test_bak_follows(self):
        (self.wd / "a.yaml.bak").write_text("ancien", encoding="utf-8")
        self.assertTrue(self.portal.delete_workflow("a.yaml")["ok"])
        self.assertFalse((self.wd / "a.yaml.bak").exists())
        self.assertEqual(len(list((self.wd / ".corbeille").glob("*.bak"))), 1)

    def test_same_name_twice_does_not_overwrite(self):
        self.portal.delete_workflow("a.yaml")
        (self.wd / "a.yaml").write_text(WF + "# v2\n", encoding="utf-8")
        self.assertTrue(self.portal.delete_workflow("a.yaml")["ok"])
        self.assertEqual(len(list((self.wd / ".corbeille").glob("a.*.yaml"))), 2)

    def test_unknown_and_bad_names(self):
        for name in ("zzz.yaml", "../x.yaml", "a/b.yaml", "a.txt", "", None):
            self.assertFalse(self.portal.delete_workflow(name)["ok"], name)
        self.assertTrue((self.wd / "a.yaml").exists())

    def test_trash_is_not_listed_and_can_be_recreated(self):
        self.portal.delete_workflow("a.yaml")
        self.assertEqual(self.portal.list_cards(), [])
        self.assertTrue(self.portal.save_yaml("a.yaml", WF, create=True)["ok"])


if __name__ == "__main__":
    unittest.main()
