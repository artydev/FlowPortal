"""
nodes/mcp_node.py — appelle un serveur MCP directement (sans CSAgent).

Deux étapes :
    mcp_tools : liste les outils d'un serveur (pour savoir quoi appeler) ;
    mcp_call  : appelle un outil avec ses arguments.

Le serveur est désigné par son NOM dans un fichier de configuration (mcp.json par défaut, le même
format que CSAgent : clé « mcpServers », ${VARIABLE} pour les secrets). Un workflow ne peut donc pas
lancer une commande ni viser une adresse de son choix. Le résultat d'un outil est une DONNÉE :
écrit dans un fichier (save_as), il se relit par un agent sans être collé dans son instruction.

Step YAML:
    - id: lister
      type: mcp_tools
      server: fichiers

    - id: lire
      type: mcp_call
      server: fichiers
      tool: read_text_file
      arguments:
        path: "{{saisie.path}}"
      save_as: tmp/mcp/lecture.txt        # facultatif
"""

import json
import os

import mcp_client
from . import register

_DEFAULT_MAX = 50000


def _int(value, default, low, high):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _fail(message: str) -> dict:
    return {"output": message, "status": "error"}


@register("mcp_tools", summary="Liste les outils d'un serveur MCP déclaré dans mcp.json (sans CSAgent) ; sortie : un outil par ligne.", params={
    "server":  ("nom du serveur dans le fichier de configuration (clé mcpServers)", True),
    "config":  ("fichier de configuration MCP (défaut mcp.json)", False),
    "timeout": ("secondes d'attente par requête (défaut 60)", False),
})
def mcp_tools(params: dict) -> dict:
    """
    Output fields:
        output: « nom(arguments) — description », un outil par ligne
        tools:  les noms séparés par des virgules
        count:  nombre d'outils
    """
    server = str(params.get("server", "")).strip()
    if not server:
        return _fail("Paramètre « server » manquant.")
    config = str(params.get("config") or mcp_client.DEFAULT_CONFIG)
    timeout = _int(params.get("timeout"), mcp_client.DEFAULT_TIMEOUT, 1, 3600)
    try:
        tools = mcp_client.session(server, config).list_tools(timeout)
    except mcp_client.McpError as exc:
        return _fail(str(exc))
    return {"output": mcp_client.format_tools(tools),
            "tools": ",".join(str(t.get("name")) for t in tools),
            "count": len(tools), "status": "ok"}


@register("mcp_call", summary="Appelle un outil d'un serveur MCP déclaré dans mcp.json (sans CSAgent) ; sortie : le texte rendu par l'outil.", params={
    "server":    ("nom du serveur dans le fichier de configuration (clé mcpServers)", True),
    "tool":      ("nom de l'outil à appeler", True),
    "arguments": ("arguments de l'outil : un objet YAML (les {{...}} sont remplacés), ou un texte JSON", False),
    "config":    ("fichier de configuration MCP (défaut mcp.json)", False),
    "timeout":   ("secondes d'attente de la réponse (défaut 60)", False),
    "save_as":   ("fichier où écrire le résultat complet (chemin relatif)", False),
    "max_chars": ("longueur maximale de la sortie (défaut 50000) ; save_as garde tout", False),
})
def mcp_call(params: dict) -> dict:
    """
    Output fields:
        output:     le texte rendu par l'outil (tronqué à max_chars)
        structured: le résultat structuré, en JSON, s'il y en a un
        path:       le fichier écrit (si save_as)
    Un outil qui signale une erreur (isError) donne status: error.
    """
    server = str(params.get("server", "")).strip()
    tool = str(params.get("tool", "")).strip()
    if not server or not tool:
        return _fail("Paramètres « server » et « tool » obligatoires.")
    config = str(params.get("config") or mcp_client.DEFAULT_CONFIG)
    timeout = _int(params.get("timeout"), mcp_client.DEFAULT_TIMEOUT, 1, 3600)
    limit = _int(params.get("max_chars"), _DEFAULT_MAX, 100, 10_000_000)

    arguments = params.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError as exc:
            return _fail(f"« arguments » n'est pas un JSON valide : {exc}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _fail("« arguments » doit être un objet (clé: valeur).")

    try:
        result = mcp_client.session(server, config).call_tool(tool, arguments, timeout)
    except mcp_client.McpError as exc:
        return _fail(str(exc))

    text, structured, is_error = mcp_client.render_result(result)
    out = {"output": text if len(text) <= limit else text[:limit] + f"\n… (tronqué : {len(text)} caractères)",
           "status": "error" if is_error else "ok"}
    if structured is not None:
        out["structured"] = json.dumps(structured, ensure_ascii=False)

    path = str(params.get("save_as") or "").strip()
    if path:
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n")
            out["path"] = path
        except OSError as exc:
            return _fail(f"Écriture impossible ({path}) : {exc}")
    return out
