"""Tests de jsonlogic.py et de skip_if_logic dans le moteur."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jsonlogic  # noqa: E402
from engine import eval_logic  # noqa: E402


class JsonLogicTests(unittest.TestCase):
    def test_pypi_examples(self):
        self.assertTrue(jsonlogic.apply({"==": [1, 1]}))
        self.assertTrue(jsonlogic.apply({"and": [{">": [3, 1]}, {"<": [1, 3]}]}))
        self.assertEqual(jsonlogic.apply({"var": "a"}, {"a": 1}), 1)
        self.assertEqual(jsonlogic.apply({"var": 1}, ["apple", "banana"]), "banana")
        rule = {"and": [{"<": [{"var": "temp"}, 110]},
                        {"==": [{"var": "pie.filling"}, "apple"]}]}
        self.assertTrue(jsonlogic.apply(rule, {"temp": 100, "pie": {"filling": "apple"}}))

    def test_var_default_and_missing(self):
        self.assertEqual(jsonlogic.apply({"var": ["x", 7]}, {}), 7)
        self.assertIsNone(jsonlogic.apply({"var": "a.b.c"}, {"a": {}}))

    def test_strict_equality_checks_type(self):
        self.assertFalse(jsonlogic.apply({"===": [1, "1"]}))
        self.assertTrue(jsonlogic.apply({"===": ["a", "a"]}))
        self.assertTrue(jsonlogic.apply({"!==": [1, "1"]}))

    def test_between_and_arithmetic(self):
        self.assertTrue(jsonlogic.apply({"<": [1, 2, 3]}))
        self.assertFalse(jsonlogic.apply({"<": [1, 5, 3]}))
        self.assertEqual(jsonlogic.apply({"+": [1, 2, 3]}), 6.0)
        self.assertEqual(jsonlogic.apply({"cat": ["a", "b"]}), "ab")
        self.assertTrue(jsonlogic.apply({"in": ["a", "abc"]}))

    def test_if_and_short_circuit(self):
        self.assertEqual(jsonlogic.apply({"if": [False, "a", True, "b", "c"]}), "b")
        # le 2e opérande n'est pas évalué (opérateur inconnu non atteint)
        self.assertFalse(jsonlogic.apply({"and": [False, {"nope": 1}]}))

    def test_unknown_operator_raises(self):
        with self.assertRaises(ValueError):
            jsonlogic.apply({"nope": [1]})


class SkipIfLogicTests(unittest.TestCase):
    def test_rouge_does_not_skip(self):
        rule = {"!=": [{"var": "statut.output"}, "ROUGE"]}
        self.assertFalse(eval_logic(rule, {"statut": {"output": "ROUGE"}}))

    def test_vert_skips(self):
        rule = {"!=": [{"var": "statut.output"}, "ROUGE"]}
        self.assertTrue(eval_logic(rule, {"statut": {"output": "VERT"}}))

    def test_quote_in_output_is_harmless(self):
        rule = {"==": [{"var": "a.output"}, "x"]}
        self.assertFalse(eval_logic(rule, {"a": {"output": "it's \"x\""}}))

    def test_error_is_false(self):
        self.assertFalse(eval_logic({"nope": 1}, {}))



# ── routage next_if ───────────────────────────────────────────────────────────
import tempfile  # noqa: E402
import schema    # noqa: E402
from engine import WorkflowEngine  # noqa: E402


def _run(yaml_text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "wf.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        events = []
        WorkflowEngine(str(p), event_callback=events.append).run()
        return [e["step_id"] for e in events if e["type"] == "step_start"], events


_ROUTE = """
steps:
  - id: s
    type: transform
    expression: "'%s'"
    next_if:
      - when: {"==": [{"var": "s.output"}, "ROUGE"]}
        goto: rouge
      - when: {"==": [{"var": "s.output"}, "ORANGE"]}
        goto: orange
  - id: orange
    type: log
    message: o
    next: fin
  - id: rouge
    type: log
    message: r
    next: fin
  - id: fin
    type: end
"""


class NextIfTests(unittest.TestCase):
    def test_first_true_branch_wins(self):
        self.assertEqual(_run(_ROUTE % "ROUGE")[0], ["s", "rouge", "fin"])
        self.assertEqual(_run(_ROUTE % "ORANGE")[0], ["s", "orange", "fin"])

    def test_no_match_falls_back_to_list_order(self):
        self.assertEqual(_run(_ROUTE % "VERT")[0], ["s", "orange", "fin"])

    def test_default_branch_without_when(self):
        y = _ROUTE.replace("    next: fin\n  - id: rouge", "    next: fin\n  - id: rouge", 1)
        y = y.replace('        goto: orange\n', '        goto: orange\n      - goto: fin\n', 1)
        self.assertEqual(_run(y % "VERT")[0], ["s", "fin"])

    def test_loop_guard(self):
        y = """
steps:
  - id: a
    type: log
    message: x
    next_if:
      - goto: a
"""
        ids, events = _run(y)
        self.assertEqual(events[-1]["type"], "workflow_error")
        self.assertLessEqual(len(ids), 500)

    def test_schema_validates_next_if(self):
        bad = """
steps:
  - id: a
    type: log
    message: x
    next_if:
      - when: {"==": [1, 1]}
        goto: inconnu
      - when: {"==": [1, 1]}
"""
        msgs = [m for lvl, _, m in schema.check(bad) if lvl == "error"]
        self.assertTrue(any("inconnu" in m for m in msgs))
        self.assertTrue(any("attendu" in m for m in msgs))
        self.assertEqual([i for i in schema.check(_ROUTE % "x") if i[0] == "error"], [])


if __name__ == "__main__":
    unittest.main()
