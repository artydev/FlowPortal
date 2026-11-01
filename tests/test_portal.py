"""Tests de portal.py : cartes, prérequis, écriture sécurisée, historique.

Lancement (depuis la racine) :
    py -m unittest discover -s tests -p "test_*.py" -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import portal  # noqa: E402
from portal import Portal, PortalError, RunRecorder, safe_name, summary_of, mask  # noqa: E402

SIMPLE = """name: demo-simple
description: Premier travail. Deuxième phrase qui ne doit pas apparaître.
inputs:
  pays: France
  n: 3
steps:
  - id: ecrire
    type: file_write
    description: Écrit un fichier
    path: tmp/out.txt
    content: bonjour
  - id: ok
    type: end
    message: fini
"""

HUMAN = """name: avec-humain
description: Demande une validation.
steps:
  - id: ask
    type: human_approval
    description: Valider
    message: ok ?
    on_approve: fin
    on_reject: fin
  - id: fin
    type: end
    message: fini
"""

MCP = """name: avec-mcp
description: Appelle un serveur MCP.
steps:
  - id: outils
    type: mcp_tools
    server: goal-api
  - id: fin
    type: end
    message: fini
"""


class Base(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.wdir = self.root / "workflows"
        self.wdir.mkdir()
        self.portal = Portal(self.wdir, self.root / "runs.jsonl", mcp_config="mcp.json", base_dir=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, name, text):
        (self.wdir / name).write_text(text, encoding="utf-8")


class HelperTests(unittest.TestCase):

    def test_safe_name_accepts_yaml(self):
        self.assertEqual(safe_name("weather.yaml"), "weather.yaml")
        self.assertEqual(safe_name("cv-analyse.yml"), "cv-analyse.yml")

    def test_safe_name_rejects_paths_and_extensions(self):
        for bad in ("", "../x.yaml", "a/b.yaml", "a\\b.yaml", "x.txt", "x.yaml.bak", "..yaml", ".hidden.yaml", None):
            with self.assertRaises(PortalError, msg=repr(bad)):
                safe_name(bad)

    def test_summary_is_first_sentence(self):
        self.assertEqual(summary_of("Un. Deux."), "Un.")
        self.assertEqual(summary_of("  a\n  b  "), "a b")
        self.assertTrue(len(summary_of("x" * 500)) <= 220)

    def test_mask_hides_secret_env_values(self):
        with mock.patch.dict(os.environ, {"MY_API_KEY": "abcdefgh12345", "PLAIN": "abcdefgh12345x"}):
            self.assertEqual(mask("Bearer abcdefgh12345 fin"), "Bearer *** fin")

    def test_mask_ignores_short_values(self):
        with mock.patch.dict(os.environ, {"MY_TOKEN": "abc"}):
            self.assertEqual(mask("abc"), "abc")


class CardTests(Base):

    def test_card_fields(self):
        self.put("demo.yaml", SIMPLE)
        card = self.portal.list_cards()[0]
        self.assertEqual(card["filename"], "demo.yaml")
        self.assertEqual(card["name"], "demo-simple")
        self.assertEqual(card["title"], "demo simple")
        self.assertEqual(card["summary"], "Premier travail.")
        self.assertEqual(card["inputs"], {"pays": "France", "n": "3"})
        self.assertEqual([s["id"] for s in card["steps"]], ["ecrire", "ok"])
        self.assertEqual(card["steps"][0]["label"], "Écrit un fichier")
        self.assertTrue(card["ready"])
        self.assertIsNone(card["last_run"])

    def test_tags(self):
        self.put("a.yaml", SIMPLE)
        self.put("b.yaml", HUMAN)
        cards = {c["filename"]: c for c in self.portal.list_cards()}
        self.assertFalse(cards["a.yaml"]["tags"]["human"])
        self.assertTrue(cards["b.yaml"]["tags"]["human"])

    def test_invalid_yaml_gives_error_card(self):
        self.put("bad.yaml", "name: [oups\n")
        card = self.portal.list_cards()[0]
        self.assertFalse(card["ready"])
        self.assertTrue(card["error"])

    def test_non_mapping_yaml(self):
        self.put("list.yaml", "- a\n- b\n")
        self.assertFalse(self.portal.list_cards()[0]["ready"])

    def test_bak_and_other_files_are_not_cards(self):
        self.put("a.yaml", SIMPLE)
        self.put("a.yaml.bak", SIMPLE)
        self.put("notes.txt", "x")
        self.assertEqual([c["filename"] for c in self.portal.list_cards()], ["a.yaml"])

    def test_mcp_requirements_missing_config(self):
        self.put("m.yaml", MCP)
        card = self.portal.list_cards()[0]
        self.assertTrue(card["tags"]["mcp"])
        self.assertFalse(card["ready"])
        self.assertIn("mcp.json", card["missing"][0])

    def test_mcp_requirements_missing_env_then_ready(self):
        self.put("m.yaml", MCP)
        cfg = {"mcpServers": {"goal-api": {"url": "https://x/mcp", "headers": {"Authorization": "Bearer ${PORTAL_TEST_KEY}"}}}}
        (self.root / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PORTAL_TEST_KEY", None)
            card = self.portal.list_cards()[0]
            self.assertFalse(card["ready"])
            self.assertIn("PORTAL_TEST_KEY", card["missing"][0])
            self.assertEqual(card["requirements"]["env"], [{"name": "PORTAL_TEST_KEY", "set": False}])
            os.environ["PORTAL_TEST_KEY"] = "secret-value-123"
            card = self.portal.list_cards()[0]
        self.assertTrue(card["ready"])
        self.assertNotIn("secret-value-123", json.dumps(card))

    def test_mcp_server_not_declared(self):
        self.put("m.yaml", MCP)
        (self.root / "mcp.json").write_text('{"mcpServers": {"autre": {"url": "https://x"}}}', encoding="utf-8")
        card = self.portal.list_cards()[0]
        self.assertFalse(card["ready"])
        self.assertIn("goal-api", card["missing"][0])


class FileTests(Base):

    def test_read_yaml(self):
        self.put("demo.yaml", SIMPLE)
        res = self.portal.read_yaml("demo.yaml")
        self.assertTrue(res["ok"])
        self.assertEqual(res["yaml"], SIMPLE)

    def test_read_rejects_traversal_and_missing(self):
        self.assertFalse(self.portal.read_yaml("../secret.yaml")["ok"])
        self.assertFalse(self.portal.read_yaml("absent.yaml")["ok"])

    def test_check_yaml_reports_issues(self):
        res = self.portal.check_yaml("name: x\nsteps:\n  - id: a\n    type: inconnu\n")
        self.assertFalse(res["ok"])
        self.assertEqual(res["issues"][0]["level"], "error")

    def test_check_yaml_valid(self):
        self.assertTrue(self.portal.check_yaml(SIMPLE)["ok"])

    def test_save_valid_writes_and_keeps_backup(self):
        self.put("demo.yaml", SIMPLE)
        new = SIMPLE.replace("bonjour", "salut")
        res = self.portal.save_yaml("demo.yaml", new)
        self.assertTrue(res["ok"], res)
        self.assertIn("salut", (self.wdir / "demo.yaml").read_text(encoding="utf-8"))
        self.assertEqual((self.wdir / "demo.yaml.bak").read_text(encoding="utf-8"), SIMPLE)
        self.assertEqual([p.name for p in self.wdir.glob(".*tmp")], [])

    def test_save_with_errors_refused_and_file_untouched(self):
        self.put("demo.yaml", SIMPLE)
        res = self.portal.save_yaml("demo.yaml", "name: x\nsteps:\n  - id: a\n    type: inconnu\n")
        self.assertFalse(res["ok"])
        self.assertTrue(res["issues"])
        self.assertEqual((self.wdir / "demo.yaml").read_text(encoding="utf-8"), SIMPLE)
        self.assertFalse((self.wdir / "demo.yaml.bak").exists())

    def test_save_rejects_bad_names(self):
        for bad in ("../evil.yaml", "sub/x.yaml", "x.py"):
            self.assertFalse(self.portal.save_yaml(bad, SIMPLE, create=True)["ok"], bad)
        self.assertEqual(list(self.root.glob("evil*")), [])

    def test_save_unknown_file_without_create(self):
        self.assertFalse(self.portal.save_yaml("nouveau.yaml", SIMPLE)["ok"])

    def test_create_new_and_refuse_existing(self):
        self.assertTrue(self.portal.save_yaml("nouveau.yaml", SIMPLE, create=True)["ok"])
        self.assertFalse(self.portal.save_yaml("nouveau.yaml", SIMPLE, create=True)["ok"])

    def test_save_adds_final_newline(self):
        self.put("demo.yaml", SIMPLE)
        self.portal.save_yaml("demo.yaml", SIMPLE.rstrip("\n"))
        self.assertTrue((self.wdir / "demo.yaml").read_text(encoding="utf-8").endswith("\n"))

    def test_normalize_inputs(self):
        self.put("demo.yaml", SIMPLE)
        got = self.portal.normalize_inputs("demo.yaml", {"pays": "Italie", "inconnu": "x", "n": 5})
        self.assertEqual(got, {"pays": "Italie", "n": "5"})
        self.assertEqual(self.portal.normalize_inputs("demo.yaml", None), {"pays": "France", "n": "3"})


def feed(rec, events):
    for ev in events:
        rec.on_event(ev)


def start_event(wf_steps):
    return {"type": "workflow_start", "name": "demo-simple", "total": len(wf_steps),
            "steps": [{"id": i, "type": t, "description": d} for i, t, d in wf_steps]}


class RecorderTests(Base):

    def setUp(self):
        super().setUp()
        self.put("demo.yaml", SIMPLE)
        self.t = [1000.0]
        self.clock = lambda: self.t[0]

    def test_successful_run_is_recorded(self):
        (self.root / "tmp").mkdir()
        (self.root / "tmp" / "out.txt").write_text("bonjour", encoding="utf-8")
        rec = self.portal.recorder("demo.yaml", {"pays": "France"}, clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "Écrit un fichier"), ("ok", "end", "")]),
                   {"type": "step_start", "step_id": "ecrire"},
                   {"type": "step_done", "step_id": "ecrire", "output": "écrit", "duration": 0.2},
                   {"type": "step_start", "step_id": "ok"},
                   {"type": "step_done", "step_id": "ok", "output": "fini"},
                   {"type": "workflow_done"}])
        self.t[0] = 1004.0
        r = rec.finish()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["duration"], 4.0)
        self.assertEqual([s["status"] for s in r["steps"]], ["ok", "ok"])
        self.assertEqual(r["steps"][0]["duration"], 0.2)
        self.assertEqual(r["files"], [{"path": "tmp/out.txt", "size": 7}])
        self.assertEqual(r["inputs"], {"pays": "France"})
        self.assertEqual(len(self.portal.history.load()), 1)

    def test_failed_step_gives_error_status(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "x"), ("ok", "end", "")]),
                   {"type": "step_start", "step_id": "ecrire"},
                   {"type": "step_error", "step_id": "ecrire", "output": "disque plein", "duration": 1.0},
                   {"type": "workflow_error", "message": "arrêt"}])
        r = rec.finish()
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["steps"][0]["status"], "error")
        self.assertEqual(r["steps"][1]["status"], "notrun")
        self.assertEqual(r["error"], "arrêt")
        self.assertEqual(r["files"], [])

    def test_continue_on_error_still_ok(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "x"), ("ok", "end", "")]),
                   {"type": "step_error", "step_id": "ecrire", "output": "boum"},
                   {"type": "step_done", "step_id": "ok", "output": "fini"},
                   {"type": "workflow_done"}])
        self.assertEqual(rec.finish()["status"], "ok")

    def test_rejected_approval(self):
        self.put("h.yaml", HUMAN)
        rec = self.portal.recorder("h.yaml", clock=self.clock)
        feed(rec, [start_event([("ask", "human_approval", "Valider"), ("fin", "end", "")]),
                   {"type": "step_done", "step_id": "ask", "output": "rejected"},
                   {"type": "step_done", "step_id": "fin", "output": "fini"},
                   {"type": "workflow_done"}])
        r = rec.finish()
        self.assertEqual(r["status"], "rejected")
        self.assertEqual(r["steps"][0]["status"], "rejected")

    def test_interrupted_when_no_final_event(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "x")]), {"type": "step_start", "step_id": "ecrire"}])
        r = rec.finish()
        self.assertEqual(r["status"], "interrupted")
        self.assertEqual(r["steps"][0]["status"], "notrun")

    def test_skip_and_warning(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "x"), ("ok", "end", "")]),
                   {"type": "workflow_warning", "message": "ligne 3 : attention"},
                   {"type": "step_skip", "step_id": "ecrire", "reason": "a == b"},
                   {"type": "workflow_done"}])
        r = rec.finish()
        self.assertEqual(r["steps"][0]["status"], "skipped")
        self.assertEqual(r["warnings"], ["ligne 3 : attention"])

    def test_finish_is_idempotent(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [start_event([("ok", "end", "")]), {"type": "workflow_done"}])
        a, b = rec.finish(), rec.finish()
        self.assertIs(a, b)
        self.assertEqual(len(self.portal.history.load()), 1)

    def test_forced_status(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        self.assertEqual(rec.finish(status="error", error="plantage")["error"], "plantage")

    def test_secrets_are_masked_in_outputs_and_inputs(self):
        with mock.patch.dict(os.environ, {"GOAL_API_KEY": "sk-1234567890abcdef"}):
            rec = self.portal.recorder("demo.yaml", {"api_token": "sk-1234567890abcdef", "x": "ok"}, clock=self.clock)
            feed(rec, [start_event([("ecrire", "file_write", "x")]),
                       {"type": "step_done", "step_id": "ecrire", "output": "Bearer sk-1234567890abcdef"}])
            r = rec.finish()
        self.assertNotIn("sk-1234567890abcdef", json.dumps(r))
        self.assertEqual(r["inputs"]["api_token"], "***")
        self.assertEqual(r["steps"][0]["output"], "Bearer ***")

    def test_long_output_is_clipped(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "x")]),
                   {"type": "step_done", "step_id": "ecrire", "output": "a" * 10000}])
        out = rec.finish()["steps"][0]["output"]
        self.assertLess(len(out), 2100)
        self.assertIn("[+", out)

    def test_unknown_events_and_steps_are_ignored(self):
        rec = self.portal.recorder("demo.yaml", clock=self.clock)
        feed(rec, [None, "x", {"type": "agent_chunk", "step_id": "zzz"}, {"type": "step_done", "step_id": "inconnu"},
                   start_event([("ok", "end", "")]), {"type": "workflow_done"}])
        self.assertEqual(rec.finish()["status"], "ok")

    def test_recorder_rejects_bad_filename(self):
        with self.assertRaises(PortalError):
            self.portal.recorder("../x.yaml")

    def test_template_paths_are_not_listed_as_files(self):
        self.put("t.yaml", SIMPLE.replace("tmp/out.txt", "tmp/{{pays}}.txt"))
        rec = self.portal.recorder("t.yaml", clock=self.clock)
        feed(rec, [start_event([("ecrire", "file_write", "x")]), {"type": "step_done", "step_id": "ecrire", "output": "ok"},
                   {"type": "workflow_done"}])
        self.assertEqual(rec.finish()["files"], [])


class HistoryTests(Base):

    def make(self, n, file="demo.yaml"):
        self.put(file, SIMPLE)
        for i in range(n):
            rec = self.portal.recorder(file, clock=lambda: 1000.0 + i)
            rec.on_event(start_event([("ok", "end", "")]))
            rec.on_event({"type": "workflow_done"})
            rec.finish()

    def test_history_is_bounded(self):
        p = Portal(self.wdir, self.root / "h.jsonl", base_dir=self.root, max_runs=5)
        self.put("demo.yaml", SIMPLE)
        for _ in range(9):
            rec = p.recorder("demo.yaml")
            rec.on_event({"type": "workflow_done"})
            rec.finish()
        self.assertEqual(len(p.history.load()), 5)

    def test_corrupt_lines_are_skipped(self):
        self.make(2)
        with open(self.root / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write("{pas du json\n\n")
        self.assertEqual(len(self.portal.history.load()), 2)

    def test_missing_history_file(self):
        self.assertEqual(self.portal.history.load(), [])

    def test_runs_newest_first_and_filtered(self):
        self.make(3)
        self.make(1, "autre.yaml")
        runs = self.portal.runs("demo.yaml")
        self.assertEqual(len(runs), 3)
        self.assertGreaterEqual(runs[0]["start_ts"], runs[-1]["start_ts"])
        self.assertEqual(len(self.portal.runs()), 4)
        self.assertEqual(len(self.portal.runs(limit=2)), 2)

    def test_runs_summary_has_no_outputs(self):
        self.make(1)
        self.assertNotIn("output", self.portal.runs()[0]["steps"][0])

    def test_get_run(self):
        self.make(1)
        rid = self.portal.runs()[0]["id"]
        self.assertEqual(self.portal.get_run(rid)["id"], rid)
        self.assertIsNone(self.portal.get_run("absent"))

    def test_card_shows_last_run_and_count(self):
        self.make(2)
        card = self.portal.list_cards()[0]
        self.assertEqual(card["runs"], 2)
        self.assertEqual(card["last_run"]["status"], "ok")


class RealWorkflowsTests(unittest.TestCase):
    """Les workflows livrés avec le dépôt doivent tous donner une carte lisible."""

    def test_all_shipped_workflows_make_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Portal(ROOT / "workflows", Path(tmp) / "runs.jsonl", base_dir=ROOT)
            cards = p.list_cards()
            self.assertGreater(len(cards), 5)
            for c in cards:
                self.assertEqual(c["error"], "", c["filename"])
                self.assertTrue(c["steps"], c["filename"])


if __name__ == "__main__":
    unittest.main()