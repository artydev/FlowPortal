"""Garde-fou : un identifiant HTML ne doit pas apparaître deux fois dans ui/portal.html ni dans les scripts ui/*.js
(les gabarits JS produisent aussi des `id="…"`).

Les boutons sont retrouvés par `$("#id")` (premier élément correspondant) : un doublon fait réaffecter le gestionnaire
d'un autre bouton (cause d'un bouton « Vue liste » inactif après l'ouverture de l'éditeur YAML du panneau)."""

import collections
import re
import unittest
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
PAGE = UI_DIR / "portal.html"


class UiIdsTests(unittest.TestCase):

    def test_ids_are_unique(self):
        sources = [PAGE, *sorted(UI_DIR.glob("*.js"))]
        text = "\n".join(path.read_text(encoding="utf-8") for path in sources)
        ids = re.findall(r"""\bid=\\?["']([A-Za-z0-9_-]+)\\?["']""", text)
        dups = {k: v for k, v in collections.Counter(ids).items() if v > 1}
        self.assertEqual(dups, {})


if __name__ == "__main__":
    unittest.main()
