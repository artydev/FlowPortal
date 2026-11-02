"""Tests de assist.py (assistant CSAgent) et des modèles de nouveaux workflows.

Lancement (depuis la racine) :
    py -m unittest discover -s tests -p "test_*.py" -v
"""

import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import assist  # noqa: E402
import portal  # noqa: E402
from assist import Assistant, line_diff  # noqa: E402

BASE = "name: demo\ndescription: d\nsteps:\n  - id: a\n    type: log\n    message: ok\n  - id: fin\n    type: end\n    message: fini\n"
EXTRA = BASE.replace("  - id: fin", "  - id: b\n    type: log\n    message: encore\n  - id: fin")


class FakeAgent:
    """Simule CSAgent : réécrit le brouillon dont le chemin figure dans le prompt."""

    def __init__(self, base: Path, *versions, output="Fait."):
        self.base, self.versions, self.output, self.calls = base, list(versions), output, []

    def __call__(self, params):
        self.calls.append(params)
        rel = re.search(r"« ([^»]+) »", params["prompt"]).group(1)
        if self.versions:
            v = self.versions.pop(0)
            if isinstance(v, Exception):
                return {"status": "error", "output": str(v)}
            if v is None:
                (self.base / rel).unlink()
            else:
                (self.base / rel).write_text(v, encoding="utf-8")
        return {"status": "ok", "output": self.output}


class Base(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "workflows").mkdir()
        self.portal = portal.Portal(self.root / "workflows", self.root / "runs.jsonl", base_dir=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def assistant(self, *versions, **kw):
        agent = FakeAgent(self.root, *versions)
        return Assistant(self.portal, self.root, agent, **kw), agent


class LineDiffTests(unittest.TestCase):

    def test_identical_gives_empty(self):
        self.assertEqual(line_diff("a\nb\n", "a\nb\n"), [])

    def test_added_line(self):
        rows = line_diff("a\nb\n", "a\nx\nb\n")
        self.assertEqual([(r["op"], r["text"]) for r in rows], [("eq", "a"), ("add", "x"), ("eq", "b")])

    def test_removed_and_changed(self):
        rows = line_diff("a\nb\nc\n", "a\nB\nc\n")
        self.assertEqual([(r["op"], r["text"]) for r in rows if r["op"] != "eq"], [("del", "b"), ("add", "B")])

    def test_long_equal_runs_are_collapsed(self):
        old = "\n".join(f"l{i}" for i in range(30)) + "\n"
        rows = line_diff(old, old.replace("l15", "XX"), context=2)
        self.assertLess(len(rows), 12)
        self.assertIn("gap", [r["op"] for r in rows])


class PromptTests(unittest.TestCase):

    def test_prompt_has_path_and_request_but_no_yaml(self):
        p = assist.build_prompt("tmp/assist/x/draft.yaml", "ajoute une étape")
        self.assertIn("tmp/assist/x/draft.yaml", p)
        self.assertIn("ajoute une étape", p)
        self.assertNotIn("steps:", p)

    def test_fix_prompt_lists_only_errors(self):
        p = assist.build_fix_prompt("f.yaml", [{"level": "error", "line": 3, "message": "boum"},
                                               {"level": "warn", "line": 4, "message": "attention"}])
        self.assertIn("ligne 3 : boum", p)
        self.assertNotIn("attention", p)


class RunTests(Base):

    def test_successful_change(self):
        a, agent = self.assistant(EXTRA)
        res = a.run(BASE, "ajoute une étape")
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["changed"])
        self.assertEqual(res["draft"], EXTRA)
        self.assertEqual(res["attempts"], 1)
        self.assertEqual(res["issues"], [])
        self.assertEqual(res["answer"], "Fait.")
        self.assertIn("add", [r["op"] for r in res["diff"]])
        self.assertEqual(len(agent.calls), 1)

    def test_draft_is_a_copy_of_the_given_text(self):
        seen = []

        def agent(params):
            rel = re.search(r"« ([^»]+) »", params["prompt"]).group(1)
            seen.append((self.root / rel).read_text(encoding="utf-8"))
            return {"status": "ok", "output": "rien"}
        res = Assistant(self.portal, self.root, agent).run(BASE, "ne change rien")
        self.assertEqual(seen, [BASE])
        self.assertFalse(res["changed"])
        self.assertEqual(res["diff"], [])

    def test_empty_text_starts_from_skeleton(self):
        a, _ = self.assistant(BASE)
        res = a.run("", "crée un workflow")
        self.assertTrue(res["ok"])
        self.assertTrue(res["changed"])

    def test_errors_are_fixed_by_a_second_call(self):
        broken = BASE.replace("type: log", "type: logg", 1)
        a, agent = self.assistant(broken, EXTRA)
        res = a.run(BASE, "fais quelque chose")
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempts"], 2)
        self.assertEqual(res["issues"], [])
        self.assertIn("erreurs de validation", agent.calls[1]["prompt"])

    def test_still_invalid_after_max_fixes_is_returned_with_issues(self):
        broken = BASE.replace("type: log", "type: logg", 1)
        a, agent = self.assistant(broken, broken, broken, max_fixes=2)
        res = a.run(BASE, "x")
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempts"], 3)
        self.assertTrue(any(i["level"] == "error" for i in res["issues"]))
        self.assertEqual(len(agent.calls), 3)

    def test_agent_error(self):
        a, _ = self.assistant(RuntimeError("Cannot reach CSAgent"))
        res = a.run(BASE, "x")
        self.assertFalse(res["ok"])
        self.assertIn("Cannot reach CSAgent", res["error"])

    def test_deleted_or_emptied_draft(self):
        self.assertFalse(self.assistant(None)[0].run(BASE, "x")["ok"])
        self.assertFalse(self.assistant("  \n")[0].run(BASE, "x")["ok"])

    def test_cancel(self):
        ev = threading.Event()

        def agent(params):
            ev.set()
            return {"status": "ok", "output": "ok"}
        res = Assistant(self.portal, self.root, agent).run(BASE, "x", cancel=ev)
        self.assertFalse(res["ok"])
        self.assertIn("Arrêté", res["error"])

    def test_empty_and_too_long_instruction(self):
        a, agent = self.assistant()
        self.assertFalse(a.run(BASE, "   ")["ok"])
        res = a.run(BASE, "x" * (assist.MAX_INSTRUCTION + 1))
        self.assertFalse(res["ok"])
        self.assertIn("trop longue", res["error"])
        self.assertEqual(agent.calls, [])

    def test_prompt_uses_an_absolute_path(self):
        a, agent = self.assistant(EXTRA)
        a.run(BASE, "x")
        self.assertTrue(Path(re.search(r"« ([^»]+) »", agent.calls[0]["prompt"]).group(1)).is_absolute())

    def test_working_folder_is_removed(self):
        a, _ = self.assistant(EXTRA)
        a.run(BASE, "x")
        self.assertEqual(list((self.root / "tmp" / "assist").glob("*")), [])

    def test_real_workflow_file_is_never_touched(self):
        (self.root / "workflows" / "w.yaml").write_text(BASE, encoding="utf-8")
        a, _ = self.assistant(EXTRA)
        a.run(BASE, "x")
        self.assertEqual((self.root / "workflows" / "w.yaml").read_text(encoding="utf-8"), BASE)

    def test_status_and_agent_events_are_emitted(self):
        events = []
        a, _ = self.assistant(EXTRA)
        a.run(BASE, "x", emit=events.append)
        self.assertEqual(events[0]["type"], "assist_status")

    def test_cleanup_stale(self):
        (self.root / "tmp" / "assist" / "vieux").mkdir(parents=True)
        self.assistant()[0].cleanup_stale()
        self.assertFalse((self.root / "tmp" / "assist").exists())

    def test_agent_receives_cancel_and_approval_hooks(self):
        ev, wa = threading.Event(), (lambda: "approved")
        a, agent = self.assistant(EXTRA)
        a.run(BASE, "x", cancel=ev, wait_approval=wa)
        self.assertIs(agent.calls[0]["_cancel"], ev)
        self.assertIs(agent.calls[0]["_wait_approval"], wa)


class TemplateTests(unittest.TestCase):

    def test_every_template_is_valid(self):
        for t in portal.Portal.templates():
            text = portal.Portal.template_text(t["id"], "mon-flux.yaml")
            issues = portal.schema.check(text)
            self.assertEqual(issues, [], t["id"])

    def test_name_comes_from_filename(self):
        text = portal.Portal.template_text("vide", "Mon Flux.yaml")
        self.assertIn("name: Mon-Flux", text)

    def test_unknown_template_and_bad_filename(self):
        with self.assertRaises(portal.PortalError):
            portal.Portal.template_text("nope", "a.yaml")
        with self.assertRaises(portal.PortalError):
            portal.Portal.template_text("vide", "../a.yaml")

    def test_templates_list_has_labels(self):
        self.assertEqual([t["id"] for t in portal.Portal.templates()], ["vide", "une-etape", "validation"])


if __name__ == "__main__":
    unittest.main()
