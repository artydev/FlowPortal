"""Garde-fou : un identifiant HTML ne doit pas apparaître deux fois dans ui/portal.html.

Les boutons sont retrouvés par `$("#id")` (premier élément correspondant) : un doublon fait réaffecter le gestionnaire
d'un autre bouton (cause d'un bouton « Vue liste » inactif après l'ouverture de l'éditeur YAML du panneau)."""

import collections
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "ui" / "portal.html"


class UiIdsTests(unittest.TestCase):

    def test_ids_are_unique(self):
        text = PAGE.read_text(encoding="utf-8")
        ids = re.findall(r"""\bid=\\?["']([A-Za-z0-9_-]+)\\?["']""", text)
        dups = {k: v for k, v in collections.Counter(ids).items() if v > 1}
        self.assertEqual(dups, {})


if __name__ == "__main__":
    unittest.main()
