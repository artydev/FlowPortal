"""Tests unitaires du moteur : résolution de templates et évaluation de skip_if.

Lancement (depuis la racine) :
    py -m unittest discover -s tests -p "test_*.py" -v
"""

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import resolve, resolve_all, eval_expr  # noqa: E402


class ResolveTests(unittest.TestCase):
    """engine.resolve : substitution de {{...}}, champs, inputs, builtins."""

    def test_default_field_is_output(self):
        ctx = {"a": {"output": "x"}}
        self.assertEqual(resolve("{{a}}", ctx), "x")

    def test_explicit_field(self):
        ctx = {"a": {"output": "x", "returncode": 3}}
        self.assertEqual(resolve("{{a.returncode}}", ctx), "3")

    def test_missing_step_ref_is_empty_string(self):
        self.assertEqual(resolve("{{inexistant.output}}", {}), "")

    def test_missing_field_is_empty_string(self):
        ctx = {"a": {"output": "x"}}
        self.assertEqual(resolve("{{a.stderr}}", ctx), "")

    def test_mixed_text(self):
        ctx = {"a": {"output": "12"}}
        self.assertEqual(resolve("rc={{a.output}} ok", ctx), "rc=12 ok")

    def test_inputs_seeded_like_engine(self):
        # engine.WorkflowEngine pré-remplit ctx[k] = {"output": str(v), "value": v}
        ctx = {"date_reference": {"output": "2026-10-16", "value": "2026-10-16"}}
        self.assertEqual(resolve("{{date_reference}}", ctx), "2026-10-16")

    def test_builtin_env(self):
        os.environ["CSFLOW_TEST_VAR"] = "valeur-de-test"
        self.assertEqual(resolve("{{env.CSFLOW_TEST_VAR}}", {}), "valeur-de-test")

    def test_builtin_now(self):
        self.assertRegex(resolve("{{now}}", {}), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_no_template_is_unchanged(self):
        self.assertEqual(resolve("pas de variable", {}), "pas de variable")

    def test_resolve_all_recursive(self):
        ctx = {"n": {"output": "5"}, "s": {"output": "ok"}}
        self.assertEqual(
            resolve_all({"a": "{{n.output}}", "b": ["{{s.output}}", 3]}, ctx),
            {"a": "5", "b": ["ok", 3]},
        )


class EvalExprTests(unittest.TestCase):
    """engine.eval_expr : la sémantique exacte de skip_if."""

    def test_rouge_does_not_skip_escalade(self):
        # copil_projet.yaml : statut ROUGE → escalade ne doit PAS être sautée
        ctx = {"statut": {"output": "ROUGE"}}
        self.assertFalse(eval_expr("'{{statut.output}}' != 'ROUGE'", ctx))

    def test_vert_skips_escalade(self):
        ctx = {"statut": {"output": "VERT"}}
        self.assertTrue(eval_expr("'{{statut.output}}' != 'ROUGE'", ctx))

    def test_returncode_failure_does_not_skip_fix(self):
        # demo_autofix / dev_fix_tests : tests qui échouent → correction
        ctx = {"first_run": {"returncode": 1}}
        self.assertFalse(eval_expr("{{first_run.returncode}} == 0", ctx))

    def test_returncode_success_skips_fix(self):
        ctx = {"first_run": {"returncode": 0}}
        self.assertTrue(eval_expr("{{first_run.returncode}} == 0", ctx))

    def test_missing_ref_evaluates_safely(self):
        # ref manquante → "" → l'expression reste évaluable, sans crash
        self.assertTrue(eval_expr("'{{x.output}}' != 'ROUGE'", {}))
        self.assertFalse(eval_expr("{{x.output}} == 0", {}))

    def test_bad_syntax_returns_false(self):
        self.assertFalse(eval_expr("pas du python {{", {}))

    def test_builtins_are_stripped(self):
        # '__builtins__': {} → aucun import/système ne doit s'exécuter
        self.assertFalse(eval_expr("__import__('os').system('exit 0')", {}))


if __name__ == "__main__":
    unittest.main()
