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


class FriendlyTests(Base):
    def test_step_label_falls_back_to_kind_never_to_id(self):
        view = assist.describe_workflow("name: x-y_z\nsteps:\n  - id: fin\n    type: end\n  - id: a\n    type: agent\n    description: Rédiger\n")
        self.assertEqual([s["label"] for s in view["steps"]], ["Fin", "Rédiger"])
        self.assertEqual(view["title"], "X y z")

    def test_problems_are_translated(self):
        import friendly
        self.assertIn("il manque le fichier", friendly.friendly_problem("étape « a » (file_write) : paramètre obligatoire « path » manquant", "Archiver"))
        self.assertIsNone(friendly.friendly_problem("étape n°2 : pas d'« id » (le moteur utilisera step_2)", "x"))
        self.assertIn("à revoir", friendly.friendly_problem("message inconnu", "x"))
        self.assertIn("mal écrit", friendly.friendly_problem("YAML invalide : boom"))

    def test_view_problems_carry_friendly_text(self):
        text = "name: w\nsteps:\n  - id: ecrire\n    type: file_write\n    content: x\n"
        issues = self.portal.check_yaml(text)["issues"]
        view = assist.describe_workflow(text, issues)
        problems = view["steps"][0]["problems"]
        self.assertTrue(problems and all("friendly" in p for p in problems))
        self.assertTrue(any("il manque le fichier" in (p["friendly"] or "") for p in problems))

    def test_cards_expose_icon_kind_and_friendly_title(self):
        (self.portal.dir / "rapport-hebdo.yaml").write_text("name: rapport-hebdo\nsteps:\n  - id: a\n    type: file_write\n    path: output/x.txt\n    content: x\n", encoding="utf-8")
        card = next(c for c in self.portal.list_cards() if c["filename"] == "rapport-hebdo.yaml")
        self.assertEqual(card["friendly_title"], "Rapport hebdo")
        self.assertEqual((card["steps"][0]["icon"], card["steps"][0]["kind"]), ("💾", "Enregistre un fichier"))
        self.assertEqual(card["steps"][0]["friendly"], "Enregistre un fichier")


class LanguageTests(Base):
    def test_prompts_ask_for_french(self):
        self.assertIn("en français", assist.build_prompt("tmp/x.yaml", "ajoute une étape"))
        self.assertIn("en français", assist.build_fix_prompt("tmp/x.yaml", [{"level": "error", "message": "m"}]))


class StructuredResultTests(Base):
    def test_steps_carry_icon_and_friendly_kind(self):
        view = assist.describe_workflow("name: x\nsteps:\n  - id: a\n    type: agent\n  - id: b\n    type: zzz\n")
        self.assertEqual((view["steps"][0]["icon"], view["steps"][0]["kind"]), ("🤖", "Réfléchit (IA)"))
        self.assertEqual(view["steps"][1]["kind"], "Étape")      # type inconnu : repli lisible

    """`view` (workflow résumé) et `changes` (ce qui a bougé) : calculés depuis le YAML, jamais demandés à CSAgent."""

    def test_added_step(self):
        a, _ = self.assistant(EXTRA)
        res = a.run(BASE, "ajoute une étape")
        self.assertEqual(res["changes"], {"added": ["b"], "removed": [], "changed": [], "reordered": False, "created": False})
        self.assertEqual([s["id"] for s in res["view"]["steps"]], ["a", "b", "fin"])
        self.assertEqual(res["view"]["name"], "demo")

    def test_removed_and_changed_steps(self):
        new = BASE.replace("message: ok", "message: autre").replace("  - id: fin\n    type: end\n    message: fini\n", "")
        a, _ = self.assistant(new)
        res = a.run(BASE, "x")
        self.assertEqual(res["changes"]["added"], [])
        self.assertEqual(res["changes"]["removed"], [{"id": "fin", "type": "end", "title": "fin"}])
        self.assertEqual(res["changes"]["changed"], [{"id": "a", "keys": ["message"]}])

    def test_unchanged_workflow_has_empty_changes(self):
        a, _ = self.assistant()                         # l'agent ne touche à rien
        res = a.run(BASE, "x")
        self.assertFalse(res["changed"])
        self.assertEqual(res["changes"], {"added": [], "removed": [], "changed": [], "reordered": False, "created": False})

    def test_reordered_steps(self):
        two = "name: d\nsteps:\n  - id: a\n    type: log\n    message: 1\n  - id: b\n    type: log\n    message: 2\n"
        swapped = "name: d\nsteps:\n  - id: b\n    type: log\n    message: 2\n  - id: a\n    type: log\n    message: 1\n"
        a, _ = self.assistant(swapped)
        res = a.run(two, "inverse")
        self.assertTrue(res["changes"]["reordered"])
        self.assertEqual((res["changes"]["added"], res["changes"]["removed"], res["changes"]["changed"]), ([], [], []))

    def test_created_when_starting_from_nothing(self):
        a, _ = self.assistant(BASE)
        res = a.run("", "crée un workflow")
        self.assertTrue(res["changes"]["created"])
        self.assertEqual([s["id"] for s in res["view"]["steps"]], ["a", "fin"])

    def test_title_comes_from_description_then_id(self):
        wf = "name: d\nsteps:\n  - id: a\n    type: log\n    description: Dire bonjour\n    message: x\n  - id: b\n    type: log\n    message: y\n"
        view = assist.describe_workflow(wf)
        self.assertEqual([s["title"] for s in view["steps"]], ["Dire bonjour", "b"])

    def test_step_without_id_gets_the_engine_fallback(self):
        view = assist.describe_workflow("name: d\nsteps:\n  - type: log\n    message: x\n  - id: b\n    type: log\n    message: y\n")
        self.assertEqual([s["id"] for s in view["steps"]], ["step_1", "b"])

    def test_flags_conditional_autonomous_and_branches(self):
        wf = ("name: d\nsteps:\n"
              "  - id: ia\n    type: agent\n    prompt: p\n    auto_confirm: true\n    skip_if: \"'{{x.output}}' == 'oui'\"\n"
              "  - id: ia2\n    type: agent\n    prompt: p\n"
              "  - id: ok\n    type: human_approval\n    on_approve: fin\n    on_reject: fin\n"
              "  - id: fin\n    type: end\n    message: m\n")
        view = assist.describe_workflow(wf)
        by_id = {s["id"]: s for s in view["steps"]}
        self.assertTrue(by_id["ia"]["autonomous"] and by_id["ia"]["conditional"])
        self.assertFalse(by_id["ia2"]["autonomous"] or by_id["ia2"]["conditional"])
        self.assertFalse(by_id["ok"]["autonomous"])           # auto_confirm ne concerne que les étapes agent
        self.assertTrue(view["branches"])
        self.assertFalse(assist.describe_workflow(BASE)["branches"])

    def test_problems_are_attached_to_the_right_step(self):
        wf = BASE.replace("  - id: fin", "  - id: w\n    type: file_write\n    path: output/{{run_id}}/x.txt\n  - id: fin")
        issues = self.portal.check_yaml(wf)["issues"]
        view = assist.describe_workflow(wf, issues)
        by_id = {s["id"]: s for s in view["steps"]}
        self.assertTrue(any(p["level"] == "error" and "content" in p["message"] for p in by_id["w"]["problems"]))
        self.assertEqual(by_id["a"]["problems"], [])
        self.assertEqual(by_id["fin"]["problems"], [])

    def test_run_attaches_problems_when_errors_remain(self):
        broken = BASE.replace("type: log", "type: logg", 1)
        a, _ = self.assistant(broken, broken, broken)
        res = a.run(BASE, "x")
        by_id = {s["id"]: s for s in res["view"]["steps"]}
        self.assertTrue(any("logg" in p["message"] for p in by_id["a"]["problems"]))

    def test_unreadable_yaml_gives_none(self):
        self.assertIsNone(assist.describe_workflow("a: [unclosed"))
        self.assertIsNone(assist.describe_workflow("juste du texte"))
        self.assertIsNone(assist.describe_workflow("name: d\nsteps: []\n"))
        self.assertIsNone(assist.step_changes(BASE, "a: [unclosed"))
        a, _ = self.assistant("a: [unclosed", "a: [unclosed", "a: [unclosed")
        res = a.run(BASE, "x")
        self.assertTrue(res["ok"])
        self.assertIsNone(res["view"])
        self.assertIsNone(res["changes"])

    def test_non_dict_steps_do_not_crash(self):
        view = assist.describe_workflow("name: d\nsteps:\n  - juste une chaîne\n  - id: b\n    type: log\n    message: y\n")
        self.assertEqual([s["id"] for s in view["steps"]], ["b"])

    def test_result_is_json_serializable(self):
        import json
        a, _ = self.assistant(EXTRA)
        json.dumps(a.run(BASE, "ajoute une étape"))


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


class OutputPathWarningTests(Base):

    def wf(self, path):
        return f"name: w\nsteps:\n  - id: ecrire\n    type: file_write\n    path: {path}\n    content: x\n  - id: fin\n    type: end\n    message: ok\n"

    def warns(self, path):
        res = self.portal.check_yaml(self.wf(path))
        return [i for i in res["issues"] if i["level"] == "warn" and "racine" in i["message"]], res

    def test_root_path_warns_on_the_right_line(self):
        w, res = self.warns("hello.html")
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["line"], 5)
        self.assertTrue(res["ok"])                               # avertissement, pas une erreur

    def test_tmp_and_variable_paths_are_fine(self):
        for p in ("tmp/hello.html", "./tmp/hello.html", "'tmp\\hello.html'", "'{{dossier}}/x.txt'"):
            self.assertEqual(self.warns(p)[0], [], p)

    def test_absolute_and_other_folders_warn(self):
        for p in ("/etc/x.txt", "workflows/x.yaml", "'C:/x.txt'"):
            self.assertEqual(len(self.warns(p)[0]), 1, p)

    def test_invalid_yaml_does_not_crash(self):
        self.assertFalse(self.portal.check_yaml("name: [")["ok"])

    def test_templates_have_no_warning(self):
        for t in portal.Portal.templates():
            res = self.portal.check_yaml(portal.Portal.template_text(t["id"], "x.yaml"))
            self.assertEqual(res["issues"], [], t["id"])


if __name__ == "__main__":
    unittest.main()
