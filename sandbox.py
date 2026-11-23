"""
sandbox.py — bac à sable des fichiers pour les workflows créés depuis le chat.

Les étapes `file_read` / `file_write` d'un workflow « sous bac à sable » ne peuvent toucher que deux dossiers
de l'application : `output/` (résultats) et `documents/` (fichiers de l'utilisateur). Le contrôle porte sur le
chemin RÉEL (après résolution des {{variables}}, des « .. » et des liens) : il est refait à l'exécution.

Limite assumée : seules les étapes file_read / file_write sont contrôlées. Un script `python` ou une commande
`shell` peut toujours accéder au disque ; le bac à sable protège contre l'erreur, pas contre la malveillance.
"""

from pathlib import Path

FILE_STEP_TYPES = ("file_read", "file_write")
FOLDERS = ("output", "documents")
HINT = "output/ et documents/"


def roots(base) -> list:
    """Dossiers autorisés, chemins absolus."""
    return [(Path(base) / name).resolve() for name in FOLDERS]


def violation(path, allowed) -> str | None:
    """Message d'erreur si `path` sort des dossiers autorisés, sinon None."""
    text = str(path or "").strip()
    if not text:
        return None                                   # chemin vide : le nœud signale déjà l'erreur
    try:
        real = Path(text).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return f"Accès refusé : chemin invalide « {text} »."
    for root in allowed:
        if real == root or root in real.parents:
            return None
    return f"Accès refusé : « {text} » est en dehors des dossiers autorisés ({HINT})."


def static_violation(path) -> str | None:
    """Contrôle sur le texte du YAML, avant exécution : seuls les chemins littéraux sont jugés (« {{ » : ignoré)."""
    text = str(path or "").strip().replace("\\", "/")
    if not text or "{{" in text:
        return None
    while text.startswith("./"):
        text = text[2:]
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if text.startswith("/") or (len(text) > 1 and text[1] == ":") or ".." in parts or not parts or parts[0] not in FOLDERS:
        return f"« {path} » est en dehors des dossiers autorisés ({HINT})"
    return None
