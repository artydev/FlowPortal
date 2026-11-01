"""tests/mcp_server.py — ré-export du serveur MCP racine.

Historiquement ce fichier était une copie à l'identique de mcp_server.py (racine).
Pour éviter que les deux copies dérivent, il n'expose plus qu'un ré-export :
l'implémentation unique vit à la racine (mcp_server.py), utilisée par app.py,
les builds Nuitka et tests/mcp_smoke.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server import serve  # noqa: E402, F401  — API identique (ré-export)
