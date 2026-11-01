"""
nodes/user_input_node.py — demande un texte à l'humain.

Interface graphique : un panneau avec un champ de texte (modifiable) s'affiche et le workflow
attend la validation. Avec voice: true, un bouton micro enregistre la voix dans le navigateur ;
l'audio est transcrit par CSAgent (Whisper), le texte arrive dans le champ et se corrige avant
validation. Terminal : une ligne est lue au clavier. Le texte est toujours écrit dans un
fichier (save_as) : un agent doit LIRE ce fichier comme une donnée plutôt que recevoir le texte
collé dans son instruction.

Step YAML:
    - id: notes
      type: user_input
      prompt: "Décrivez ce que vous avez constaté"
      voice: true                           # bouton micro (CSAgent --api avec les points d'accès audio)
      save_as: tmp/notes/constat.txt        # facultatif (défaut : tmp/saisie/<id>.txt)
"""

import os

from . import register

_YLW = "\033[93m"; _RST = "\033[0m"


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "oui", "on")


@register("user_input", summary="Demande un texte à l'humain (saisie, interface graphique ou terminal) ; sortie : le texte, aussi écrit dans un fichier.", params={
    "prompt":   ("question posée à l'humain", True),
    "default":  ("texte pré-rempli, modifiable (défaut : vide)", False),
    "required": ("true = refuse un texte vide (défaut true)", False),
    "save_as":  ("fichier (chemin relatif) où écrire le texte (défaut tmp/saisie/<id de l'étape>.txt)", False),
    "voice":    ("true = bouton micro : dictée transcrite par CSAgent (interface graphique ; défaut false)", False),
    "language": ("langue parlée : fr, en… ou auto (défaut fr)", False),
    "keep_audio": ("true = garde l'enregistrement et sa transcription dans recordings/ et transcripts/ (défaut false : supprimés)", False),
    "csagent_url": ("URL de CSAgent pour la dictée (défaut http://localhost:8080)", False),
    "csagent_key": ("clé d'API de CSAgent (défaut demo-secret)", False),
})
def user_input(params: dict) -> dict:
    """
    Output fields:
        output: le texte saisi (sans espaces en début et fin)
        path:   le fichier où il a été écrit
    """
    prompt   = str(params.get("prompt", "")).strip() or "Saisie demandée"
    default  = str(params.get("default", ""))
    required = _truthy(params.get("required", True))
    sid      = str(params.get("_step_id") or "saisie")
    path     = str(params.get("save_as") or "").strip() or f"tmp/saisie/{sid}.txt"
    emit     = params.get("_callback")
    wait_fn  = params.get("_wait_input")

    voice    = _truthy(params.get("voice", False))
    request  = {"prompt": prompt, "default": default, "required": required, "voice": voice,
                "language": str(params.get("language") or "fr").strip().lower(),
                "keep_audio": _truthy(params.get("keep_audio", False)),
                "csagent_url": str(params.get("csagent_url") or "http://localhost:8080"),
                "csagent_key": str(params.get("csagent_key") or "demo-secret")}
    if emit:
        # la clé d'API ne part jamais vers l'interface
        emit({"type": "user_input_required", **{k: request[k] for k in ("prompt", "default", "required", "voice", "language")}})
    else:
        print(f"\n  {_YLW}✎  {prompt}{_RST}")
        if voice:
            print("  (dictée indisponible dans le terminal : saisissez le texte)")

    if wait_fn:
        answer = wait_fn(request) or {}               # bloque jusqu'à la réponse de l'interface
    else:
        try:
            answer = {"text": input("  > ")}
        except EOFError:
            answer = {"cancelled": True}

    if answer.get("cancelled"):
        return {"output": "Saisie annulée.", "status": "error"}
    text = str(answer.get("text", "")).strip()
    if required and not text:
        return {"output": "Saisie vide : un texte est obligatoire (required: false pour l'autoriser).", "status": "error"}

    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    except OSError as exc:
        return {"output": f"Écriture impossible ({path}) : {exc}", "status": "error"}
    return {"output": text, "path": path, "status": "ok"}
