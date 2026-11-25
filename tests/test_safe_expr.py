"""Tests de la substitution sûre des {{variables}} dans transform / skip_if (issue #9)."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import safe_expr  # noqa: E402
from engine import WorkflowEngine, eval_expr, resolve_expr  # noqa: E402
from nodes.transform_node import transform  # noqa: E402


def run_transform(expression, ctx):
    return transform({"expression": resolve_expr(expression, ctx)})


class RobustesseDesLitteraux(unittest.TestCase):
    """Les valeurs insérées dans un littéral ne peuvent pas le casser."""

    TPL = "'{{proposition.output}}'.split('|')[0].replace('Titre :', '').strip()"

    def test_cas_de_l_issue_apostrophe(self):
        r = run_transform(self.TPL, {"proposition": {"output": "Titre : L'été | blabla"}})
        self.assertEqual((r["status"], r["value"]), ("ok", "L'été"))

    def test_retours_a_la_ligne_guillemets_backticks_et_antislash(self):
        for texte in ("ligne 1\nligne 2", 'dit "oui"', "`code` et ``` blocs", "chemin C:\\temp\\nouveau", "tab\there\r\nfin",
                      "x'''y", 'x"""y', "\x00\x1f contrôle"):
            with self.subTest(texte=texte):
                r = run_transform("'{{a.output}}'", {"a": {"output": texte}})
                self.assertEqual((r["status"], r["value"]), ("ok", texte))

    def test_guillemets_doubles_et_triples_ecrits_par_l_auteur(self):
        ctx = {"a": {"output": "l'été \"fou\"\nsuite"}}
        self.assertEqual(run_transform('"{{a.output}}"', ctx)["value"], ctx["a"]["output"])
        self.assertEqual(run_transform("'''{{a.output}}'''", ctx)["value"], ctx["a"]["output"])
        self.assertEqual(run_transform('"""{{a.output}}"""', ctx)["value"], ctx["a"]["output"])

    def test_plusieurs_variables_et_texte_autour(self):
        ctx = {"a": {"output": "it's"}, "b": {"output": 'say "hi"'}}
        self.assertEqual(run_transform("'A={{a.output}} B={{b.output}}'.upper()", ctx)["value"], "A=IT'S B=SAY \"HI\"")

    def test_sequence_d_echappement_ecrite_par_l_auteur_est_conservee(self):
        r = run_transform("'{{a.output}}\\n'", {"a": {"output": "x"}})      # le \n est dans le gabarit
        self.assertEqual(r["value"], "x\n")


class CompatibiliteDesWorkflowsExistants(unittest.TestCase):

    def test_exemples_de_la_documentation(self):
        self.assertEqual(run_transform("len('{{prev.output}}'.splitlines())", {"prev": {"output": "a\nb\nc"}})["value"], 3)
        self.assertEqual(run_transform("round(float('{{sales.revenue}}') * 1.2, 2)", {"sales": {"revenue": "100"}})["value"], 120.0)

    def test_nombre_sans_guillemets_reste_un_nombre(self):
        self.assertTrue(eval_expr("{{x.returncode}} == 0", {"x": {"returncode": 0}}))
        self.assertTrue(eval_expr("{{compter.output}} <= 5", {"compter": {"output": "3"}}))
        self.assertFalse(eval_expr("{{compter.output}} <= 5", {"compter": {"output": "12"}}))
        self.assertTrue(eval_expr("{{x.output}} == -2.5", {"x": {"output": "-2.5"}}))

    def test_texte_compare_entre_guillemets(self):
        self.assertTrue(eval_expr("'{{statut.output}}' == 'ROUGE'", {"statut": {"output": "ROUGE"}}))
        self.assertFalse(eval_expr("'{{statut.output}}' != 'ROUGE'", {"statut": {"output": "ROUGE"}}))

    def test_texte_sans_guillemets_devient_une_donnee_texte(self):
        self.assertTrue(eval_expr("{{statut.output}} == 'ROUGE'", {"statut": {"output": "ROUGE"}}))

    def test_valeur_python_non_texte_reste_une_valeur(self):
        r = run_transform("sum({{x.value}})", {"x": {"value": [1, 2, 3]}})
        self.assertEqual(r["value"], 6)
        r = run_transform("{{x.value}}['k']", {"x": {"value": {"k": "v'w"}}})
        self.assertEqual(r["value"], "v'w")

    def test_variable_integree_et_champ_absent(self):
        self.assertEqual(run_transform("'{{today}}'", {})["status"], "ok")
        self.assertEqual(run_transform("'{{absent.output}}'", {})["value"], "")


class Injection(unittest.TestCase):

    def test_sortie_forgee_est_inerte_dans_transform(self):
        for payload in ("'+str(1)+'", "' + str(__import__('os').getpid()) + '", "x') or ('1",
                        "'+str([c for c in ().__class__.__base__.__subclasses__()][0])+'"):
            with self.subTest(payload=payload):
                r = run_transform("'{{a.output}}'", {"a": {"output": payload}})
                self.assertEqual((r["status"], r["value"]), ("ok", payload))      # texte littéral, rien d'exécuté

    def test_skip_if_ne_peut_pas_etre_detourne(self):
        for payload in ("' or True or '", "x' == 'x' or 'a", "') or (1 == 1) or ('"):
            with self.subTest(payload=payload):
                self.assertFalse(eval_expr("'{{a.output}}' == 'ROUGE'", {"a": {"output": payload}}))

    def test_texte_forge_sans_guillemets_reste_une_donnee(self):
        self.assertFalse(eval_expr("{{a.output}} == 'ROUGE'", {"a": {"output": "'ROUGE' or True"}}))

    def test_la_sortie_forgee_n_est_pas_evaluee_meme_hors_litteral(self):
        r = run_transform("len({{a.output}})", {"a": {"output": "__import__('os').getpid()"}})
        self.assertEqual((r["status"], r["value"]), ("ok", len("__import__('os').getpid()")))

    def test_les_attributs_internes_ecrits_dans_l_expression_sont_refuses(self):
        for expression in ("().__class__", "''.__class__.__mro__", "[c for c in ().__class__.__base__.__subclasses__()]",
                           "str._x", "__import__('os')"):
            with self.subTest(expression=expression):
                r = transform({"expression": expression})
                self.assertEqual(r["status"], "error")
                self.assertIn("refusé", r["output"])

    def test_skip_if_refuse_aussi_les_attributs_internes(self):
        self.assertFalse(eval_expr("().__class__ is tuple", {}))


class DeBoutEnBout(unittest.TestCase):
    """Le moteur évalue la porte et l'expression avec la substitution sûre."""

    def _run(self, yaml_text):
        events = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "wf.yaml"
            path.write_text(yaml_text, encoding="utf-8")
            WorkflowEngine(str(path), event_callback=events.append).run()
        return events

    def test_transform_apres_une_etape_dont_la_sortie_contient_des_apostrophes(self):
        events = self._run(
            "name: t\nsteps:\n"
            "- id: texte\n  type: python\n  code: |\n    print(\"Titre : L'été | autre\")\n"
            "- id: titre\n  type: transform\n"
            "  expression: \"'{{texte.output}}'.split('|')[0].replace('Titre :', '').strip()\"\n"
            "- id: fin\n  type: end\n  message: \"{{titre.output}}\"\n")
        done = [e for e in events if e["type"] == "step_done" and e["step_id"] == "titre"][0]
        self.assertEqual(done["output"], "L'été")
        self.assertEqual([e["type"] for e in events if e["type"] in ("workflow_done", "workflow_error")], ["workflow_done"])


if __name__ == "__main__":
    unittest.main()
