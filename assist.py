"""FlowPortal : assistant de création et de modification de workflows (sans interface, sans eel).

L'assistant confie la demande de l'utilisateur à CSAgent, qui édite un FICHIER DE TRAVAIL
(`tmp/assist/<id>/draft.yaml`) et jamais le vrai workflow. Le YAML ne passe donc pas dans le prompt
(l'adresse d'appel de CSAgent a une longueur limitée). Le résultat est validé par `schema.check` ; en cas
d'erreur, CSAgent est relancé pour corriger (deux fois au plus). Le portail montre ensuite les différences, et
c'est l'utilisateur qui décide de les appliquer à l'éditeur, puis d'enregistrer.

    assistant = Assistant(portal, base_dir, agent_fn)
    result = assistant.run(texte_courant, "ajoute une étape de validation", emit=..., cancel=...)

Réponse structurée : en plus du YAML (`draft`) et du diff ligne à ligne (`diff`), `run` renvoie `view` (le workflow
résumé étape par étape) et `changes` (ce qui a bougé, par id d'étape). Les deux sont calculés ici en lisant le YAML,
jamais demandés à CSAgent : ils restent donc fiables, et valables aussi pour une modification faite à la main.
Voir `describe_workflow` et `step_changes` pour le détail des champs.
"""

import difflib
import shutil
import uuid
from pathlib import Path

import yaml

from friendly import friendly_problem, friendly_title, step_kind

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


# ── Vue structurée d'un workflow (pour l'interface) ───────────────────────────
_FLOW_KEYS = ("next", "on_approve", "on_reject")      # clés qui rendent le déroulement non linéaire


def _load(text: str):
    """(workflow | None, [(position, étape)]) : tolérant, un YAML illisible donne (None, [])."""
    try:
        wf = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, []
    if not isinstance(wf, dict):
        return None, []
    steps = wf.get("steps")
    if not isinstance(steps, list):
        return wf, []
    return wf, [(i, s) for i, s in enumerate(steps) if isinstance(s, dict)]


def _step_id(step: dict, pos: int) -> str:
    return str(step.get("id") or f"step_{pos + 1}")        # même repli que le moteur


def _step_title(step: dict, sid: str) -> str:
    first = str(step.get("description") or "").strip().splitlines()
    return first[0] if first else sid


def _step_starts(text: str) -> list:
    """Numéro de ligne (à partir de 1) où commence chaque étape de la liste `steps`."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return []
    if not isinstance(root, yaml.MappingNode):
        return []
    for key, value in root.value:
        if key.value == "steps" and isinstance(value, yaml.SequenceNode):
            return [item.start_mark.line + 1 for item in value.value]
    return []


def describe_workflow(text: str, issues: list = None):
    """Le workflow résumé pour l'interface, ou None si le YAML est illisible ou sans étapes.

        {"name", "title", "description", "category", "branches": bool,
         "steps": [{"index", "id", "type", "title", "label", "icon", "kind", "conditional", "autonomous",
                    "problems": [{"level", "message", "friendly"}]}]}

    title (étape) : « description », sinon l'id. label : « description », sinon le verbe du type (vue simple).
    friendly : le message de validation en langage courant (None : à ne pas montrer). title (workflow) : nom lisible. conditional : l'étape a un `skip_if`. autonomous : étape
    `agent` en `auto_confirm: true` (CSAgent agit sans demander confirmation). branches : au moins une étape
    utilise next / on_approve / on_reject, donc l'ordre du fichier n'est pas forcément l'ordre d'exécution.
    problems : les lignes de `issues` (format de `check_yaml`) situées dans le texte de l'étape.
    """
    wf, items = _load(text)
    if wf is None or not items:
        return None
    starts, issues = _step_starts(text), issues or []
    steps = []
    for pos, step in items:
        sid = _step_id(step, pos)
        lo = starts[pos] if pos < len(starts) else None
        hi = starts[pos + 1] - 1 if pos + 1 < len(starts) else float("inf")
        kind = step_kind(str(step.get("type") or ""))
        title = _step_title(step, sid)
        label = title if title != sid else kind["kind"]            # sans description : le verbe du type, jamais l'identifiant
        found = [i for i in issues if lo is not None and i.get("line") and lo <= i["line"] <= hi]
        steps.append({
            "index": pos,
            "id": sid,
            "type": str(step.get("type") or ""),
            "title": title,
            "label": label,
            **kind,
            "conditional": bool(step.get("skip_if")),
            "autonomous": step.get("type") == "agent" and bool(step.get("auto_confirm")),
            "problems": [{"level": i["level"], "message": i["message"],
                          "friendly": friendly_problem(i["message"], label)} for i in found],
        })
    return {"name": str(wf.get("name") or ""), "title": friendly_title(wf.get("name") or ""), "description": str(wf.get("description") or ""),
            "category": " ".join(str(wf.get("category") or "").split()),
            "branches": any(k in s for _, s in items for k in _FLOW_KEYS), "steps": steps}


def step_changes(old: str, new: str):
    """Ce qui a changé entre deux versions, étape par étape (rapprochées par id), ou None si `new` est illisible.

        {"added": [id], "removed": [{"id", "type", "title"}], "changed": [{"id", "keys": [clé]}], "reordered": bool,
         "category": {"from": str, "to": str} | None}

    changed : même id dans les deux versions mais contenu différent ; keys = les clés de l'étape qui diffèrent.
    reordered : les étapes communes ne sont plus dans le même ordre. Un id renommé compte comme retiré + ajouté.
    category : la catégorie du workflow a changé ("" = aucune) ; None si elle est restée la même.
    """
    wf_new, items_new = _load(new)
    if wf_new is None or not items_new:
        return None
    wf_old, items_old = _load(old)
    before = {_step_id(s, p): s for p, s in items_old}
    cat_old = " ".join(str((wf_old or {}).get("category") or "").split())
    cat_new = " ".join(str(wf_new.get("category") or "").split())
    after = {_step_id(s, p): s for p, s in items_new}
    return {
        "added": [i for i in after if i not in before],
        "removed": [{"id": i, "type": str(s.get("type") or ""), "title": _step_title(s, i)}
                    for i, s in before.items() if i not in after],
        "changed": [{"id": i, "keys": sorted(k for k in set(before[i]) | set(s) if before[i].get(k) != s.get(k))}
                    for i, s in after.items() if i in before and before[i] != s],
        "reordered": [i for i in before if i in after] != [i for i in after if i in before],
        "category": {"from": cat_old, "to": cat_new} if cat_old != cat_new else None,
    }


# Consigne de langue : quelques mots dans le prompt suffisent (aucun appel de traduction supplémentaire).
FRENCH = ("Réponds toujours en français, y compris tes commentaires de progression. "
          "Donne à chaque étape une « description » courte et claire, en français, qui dit ce qu'elle fait "
          "(par exemple « Rédaction du résumé »), et un « name » du workflow lisible. ")


def category_rule(categories=None) -> str:
    """Consigne sur la clé racine `category:` (rangement des cartes du portail), à glisser dans le prompt."""
    names = [c for c in (categories or []) if c]
    rule = ("Le workflow peut porter une clé racine « category » (texte court, ex. « category: Démo ») qui le range dans le portail : "
            "conserve celle qui existe, sauf si la demande dit de la changer ou de la retirer ; "
            "si la demande nomme une catégorie, utilise exactement ce nom. ")
    if names:
        rule += ("Si le fichier n'en a pas et que tu crées ou refais le workflow, reprends l'une des catégories déjà utilisées si l'une convient ("
                 + ", ".join(f"« {n} »" for n in names) + "), sinon n'en ajoute pas. ")
    else:
        rule += "N'en invente pas si la demande n'en parle pas. "
    return rule


def build_prompt(rel_path: str, instruction: str, categories=None) -> str:
    return (f"Tu dois modifier le fichier de workflow CSFlow « {rel_path} » selon la demande ci-dessous.\n"
            "Règles : édite UNIQUEMENT ce fichier, sans créer ni modifier aucun autre fichier ; "
            "respecte la compétence « csflow » (types d'étapes, ids uniques, références {{id.output}}) ; "
            "conserve tout ce que la demande ne concerne pas (ids, commentaires, ordre) ; "
            "le fichier doit rester un YAML valide avec au moins « name » et « steps ». "
            + category_rule(categories) +
            "Le workflow s'exécute sous Windows et Linux : les chemins des fichiers qu'il lit ou écrit sont relatifs au dossier de FlowPortal ; "
            "les fichiers qu'il produit vont dans le sous-dossier « output/{{run_id}}/ » (par exemple « output/{{run_id}}/rapport.html »), jamais dans « tmp/ » ni à la racine — cela isole chaque run automatiquement ; "
            "pour écrire un fichier, utilise TOUJOURS « type: file_write » (pas python+pathlib) : file_write crée les dossiers manquants automatiquement et le fichier apparaît dans l'onglet Fichiers de l'interface ; "
            "pour ouvrir un fichier HTML, utilise un step « python » avec webbrowser.open(pathlib.Path('output/{{run_id}}/fichier.html').resolve().as_uri()), "
            "jamais « start \"\" \"fichier\" » (non portable, échoue en subprocess). "
            + FRENCH + "Quand tu as terminé, réponds en une ou deux phrases : ce que tu as changé.\n"
            f"Demande : {instruction}")


def build_fix_prompt(rel_path: str, issues: list) -> str:
    lines = "\n".join(f"- {('ligne %s : ' % i['line']) if i.get('line') else ''}{i['message']}"
                      for i in issues if i["level"] == "error")
    return (f"Le fichier « {rel_path} » contient des erreurs de validation CSFlow :\n{lines}\n"
            "Corrige uniquement ces erreurs dans ce fichier, sans rien changer d'autre, "
            + FRENCH + "puis réponds en une phrase.")


class Assistant:

    def __init__(self, portal, base_dir, agent_fn, max_fixes: int = DEFAULT_FIXES):
        self.portal = portal
        self.base = Path(base_dir)
        self.agent = agent_fn
        self.max_fixes = max(0, int(max_fixes))
        self.root = self.base / "tmp" / "assist"

    def _categories(self) -> list:
        """Catégories déjà utilisées par les workflows du portail (triées, sans doublon) ; vide si le portail ne répond pas."""
        try:
            return sorted({c["category"] for c in self.portal.list_cards() if c.get("category")}, key=lambda n: (n.casefold(), n))
        except Exception:
            return []

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
            prompt, answers = build_prompt(rel, instruction, self._categories()), []
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
            changes = step_changes(original, new)
            if changes is not None:
                changes["created"] = not str(text or "").strip()      # point de départ vide : tout est nouveau
            return {"ok": True, "draft": new, "changed": new != original, "diff": diff, "issues": issues,
                    "view": describe_workflow(new, issues), "changes": changes,
                    "answer": answers[0] if answers and answers[0] else (answers[-1] if answers else ""),
                    "attempts": attempts}
        finally:
            shutil.rmtree(folder, ignore_errors=True)