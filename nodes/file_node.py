"""nodes/file_node.py — read and write local files."""

from pathlib import Path
from . import register


@register("file_read", summary="Lit un fichier texte ; sortie : output (le contenu).", params={
    "path": ("chemin du fichier à lire (relatif au dossier workflow)", True),
})
def file_read(params: dict) -> dict:
    """
    Read a text file from disk.

    Params:
        path (str): file path (relative to CWD or absolute)

    Output fields:
        output / content: file contents as a string
    """
    path = params.get("path", "")
    try:
        content = Path(path).read_text(encoding="utf-8")
        return {"output": content, "content": content, "status": "ok"}
    except FileNotFoundError:
        return {"output": f"File not found: {path}", "status": "error"}
    except Exception as exc:
        return {"output": str(exc), "status": "error"}


@register("file_write", summary="Écrit (ou remplace) un fichier texte ; les dossiers manquants sont créés.", params={
    "path":    ("fichier à écrire (les dossiers sont créés)", True),
    "content": ("texte à écrire", True),
})
def file_write(params: dict) -> dict:
    """
    Write (or overwrite) a text file. Parent directories are created automatically.

    Params:
        path    (str): destination file path
        content (str): text to write

    Output fields:
        output: confirmation message
        path:   the path that was written
    """
    path    = params.get("path", "")
    content = params.get("content", "")
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {
            "output": f"Written {len(content)} chars → {path}",
            "path":   path,
            "status": "ok",
        }
    except Exception as exc:
        return {"output": str(exc), "status": "error"}
