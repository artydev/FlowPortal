"""Bibliothèque de workflows prêts à l'emploi (library/)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import portal
import sandbox
import schema


def library_files():
    return sorted((ROOT / "library").glob("*.yaml"))


class LibraryFilesTests(unittest.TestCase):
    def test_il_y_a_des_modeles(self):
        self.assertGreaterEqual(len(library_files()), 6)

    def test_chaque_modele_est_valide(self):
        for path in library_files():
            with self.subTest(path.name):
                issues = schema.check(path.read_text(encoding="utf-8"))
                self.assertEqual([i for i in issues if i[0] == "error"], [])

    def test_chaque_modele_a_une_categorie_et_une_description(self):
        import yaml
        for path in library_files():
            with self.subTest(path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertTrue(str(data.get("category", "")).strip())
                self.assertTrue(str(data.get("description", "")).strip())

    def test_chaque_modele_respecte_le_bac_a_sable(self):
        """Aucun chemin littéral de lecture/écriture hors de output/ et documents/ (les copies sont sous bac à sable)."""
        import yaml
        for path in library_files():
            with self.subTest(path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                for step in data["steps"]:
                    if step.get("type") in sandbox.FILE_STEP_TYPES:
                        self.assertIsNone(sandbox.static_violation(step.get("path")), step.get("id"))

    def test_les_etapes_utilisateur_ecrivent_dans_output(self):
        """user_input sans save_as écrirait dans tmp/saisie/ : interdit sous bac à sable."""
        import yaml
        for path in library_files():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for step in data["steps"]:
                if step.get("type") == "user_input":
                    with self.subTest(f"{path.name}:{step['id']}"):
                        self.assertTrue(str(step.get("save_as", "")).startswith("output/"))

    def test_les_agents_ne_creent_pas_de_fichier(self):
        import yaml
        for path in library_files():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for step in data["steps"]:
                if step.get("type") == "agent":
                    with self.subTest(f"{path.name}:{step['id']}"):
                        self.assertIn("Ne crée aucun fichier", step["prompt"])


class WebTemplatesTests(unittest.TestCase):
    """Les modèles web n'exigent aucune installation : pas d'outil MCP, simple téléchargement."""

    def test_les_modeles_web_n_utilisent_pas_de_serveur_mcp(self):
        import yaml
        for name in ("lire-une-page-web", "surveiller-une-page-web"):
            data = yaml.safe_load((ROOT / "library" / f"{name}.yaml").read_text(encoding="utf-8"))
            types = {s.get("type") for s in data["steps"]}
            with self.subTest(name):
                self.assertIn("http_get", types)
                self.assertNotIn("mcp_call", types)

    def test_page_sans_texte_est_signalee_et_la_memoire_est_preservee(self):
        """Le script d'extraction : texte lu hors scripts/styles ; page trop courte -> PAGE_VIDE sans écrire la mémoire."""
        import yaml
        data = yaml.safe_load((ROOT / "library" / "surveiller-une-page-web.yaml").read_text(encoding="utf-8"))
        code = next(s["code"] for s in data["steps"] if s["id"] == "extraire")
        for page, attendu in (("<html><script>var a=1</script><body><div id=r></div></body></html>", "PAGE_VIDE"),
                              ("<html><head><title>Tarifs</title><style>x{}</style></head><body><p>" + "Abonnement à douze euros. " * 12 + "</p></body></html>", "Abonnement")):
            with self.subTest(attendu), tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp)
                (work / "output" / "r1").mkdir(parents=True)
                (work / "output" / "r1" / "page.html").write_text(page, encoding="utf-8")
                script = code.replace("{{run_id}}", "r1").replace("{{cle.output}}", "cle")
                import subprocess
                out = subprocess.run([sys.executable, "-c", script], cwd=work, capture_output=True, text=True, timeout=30)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertIn(attendu, out.stdout)
                self.assertNotIn("var a", out.stdout)
                self.assertEqual((work / "output" / "surveillance" / "cle.txt").exists(), attendu != "PAGE_VIDE")


class PortalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "workflows").mkdir()
        (self.tmp / "library").mkdir()
        for path in library_files():
            (self.tmp / "library" / path.name).write_bytes(path.read_bytes())
        (self.tmp / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        self.portal = portal.Portal(self.tmp / "workflows", self.tmp / "runs.jsonl", mcp_config="mcp.json", base_dir=self.tmp)

    def test_liste_de_la_bibliotheque(self):
        items = {i["id"]: i for i in self.portal.library()}
        self.assertEqual(len(items), len(library_files()))
        item = items["resumer-un-document"]
        self.assertEqual(item["title"], "Résumer un document")
        self.assertEqual(item["category"], "Documents")
        self.assertEqual(item["icon"], "📄")
        self.assertEqual(item["needs_setup"], [])

    def test_un_modele_avec_outil_mcp_absent_est_signale(self):
        (self.tmp / "library" / "outil.yaml").write_text(
            "name: Avec outil\ncategory: Test\ndescription: x\nsteps:\n"
            "  - id: a\n    type: mcp_call\n    server: demo\n    tool: ping\n  - id: f\n    type: end\n", encoding="utf-8")
        items = {i["id"]: i for i in self.portal.library()}
        self.assertEqual(items["outil"]["needs_setup"], ["demo"])
        self.assertEqual(items["resumer-un-document"]["needs_setup"], [])
        (self.tmp / "mcp.json").write_text('{"mcpServers": {"demo": {"command": "x"}}}', encoding="utf-8")
        self.assertEqual({i["id"]: i for i in self.portal.library()}["outil"]["needs_setup"], [])

    def test_aucun_modele_ne_demande_d_installation(self):
        self.assertTrue(all(i["needs_setup"] == [] for i in self.portal.library()))

    def test_dossier_absent(self):
        for p in (self.tmp / "library").glob("*"):
            p.unlink()
        (self.tmp / "library").rmdir()
        self.assertEqual(self.portal.library(), [])

    def test_utiliser_un_modele_cree_un_brouillon_sous_bac_a_sable(self):
        res = self.portal.use_library("resumer-un-document")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["filename"], "resumer-un-document.yaml")
        self.assertTrue((self.tmp / "workflows" / "resumer-un-document.yaml").is_file())
        self.assertEqual(res["card"]["status"], "draft")
        self.assertTrue(res["card"]["sandboxed"])
        self.assertEqual(res["card"]["category"], "Documents")

    def test_le_modele_d_origine_reste_intact(self):
        before = (self.tmp / "library" / "resumer-un-document.yaml").read_bytes()
        self.portal.use_library("resumer-un-document")
        self.assertEqual((self.tmp / "library" / "resumer-un-document.yaml").read_bytes(), before)

    def test_deuxieme_copie_prend_un_nom_distinct(self):
        self.portal.use_library("resumer-un-document")
        res = self.portal.use_library("resumer-un-document")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["filename"], "resumer-un-document-2.yaml")
        self.assertEqual(res["card"]["title"], "Résumer un document 2")
        self.assertEqual(self.portal.use_library("resumer-un-document")["filename"], "resumer-un-document-3.yaml")

    def test_modele_inconnu_ou_nom_dangereux(self):
        for bad in ("nexiste-pas", "../app", "..\\app", ""):
            with self.subTest(bad):
                res = self.portal.use_library(bad)
                self.assertFalse(res["ok"])
        self.assertEqual(list((self.tmp / "workflows").glob("*.yaml")), [])


if __name__ == "__main__":
    unittest.main()
