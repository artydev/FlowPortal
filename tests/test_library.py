"""Bibliothèque de workflows prêts à l'emploi (library/) : le contenu appartient à l'utilisateur, les tests vérifient le mécanisme."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import portal
import sandbox
import schema
import yaml

MODELE = (
    "name: Résumer un document\n"
    "category: Documents\n"
    "description: Résume un fichier choisi.\n"
    "steps:\n"
    "  - id: lire\n    type: file_read\n    path: documents/note.txt\n"
    "  - id: fin\n    type: end\n"
)


def library_files():
    return sorted((ROOT / "library").glob("*.y*ml"))


class LibraryFilesTests(unittest.TestCase):
    """Les fichiers réellement présents dans library/ (quels qu'ils soient) doivent être des workflows valides."""

    def test_chaque_fichier_est_un_workflow_valide(self):
        for path in library_files():
            with self.subTest(path.name):
                issues = schema.check(path.read_text(encoding="utf-8"))
                self.assertEqual([i for i in issues if i[0] == "error"], [])


    def test_chaque_fichier_respecte_le_bac_a_sable(self):
        """Les copies sont des brouillons sous bac à sable : file_read/file_write doivent viser output/ ou documents/."""
        for path in library_files():
            with self.subTest(path.name):
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                for step in data["steps"]:
                    if step.get("type") in sandbox.FILE_STEP_TYPES:
                        self.assertIsNone(sandbox.static_violation(step.get("path")), step.get("id"))


class PortalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "workflows").mkdir()
        (self.tmp / "library").mkdir()
        (self.tmp / "library" / "resumer-un-document.yaml").write_text(MODELE, encoding="utf-8")
        (self.tmp / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        self.portal = portal.Portal(self.tmp / "workflows", self.tmp / "runs.jsonl", mcp_config="mcp.json", base_dir=self.tmp)

    def test_liste_de_la_bibliotheque(self):
        items = {i["id"]: i for i in self.portal.library()}
        self.assertEqual(list(items), ["resumer-un-document"])
        item = items["resumer-un-document"]
        self.assertEqual(item["title"], "Résumer un document")
        self.assertEqual(item["category"], "Documents")
        self.assertEqual(item["icon"], "📄")
        self.assertEqual(item["needs_setup"], [])

    def test_un_modele_sans_categorie_est_accepte(self):
        (self.tmp / "library" / "libre.yaml").write_text(
            "name: Sans catégorie\nsteps:\n  - id: a\n    type: log\n    message: x\n  - id: f\n    type: end\n", encoding="utf-8")
        items = {i["id"]: i for i in self.portal.library()}
        self.assertIn("libre", items)
        self.assertEqual(items["libre"]["title"], "Sans catégorie")

    def test_un_fichier_invalide_est_ignore(self):
        (self.tmp / "library" / "casse.yaml").write_text("name: [pas un workflow\n", encoding="utf-8")
        self.assertEqual([i["id"] for i in self.portal.library()], ["resumer-un-document"])

    def test_un_modele_avec_outil_mcp_absent_est_signale(self):
        (self.tmp / "library" / "outil.yaml").write_text(
            "name: Avec outil\ncategory: Test\ndescription: x\nsteps:\n"
            "  - id: a\n    type: mcp_call\n    server: demo\n    tool: ping\n  - id: f\n    type: end\n", encoding="utf-8")
        items = {i["id"]: i for i in self.portal.library()}
        self.assertEqual(items["outil"]["needs_setup"], ["demo"])
        self.assertEqual(items["resumer-un-document"]["needs_setup"], [])
        (self.tmp / "mcp.json").write_text('{"mcpServers": {"demo": {"command": "x"}}}', encoding="utf-8")
        self.assertEqual({i["id"]: i for i in self.portal.library()}["outil"]["needs_setup"], [])

    def test_dossier_absent(self):
        for p in (self.tmp / "library").glob("*"):
            p.unlink()
        (self.tmp / "library").rmdir()
        self.assertEqual(self.portal.library(), [])

    def test_dossier_vide(self):
        (self.tmp / "library" / "resumer-un-document.yaml").unlink()
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
