"""FlowPortal : assistant de création et de modification de workflows (sans interface, sans eel).

L'assistant confie la demande de l'utilisateur à CSAgent, qui édite un FICHIER DE TRAVAIL
(`tmp/assist/<id>/draft.yaml`) et jamais le vrai workflow. Le YAML ne passe donc pas dans le prompt
(l'adresse d'appel de CSAgent a une longueur limitée). Le résultat est validé par `schema.check` ; en cas
d'erreur, CSAgent est relancé pour corriger (deux fois au plus). Le portail montre ensuite les différences, et
c'est l'utilisateur qui décide de les appliquer à l'éditeur, puis d'enregistrer.

    assistant = Assistant(portal, base_dir, agent_fn)
    result = assistant.run(texte_courant, "ajoute une étape de validation", emit=..., cancel=...)
"""

import difflib
import shutil
import uuid
from pathlib import Path

MAX_INSTRUCTION = 3000        # caractères : la demande voyage dans l'adresse d'appel de CSAgent
MAX_DRAFT = 200_000           # octets : taille maximale d'un brouillon relu
DEFAULT_FIXES = 2

SKELETON = "name: nouveau-workflow\ndescription: À compléter.\nsteps:\n  - id: fin\n    type: end\n    message: Terminé\n"


def line_diff(old: str, new: str, context: int = 2) -> list:
    """Différences ligne à ligne : [{"op": "eq"|"add"|"del"|"gap", "text": ...}], contexte réduit autour des changements."""
    a, b = old.splitlines(), new.splitlines()
    rows = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            rows += [{"op": "eq", "text": t} for t in a[i1:i2]]
        else:
            rows += [{"op": "del", "text": t} for t in a[i1:i2]]
            rows += [{"op": "add", "text": t} for t in b[j1:j2]]
    keep = set()
    for i, r in enumerate(rows):
        if r["op"] != "eq":
            keep.update(range(max(0, i - context), min(len(rows), i + context + 1)))
    out, skipped = [], False
    for i, r in enumerate(rows):
        if i in keep:
            out.append(r)
            skipped = False
        elif not skipped:
            out.append({"op": "gap", "text": "…"})
            skipped = True
    return out if any(r["op"] in ("add", "del") for r in rows) else []


def build_prompt(rel_path: str, instruction: str) -> str:
    return (f"Tu dois modifier le fichier de workflow CSFlow « {rel_path} » selon la demande ci-dessous.\n"
            "Règles : édite UNIQUEMENT ce fichier, sans créer ni modifier aucun autre fichier ; "
            "respecte la compétence « csflow » (types d'étapes, ids uniques, références {{id.output}}) ; "
            "conserve tout ce que la demande ne concerne pas (ids, commentaires, ordre) ; "
            "le fichier doit rester un YAML valide avec au moins « name » et « steps ». "
            "Le workflow s'exécute sous Windows : les chemins des fichiers qu'il lit ou écrit sont relatifs au dossier de FlowPortal ; les fichiers qu'il produit vont dans le sous-dossier « tmp/ » (par exemple « tmp/rapport.html »), jamais à la racine ; "
            "pour ouvrir un fichier, utilise la commande shell « start \"\" \"fichier\" » (application par défaut), "
            "jamais « start navigateur fichier ». "
            "Quand tu as terminé, réponds en une ou deux phrases : ce que tu as changé.\n"
            f"Demande : {instruction}")


def build_fix_prompt(rel_path: str, issues: list) -> str:
    lines = "\n".join(f"- {('ligne %s : ' % i['line']) if i.get('line') else ''}{i['message']}"
                      for i in issues if i["level"] == "error")
    return (f"Le fichier « {rel_path} » contient des erreurs de validation CSFlow :\n{lines}\n"
            "Corrige uniquement ces erreurs dans ce fichier, sans rien changer d'autre, "
            "puis réponds en une phrase.")


class Assistant:

    def __init__(self, portal, base_dir, agent_fn, max_fixes: int = DEFAULT_FIXES):
        self.portal = portal
        self.base = Path(base_dir)
        self.agent = agent_fn
        self.max_fixes = max(0, int(max_fixes))
        self.root = self.base / "tmp" / "assist"

    def cleanup_stale(self) -> None:
        """Supprime les brouillons laissés par une session précédente."""
        shutil.rmtree(self.root, ignore_errors=True)

    def run(self, text: str, instruction: str, emit=None, cancel=None, wait_approval=None, timeout: int = 180) -> dict:
        instruction = str(instruction or "").strip()
        if not instruction:
            return {"ok": False, "error": "La demande est vide."}
        if len(instruction) > MAX_INSTRUCTION:
            return {"ok": False, "error": f"La demande est trop longue ({len(instruction)} caractères, {MAX_INSTRUCTION} au plus) : résumez-la."}
        original = str(text or "").strip() and str(text) or SKELETON
        folder = self.root / uuid.uuid4().hex[:10]
        draft = folder / "draft.yaml"
        rel = str(draft.resolve())          # chemin absolu : le worker CSAgent n'a pas le même dossier de travail que le portail
        say = emit or (lambda ev: None)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            draft.write_text(original, encoding="utf-8")
            prompt, answers = build_prompt(rel, instruction), []
            issues, attempts = [], 0
            for attempt in range(self.max_fixes + 1):
                attempts = attempt + 1
                say({"type": "assist_status",
                     "text": "CSAgent travaille sur votre demande…" if attempt == 0
                             else f"Correction des erreurs ({attempt}/{self.max_fixes})…"})
                res = self.agent({"prompt": prompt, "timeout": timeout, "_callback": say,
                                  "_wait_approval": wait_approval, "_cancel": cancel})
                if cancel is not None and cancel.is_set():
                    return {"ok": False, "error": "Arrêté par l'utilisateur."}
                if res.get("status") == "error":
                    return {"ok": False, "error": str(res.get("output") or "CSAgent a échoué.")}
                answers.append(str(res.get("output") or "").strip())
                try:
                    if draft.stat().st_size > MAX_DRAFT:
                        return {"ok": False, "error": "Le brouillon produit est trop volumineux."}
                    new = draft.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    return {"ok": False, "error": "CSAgent n'a pas laissé de brouillon lisible."}
                if not new.strip():
                    return {"ok": False, "error": "CSAgent a vidé le brouillon : aucune modification retenue."}
                issues = self.portal.check_yaml(new)["issues"]
                if not any(i["level"] == "error" for i in issues):
                    break
                prompt = build_fix_prompt(rel, issues)
            diff = line_diff(original, new)
            return {"ok": True, "draft": new, "changed": new != original, "diff": diff, "issues": issues,
                    "answer": answers[0] if answers and answers[0] else (answers[-1] if answers else ""),
                    "attempts": attempts}
        finally:
            shutil.rmtree(folder, ignore_errors=True)
