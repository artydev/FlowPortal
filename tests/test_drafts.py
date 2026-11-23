"""Tests des brouillons (test réussi puis validation) et du bac à sable des fichiers (output/ et documents/)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portal  # noqa: E402
import sandbox  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from tests.test_assist import Base, FakeAgent  # noqa: E402
from assist import Assistant  # noqa: E402

WF = "name: w\nsteps:\n  - id: a\n    type: log\n    message: ok\n  - id: fin\n    type: end\n    message: fini\n"


def write_wf(path_value: str) -> str:
    return f"name: w\nsteps:\n  - id: ecrire\n    type: file_write\n    path: {path_value}\n    content: x\n  - id: fin\n    type: end\n    message: ok\n"


class SandboxPathTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.roots = sandbox.roots(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def test_inside_is_allowed(self):
        for rel in ("output/a.txt", "output/run/x/y.txt", "documents/notes.md"):
            self.assertIsNone(sandbox.violation(self.base / rel, self.roots), rel)

    def test_outside_is_refused(self):
        for rel in ("a.txt", "workflows/x.yaml", "tmp/a.txt", "output/../secret.txt", "documentsX/a.txt"):
            self.assertIn("Accès refusé", sandbox.violation(self.base / rel, self.roots) or "", rel)

    def test_absolute_system_path_is_refused(self):
        self.assertIn("Accès refusé", sandbox.violation("/etc/passwd", self.roots))

    def test_static_check_judges_literal_paths_only(self):
        self.assertIsNone(sandbox.static_violation("output/{{run_id}}/r.html"))
        self.assertIsNone(sandbox.static_violation("{{saisie.fichier}}"))
        self.assertIsNone(sandbox.static_violation("./documents/a.txt"))
        for bad in ("rapport.html", "tmp/a.txt", "/etc/x", "C:/x.txt", "output/../x", "../documents/x"):
            self.assertIsNotNone(sandbox.static_violation(bad), bad)


class EngineSandboxTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_wf(self, path_value, sandboxed=True):
        wf = self.base / "w.yaml"
        wf.write_text(write_wf(path_value), encoding="utf-8")
        events = []
        WorkflowEngine(str(wf), event_callback=events.append,
                       sandbox_roots=sandbox.roots(self.base) if sandboxed else None).run()
        return events

    def test_write_inside_works(self):
        target = self.base / "output" / "ok.txt"
        events = self.run_wf(str(target))
        self.assertTrue(target.is_file())
        self.assertTrue(any(e["type"] == "workflow_done" for e in events))

    def test_write_outside_is_blocked_before_writing(self):
        target = self.base / "dehors.txt"
        events = self.run_wf(str(target))
        self.assertFalse(target.exists())
        errors = [e for e in events if e["type"] == "step_error"]
        self.assertTrue(errors and "Accès refusé" in errors[0]["output"])

    def test_without_sandbox_nothing_changes(self):
        target = self.base / "libre.txt"
        self.run_wf(str(target), sandboxed=False)
        self.assertTrue(target.is_file())


class DraftLifecycleTests(Base):

    def make(self, name="w.yaml", draft=True, text=WF):
        res = self.portal.save_yaml(name, text, create=True, draft=draft)
        self.assertTrue(res["ok"], res)

    def test_plain_save_is_active_and_unrestricted(self):
        self.make(draft=False)
        self.assertEqual(self.portal.status("w.yaml"), "active")
        self.assertIsNone(self.portal.sandbox_roots("w.yaml"))

    def test_draft_is_sandboxed_until_tested_then_validated(self):
        self.make()
        self.assertEqual(self.portal.status("w.yaml"), "draft")
        self.assertTrue(self.portal.sandbox_roots("w.yaml"))
        refused = self.portal.activate("w.yaml")
        self.assertFalse(refused["ok"])
        self.assertIn("Testez", refused["error"])
        self.assertEqual(self.portal.record_test("w.yaml", "r1", True), "tested")
        self.assertEqual(self.portal.activate("w.yaml"), {"ok": True, "status": "active"})
        self.assertEqual(self.portal.status("w.yaml"), "active")
        self.assertTrue(self.portal.sandbox_roots("w.yaml"), "le bac à sable reste actif après validation")

    def test_failed_test_keeps_draft(self):
        self.make()
        self.assertEqual(self.portal.record_test("w.yaml", "r1", False), "draft")

    def test_edit_after_test_requires_a_new_test(self):
        self.make()
        self.portal.record_test("w.yaml", "r1", True)
        self.portal.save_yaml("w.yaml", WF.replace("ok", "autre"))
        self.assertEqual(self.portal.status("w.yaml"), "draft")
        self.assertFalse(self.portal.activate("w.yaml")["ok"])

    def test_chat_modification_of_active_workflow_goes_back_to_draft(self):
        self.make(draft=False)
        self.portal.save_yaml("w.yaml", WF.replace("ok", "autre"), draft=True)
        self.assertEqual(self.portal.status("w.yaml"), "draft")

    def test_validated_workflow_edited_by_chat_is_draft_again(self):
        self.make()
        self.portal.record_test("w.yaml", "r1", True)
        self.portal.activate("w.yaml")
        self.portal.save_yaml("w.yaml", WF.replace("ok", "autre"), draft=True)
        self.assertEqual(self.portal.status("w.yaml"), "draft")

    def test_record_test_ignores_workflows_that_are_not_drafts(self):
        self.make(draft=False)
        self.assertEqual(self.portal.record_test("w.yaml", "r1", True), "active")
        self.assertFalse(self.portal.sandboxed("w.yaml"))

    def test_rename_keeps_state_and_test(self):
        self.make()
        self.portal.record_test("w.yaml", "r1", True)
        self.assertTrue(self.portal.rename_workflow("w.yaml", "autre.yaml")["ok"])
        self.assertEqual(self.portal.status("autre.yaml"), "tested")
        self.assertFalse(self.portal.sandboxed("w.yaml"))

    def test_duplicate_of_a_sandboxed_workflow_is_a_new_draft(self):
        self.make()
        self.portal.record_test("w.yaml", "r1", True)
        self.assertTrue(self.portal.duplicate_workflow("w.yaml", "copie.yaml")["ok"])
        self.assertEqual(self.portal.status("copie.yaml"), "draft")

    def test_delete_forgets_state(self):
        self.make()
        self.assertTrue(self.portal.delete_workflow("w.yaml")["ok"])
        self.assertFalse(self.portal.sandboxed("w.yaml"))
        self.make()                                              # même nom, nouveau brouillon propre
        self.assertEqual(self.portal.status("w.yaml"), "draft")

    def test_cards_expose_status_and_sandbox(self):
        self.make()
        self.make("b.yaml", draft=False)
        cards = {c["filename"]: c for c in self.portal.list_cards()}
        self.assertEqual((cards["w.yaml"]["status"], cards["w.yaml"]["sandboxed"]), ("draft", True))
        self.assertEqual((cards["b.yaml"]["status"], cards["b.yaml"]["sandboxed"]), ("active", False))

    def test_corrupt_state_file_means_everything_is_active(self):
        self.make()
        (self.root / "drafts.json").write_text("{pas du json", encoding="utf-8")
        self.assertEqual(self.portal.status("w.yaml"), "active")


class StaticSandboxIssuesTests(Base):

    def test_literal_paths_outside_are_errors_with_the_right_line(self):
        issues = self.portal.sandbox_issues(write_wf("rapport.html"))
        self.assertEqual(len(issues), 1)
        self.assertEqual((issues[0]["level"], issues[0]["line"]), ("error", 5))

    def test_allowed_and_templated_paths_pass(self):
        for p in ("output/{{run_id}}/r.html", "documents/a.txt", "'{{saisie.fichier}}'"):
            self.assertEqual(self.portal.sandbox_issues(write_wf(p)), [], p)

    def test_invalid_yaml_does_not_crash(self):
        self.assertEqual(self.portal.sandbox_issues("name: ["), [])


class AssistantSandboxTests(Base):

    def test_sandbox_errors_trigger_a_fix_pass(self):
        bad, good = write_wf("rapport.html"), write_wf("output/{{run_id}}/rapport.html")
        assistant, agent = self.assistant(bad, good)
        res = assistant.run("", "écris un rapport", sandbox=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempts"], 2)
        self.assertIn("output/{{run_id}}/rapport.html", res["draft"])
        self.assertIn("documents/", agent.calls[0]["prompt"])

    def test_without_sandbox_flag_no_such_check(self):
        assistant, agent = self.assistant(write_wf("rapport.html"))
        res = assistant.run("", "écris un rapport")
        self.assertEqual(res["attempts"], 1)
        self.assertNotIn("documents/", agent.calls[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
