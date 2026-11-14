"""
friendly.py — vocabulaire « langage courant » pour les non-techniciens.

Un seul endroit pour : l'icône et le verbe de chaque type d'étape, le titre lisible d'un workflow,
et la traduction des messages de validation (schema.py, portal.py) en phrases compréhensibles.
Utilisé par assist.py (réponse structurée du chat) et portal.py (cartes).
"""

import re

STEP_KINDS = {
    "agent": ("🤖", "Réfléchit (IA)"), "file_write": ("💾", "Enregistre un fichier"), "file_read": ("📖", "Lit un fichier"),
    "python": ("🧮", "Calcule"), "shell": ("⚙️", "Lance un programme"), "http_get": ("🌐", "Récupère une page web"),
    "http_post": ("📤", "Envoie des données"), "http_put": ("📤", "Envoie des données"),
    "http_delete": ("🗑️", "Supprime à distance"), "human_approval": ("👤", "Vous demande de valider"),
    "user_input": ("✍️", "Vous pose une question"), "mcp_call": ("🔌", "Utilise un outil"),
    "mcp_tools": ("🔌", "Utilise un outil"), "log": ("📄", "Note un message"), "end": ("✅", "Fin"),
    "transform": ("🔄", "Met en forme"),
}


def step_kind(step_type: str) -> dict:
    """{"icon", "kind"} d'un type d'étape ; un type inconnu reste lisible."""
    icon, kind = STEP_KINDS.get(step_type, ("▫️", "Étape"))
    return {"icon": icon, "kind": kind}


def friendly_title(name: str) -> str:
    """« sales-report_pipeline » → « Sales report pipeline »."""
    text = re.sub(r"[-_]+", " ", str(name or "")).strip()
    return text[:1].upper() + text[1:]


_PARAMS = {"path": "le fichier", "prompt": "la consigne", "url": "l'adresse", "content": "le contenu",
           "command": "la commande", "message": "le message", "code": "le code", "tool": "l'outil", "server": "le serveur"}

# (motif du message technique, phrase ; {param} est remplacé) — la phrase est préfixée par « titre : ».
# Une phrase vide signifie « ne pas montrer » (avertissement sans intérêt pour un non-technicien).
_RULES = [
    (r"pas d'« id »", ""),
    (r"paramètre obligatoire « ?(\w+) ?» manquant", "il manque {param} pour que cette étape fonctionne."),
    (r"id en double", "cette étape porte le même nom interne qu'une autre : l'assistant doit en renommer une."),
    (r"« type » manquant|type « .* inconnu", "cette étape utilise une action qui n'existe pas."),
    (r"clé « .* inconnue", "un réglage de cette étape n'est pas reconnu, il sera ignoré."),
    (r"ne désigne aucune étape", "cette étape renvoie vers une étape qui n'existe pas."),
    (r"on_\w+.* absent|absent, le workflow s'arrêtera", "dites ce qui se passe ensuite (si vous acceptez ou refusez), sinon le déroulement s'arrête ici."),
    (r"ne correspond à aucune étape ni entrée", "cette étape utilise un résultat qui n'existe pas, elle recevra du vide."),
    (r"à la racine", "cette étape enregistre un fichier au mauvais endroit : il doit aller dans le dossier des résultats."),
]


def friendly_problem(message: str, title: str = "") -> str | None:
    """Phrase lisible pour un message de validation ; None s'il n'y a rien à montrer ; repli générique sinon."""
    message = str(message or "")
    prefix = f"« {title} » : " if title else ""
    for pattern, sentence in _RULES:
        match = re.search(pattern, message)
        if not match:
            continue
        if not sentence:
            return None
        param = _PARAMS.get(match.group(1), "une information") if match.groups() else ""
        return prefix + sentence.format(param=param)
    if message.startswith("YAML invalide"):
        return "Le fichier est mal écrit. Demandez à l'assistant de le corriger."
    if "« steps »" in message:
        return "Le workflow n'a aucune étape."
    return prefix + "cette étape est à revoir. Demandez à l'assistant de la corriger."
