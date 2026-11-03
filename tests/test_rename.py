"""Tests de renommer et dupliquer un workflow."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portal  # noqa: E402

BODY = "steps:\n  - id: fin\n    type: end\n    message: ok\n"


class RenameDuplicateTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.wd = self.root / "workflows"
        self.wd.mkdir()
        self.hist = self.root / "runs.jsonl"
        self.portal = portal.Portal(self.wd, self.hist, base_dir=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        (self.wd / name).write_text(text, encoding="utf-8")

    def read(self, name):
        return (self.wd / name).read_text(encoding="utf-8")

    # --- renommer
    def test_rename_moves_file_bak_and_updates_default_name(self):
        self.write("ancien.yaml", "name: ancien\n" + BODY)
        self.write("ancien.yaml.bak", "vieux")
        res = self.portal.rename_workflow("ancien.yaml", "nouveau.yaml")
        self.assertTrue(res["ok"], res)
        self.assertFalse((self.wd / "ancien.yaml").exists())
        self.assertFalse((self.wd / "ancien.yaml.bak").exists())
        self.assertEqual((self.wd / "nouveau.yaml.bak").read_text(encoding="utf-8"), "vieux")
        self.assertIn("name: nouveau\n", self.read("nouveau.yaml"))
        self.assertEqual([c["filename"] for c in self.portal.list_cards()], ["nouveau.yaml"])

    def test_rename_keeps_a_custom_name_and_comment_lines(self):
        self.write("a.yaml", "# en-tête\nname: Mon rapport\n" + BODY)
        self.assertTrue(self.portal.rename_workflow("a.yaml", "b.yaml")["ok"])
        self.assertEqual(self.read("b.yaml"), "# en-tête\nname: Mon rapport\n" + BODY)

    def test_rename_repoints_history(self):
        self.write("a.yaml", "name: a\n" + BODY)
        self.hist.write_text(json.dumps({"id": "1", "file": "a.yaml"}) + "\n" + json.dumps({"id": "2", "file": "z.yaml"}) + "\n",
                             encoding="utf-8")
        self.assertTrue(self.portal.rename_workflow("a.yaml", "b.yaml")["ok"])
        files = [r["file"] for r in self.portal.history.load()]
        self.assertEqual(files, ["b.yaml", "z.yaml"])

    def test_rename_refusals(self):
        self.write("a.yaml", "name: a\n" + BODY)
        self.write("b.yaml", "name: b\n" + BODY)
        for old, new in (("a.yaml", "b.yaml"), ("a.yaml", "B.YAML"), ("a.yaml", "a.yaml"), ("zz.yaml", "c.yaml"),
                         ("a.yaml", "../c.yaml"), ("a.yaml", "c.txt"), ("a.yaml", "")):
            self.assertFalse(self.portal.rename_workflow(old, new)["ok"], (old, new))
        self.assertTrue((self.wd / "a.yaml").exists() and (self.wd / "b.yaml").exists())

    def test_rename_only_case_is_allowed(self):
        self.write("a.yaml", "name: a\n" + BODY)
        self.assertTrue(self.portal.rename_workflow("a.yaml", "A.yaml")["ok"])
        self.assertEqual([p.name for p in self.wd.glob("*.yaml")], ["A.yaml"])

    # --- dupliquer
    def test_duplicate_copies_and_renames_name(self):
        text = "name: original  # titre\ndescription: d\n" + BODY
        self.write("o.yaml", text)
        self.write("o.yaml.bak", "vieux")
        res = self.portal.duplicate_workflow("o.yaml", "copie.yaml")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.read("o.yaml"), text)                      # l'original est intact
        self.assertEqual(self.read("copie.yaml"), "name: copie  # titre\ndescription: d\n" + BODY)
        self.assertFalse((self.wd / "copie.yaml.bak").exists())
        self.assertEqual(len(self.portal.list_cards()), 2)
        self.assertTrue(all(not c.get("error") for c in self.portal.list_cards()))

    def test_duplicate_quotes_risky_names(self):
        self.write("o.yaml", "name: o\n" + BODY)
        for new, expect in (("2024.yaml", 'name: "2024"'), ("true.yaml", 'name: "true"'), ("mon rapport.yaml", 'name: "mon rapport"'),
                            ("ok-1.yaml", "name: ok-1")):
            self.assertTrue(self.portal.duplicate_workflow("o.yaml", new)["ok"], new)
            self.assertIn(expect + "\n", self.read(new))
            self.assertFalse(self.portal.check_yaml(self.read(new))["issues"], new)

    def test_duplicate_without_name_line_and_refusals(self):
        self.write("s.yaml", BODY)
        self.assertTrue(self.portal.duplicate_workflow("s.yaml", "t.yaml")["ok"])
        self.assertEqual(self.read("t.yaml"), BODY)
        for old, new in (("s.yaml", "S.yaml"), ("s.yaml", "t.yaml"), ("nope.yaml", "u.yaml"), ("s.yaml", "../u.yaml")):
            self.assertFalse(self.portal.duplicate_workflow(old, new)["ok"], (old, new))


if __name__ == "__main__":
    unittest.main()
