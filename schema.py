"""
schema.py — le vocabulaire du YAML CSFlow, en un seul endroit
──────────────────────────────────────────────────────────────
Les mots-clés propres à chaque type d'étape sont déclarés dans le décorateur
@register(..., params={...}) de chaque nœud (dossier nodes/). Ce module y ajoute
les mots-clés communs aux étapes et le type `end` (géré par le moteur), puis offre :

    check(texte_yaml)       → liste de (niveau, ligne, message) : erreurs et avertissements
    describe(markdown)      → l'aide listant tous les types et leurs paramètres
    json_schema()           → un schéma JSON (autocomplétion et erreurs dans VS Code)

En ligne de commande (depuis workflow\\) :
    python engine.py --nodes             liste les types d'étape et leurs paramètres
    python engine.py --check f.yaml      valide un ou plusieurs workflows
    python engine.py --schema            écrit le schéma JSON sur la sortie standard
"""

import difflib
import re

import yaml

from nodes import PARAMS, REGISTRY, SUMMARIES

# ── mots-clés du fichier ──────────────────────────────────────────────────────
TOP_KEYS = {
    "name":        "nom du workflow (affiché dans l'interface)",
    "description": "description du workflow",
    "inputs":      "variables d'entrée, utilisables dans tout le workflow avec {{nom}}",
    "steps":       "liste des étapes",
}

# Mots-clés acceptés sur TOUTE étape (lus par le moteur, pas par les nœuds)
COMMON_KEYS = {
    "id":                ("identifiant unique de l'étape ; son résultat s'utilise avec {{id.output}}", True),
    "type":              ("type d'étape (liste : python engine.py --nodes)", True),
    "description":       ("libellé affiché dans l'interface", False),
    "skip_if":           ("expression Python ; si elle est vraie, l'étape est ignorée. "
                          "Ex : \"'{{statut.output}}' != 'ROUGE'\"", False),
    "continue_on_error": ("true = le workflow continue même si l'étape échoue", False),
    "next":              ("id de l'étape suivante (défaut : l'étape suivante dans la liste)", False),
    "on_approve":        ("human_approval : id de l'étape suivante si approuvé", False),
    "on_reject":         ("human_approval : id de l'étape suivante si rejeté", False),
}

# Type géré directement par le moteur (engine.py)
END_TYPE = ("end", {"message": ("message affiché à la fin du workflow", False)},
            "Termine le workflow (étape finale, aucune étape ne peut la suivre).")

BUILTIN_VARS = {
    "{{today}}":    "date du jour (AAAA-MM-JJ)",
    "{{now}}":      "date et heure (AAAA-MM-JJTHH:MM:SS)",
    "{{env.NOM}}":  "variable d'environnement NOM",
    "{{id.output}}": "résultat de l'étape « id » ; autres champs : {{id.returncode}}, {{id.stdout}}, "
                     "{{id.status}}... selon le type",
    "{{nom}}":      "variable déclarée dans `inputs`",
}

# Types JSON pour le schéma (les autres paramètres acceptent n'importe quelle valeur)
_JSON_TYPES = {
    "timeout":           ["integer", "string"],
    "auto_confirm":      ["boolean", "string"],
    "continue_on_error": ["boolean", "string"],
    "headers":           "object",
    "arguments":         ["object", "string"],
    "max_chars":         ["integer", "string"],
}

_VAR_RE = re.compile(r"\{\{(.+?)\}\}")      # même motif que le moteur


# ── types disponibles ─────────────────────────────────────────────────────────
def _summary(fn) -> str:
    for line in (fn.__doc__ or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def node_types() -> dict:
    """{type: (params, résumé)} pour tous les types, `end` compris."""
    types = {name: (PARAMS.get(name, {}), SUMMARIES.get(name) or _summary(fn)) for name, fn in REGISTRY.items()}
    types[END_TYPE[0]] = (END_TYPE[1], END_TYPE[2])
    return dict(sorted(types.items()))


def allowed_keys(step_type: str) -> dict:
    keys = {k: d for k, (d, _) in COMMON_KEYS.items()}
    keys.update({k: d for k, (d, _) in node_types().get(step_type, ({}, ""))[0].items()})
    return keys


# ── validation ────────────────────────────────────────────────────────────────
def _positions(root) -> dict:
    """Numéros de ligne (à partir de 1) des clés du fichier et de chaque étape."""
    pos = {"top": {}, "steps": []}
    if not isinstance(root, yaml.MappingNode):
        return pos
    for knode, vnode in root.value:
        pos["top"][knode.value] = knode.start_mark.line + 1
        if knode.value == "steps" and isinstance(vnode, yaml.SequenceNode):
            for item in vnode.value:
                d = {"_start": item.start_mark.line + 1}
                if isinstance(item, yaml.MappingNode):
                    d.update({k.value: k.start_mark.line + 1 for k, _ in item.value})
                pos["steps"].append(d)
    return pos


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _suggest(word, choices) -> str:
    close = difflib.get_close_matches(str(word), list(choices), n=1, cutoff=0.6)
    return f" — vouliez-vous dire « {close[0]} » ?" if close else ""


def check(text: str) -> list:
    """Valide un workflow. Retourne [(niveau, ligne|None, message)], niveau = 'error' | 'warn'."""
    issues = []

    def add(level, line, msg):
        issues.append((level, line, msg))

    try:
        wf = yaml.safe_load(text)
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return [("error", mark.line + 1 if mark else None, f"YAML invalide : {exc}")]

    if not isinstance(wf, dict):
        return [("error", 1, "le fichier doit être un dictionnaire YAML avec une clé « steps »")]

    pos = _positions(root)

    for key in wf:
        if key not in TOP_KEYS:
            add("warn", pos["top"].get(key),
                f"clé « {key} » inconnue au niveau du workflow{_suggest(key, TOP_KEYS)} "
                f"(clés : {', '.join(TOP_KEYS)})")

    steps = wf.get("steps")
    if not isinstance(steps, list) or not steps:
        add("error", pos["top"].get("steps", 1), "« steps » doit être une liste d'étapes non vide")
        return issues

    types = node_types()
    ids = [s.get("id") for s in steps if isinstance(s, dict) and s.get("id")]
    known_refs = set(ids) | set(wf.get("inputs") or {}) | {"today", "now"}
    seen = set()

    for i, step in enumerate(steps):
        sp = pos["steps"][i] if i < len(pos["steps"]) else {}
        start = sp.get("_start")
        if not isinstance(step, dict):
            add("error", start, f"étape n°{i + 1} : chaque étape doit être un dictionnaire")
            continue

        sid = step.get("id")
        label = f"étape « {sid} »" if sid else f"étape n°{i + 1}"
        if not sid:
            add("warn", start, f"{label} : pas d'« id » (le moteur utilisera step_{i + 1})")
        elif sid in seen:
            add("error", sp.get("id", start), f"{label} : id en double")
        seen.add(sid)

        stype = step.get("type")
        if not stype:
            add("error", start, f"{label} : « type » manquant")
            continue
        if stype not in types:
            add("error", sp.get("type", start),
                f"{label} : type « {stype} » inconnu{_suggest(stype, types)} "
                f"(types : {', '.join(types)})")
            continue

        params = types[stype][0]
        allowed = allowed_keys(stype)
        for key in step:
            if key not in allowed:
                add("warn", sp.get(key, start),
                    f"{label} ({stype}) : clé « {key} » inconnue, elle sera ignorée"
                    f"{_suggest(key, allowed)}")
        for key, (_, required) in params.items():
            if required and key not in step:
                add("error", start, f"{label} ({stype}) : paramètre obligatoire « {key} » manquant")

        for key in ("next", "on_approve", "on_reject"):
            if key in step and step[key] not in ids:
                add("error", sp.get(key, start),
                    f"{label} : « {key}: {step[key]} » ne désigne aucune étape{_suggest(step[key], ids)}")
        if stype == "human_approval":
            for key in ("on_approve", "on_reject"):
                if key not in step:
                    add("warn", start, f"{label} : « {key} » absent, le workflow s'arrêtera sur cette branche")

        for text_value in _strings({k: v for k, v in step.items() if k != "id"}):
            for ref in _VAR_RE.findall(text_value):
                head = ref.strip().split(".", 1)[0]
                if ref.strip().startswith("env.") or head in known_refs:
                    continue
                add("warn", start,
                    f"{label} : « {{{{{ref.strip()}}}}} » ne correspond à aucune étape ni entrée "
                    f"(sera remplacé par du vide){_suggest(head, known_refs)}")

    issues.sort(key=lambda x: (x[1] is None, x[1] or 0))
    return issues


def format_issues(name: str, issues: list) -> str:
    lines = []
    for level, line, msg in issues:
        mark = "✗" if level == "error" else "⚠"
        where = f"{name}:{line}" if line else name
        lines.append(f"  {mark} {where}  {msg}")
    return "\n".join(lines)


# ── aide ──────────────────────────────────────────────────────────────────────
def describe(markdown: bool = False) -> str:
    out = []
    types = node_types()

    def h(title, level=2):
        out.append(f"\n{'#' * level} {title}\n" if markdown else f"\n{title}\n{'─' * len(title)}")

    def row(name, desc, required=False):
        if markdown:
            out.append(f"- `{name}`{' *(obligatoire)*' if required else ''} : {desc}")
        else:
            out.append(f"  {name:<18}{desc}{' (obligatoire)' if required else ''}")

    out.append("# Référence du YAML CSFlow" if markdown else "RÉFÉRENCE DU YAML CSFLOW")
    out.append("_Générée par `python engine.py --nodes --md` à partir des `@register` de `nodes/`._"
               if markdown else "(générée à partir des @register de nodes/)")

    h("Fichier")
    for k, d in TOP_KEYS.items():
        row(k, d, k == "steps")

    h("Mots-clés de toute étape")
    for k, (d, req) in COMMON_KEYS.items():
        row(k, d, req)

    h("Types d'étape (valeurs de `type:`)")
    for name, (params, summary) in types.items():
        out.append(f"\n### {name}\n{summary}\n" if markdown else f"\n[{name}]  {summary}")
        if not params:
            out.append("- (aucun paramètre)" if markdown else "  (aucun paramètre)")
        for k, (d, req) in params.items():
            row(k, d, req)

    h("Variables {{…}}")
    for k, d in BUILTIN_VARS.items():
        out.append(f"- `{k}` : {d}" if markdown else f"  {k:<18}{d}")

    return "\n".join(out) + "\n"


# ── schéma JSON (autocomplétion VS Code, extension « YAML » de Red Hat) ───────
def json_schema() -> dict:
    def prop(key, desc):
        p = {"description": desc}
        if key in _JSON_TYPES:
            p["type"] = _JSON_TYPES[key]
        return p

    common = {k: prop(k, d) for k, (d, _) in COMMON_KEYS.items()}
    variants = []
    for name, (params, summary) in node_types().items():
        props = dict(common)
        props["type"] = {"const": name, "description": summary}
        props.update({k: prop(k, d) for k, (d, _) in params.items()})
        variants.append({
            "title": name,
            "type": "object",
            "properties": props,
            "required": ["id", "type"] + [k for k, (_, req) in params.items() if req],
            "additionalProperties": False,
        })
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Workflow CSFlow",
        "type": "object",
        "properties": {
            "name":        {"type": "string", "description": TOP_KEYS["name"]},
            "description": {"type": "string", "description": TOP_KEYS["description"]},
            "inputs":      {"type": "object", "description": TOP_KEYS["inputs"]},
            "steps":       {"type": "array", "minItems": 1, "description": TOP_KEYS["steps"],
                            "items": {"oneOf": variants}},
        },
        "required": ["steps"],
        "additionalProperties": False,
    }
