"""safe_expr.py — substitution sûre des {{variables}} dans les expressions Python (transform, skip_if).

Problème (issue #9) : coller la sortie d'une étape (texte d'un agent, d'une page web, d'un mail…) dans le
texte d'une expression puis l'évaluer fait de ces données du CODE : une apostrophe ou un retour à la ligne
casse le littéral (« unterminated string literal »), et une sortie forgée s'exécute.

Ici, on parcourt le gabarit écrit par l'auteur du workflow (jamais les valeurs) et l'on insère chaque valeur
sous une forme qui reste une DONNÉE :
  • dans un littéral entre guillemets ('{{x.output}}', "…", '''…''') : la valeur est échappée pour ce littéral
    (barre oblique inverse, guillemet du littéral, retours à la ligne, caractères de contrôle) ;
  • hors littéral ({{x.returncode}} <= 0) : un nombre reste un nombre ; une valeur Python (liste, dict,
    nombre, booléen…) est insérée par repr ; tout autre texte devient un littéral texte (repr).
Les workflows existants, qui écrivent déjà leurs propres guillemets, continuent donc de fonctionner.

Limite connue : dans un littéral brut (r'…') les échappements ne sont pas interprétés ; évitez d'y mettre {{…}}.
"""
from __future__ import annotations

import ast
import re

_VAR_RE = re.compile(r"\{\{(.+?)\}\}")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


def _escape_in_literal(text: str, quote: str) -> str:
    """Échappe `text` pour qu'il reste du contenu à l'intérieur d'un littéral délimité par `quote`."""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == quote[0]:
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\x%02x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _bare(value) -> str:
    """Valeur insérée hors de tout littéral : un nombre reste un nombre, le reste devient une donnée."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if _NUMBER_RE.match(stripped) else repr(value)
    return repr(value)


def substitute(template: str, value_of) -> str:
    """Remplace chaque {{nom}} de `template` ; `value_of(nom)` rend la valeur brute (str ou objet Python)."""
    out: list[str] = []
    i, n, quote = 0, len(template), None          # quote : délimiteur du littéral en cours, ou None
    while i < n:
        match = _VAR_RE.match(template, i) if template.startswith("{{", i) else None
        if match:
            value = value_of(match.group(1).strip())
            out.append(_escape_in_literal("" if value is None else str(value), quote) if quote else _bare(value))
            i = match.end()
            continue
        ch = template[i]
        if quote is None:
            if ch in "'\"":
                quote = ch * 3 if template.startswith(ch * 3, i) else ch
                out.append(quote)
                i += len(quote)
                continue
        else:
            if ch == "\\" and i + 1 < n:           # séquence d'échappement écrite par l'auteur : on la garde telle quelle
                out.append(template[i:i + 2])
                i += 2
                continue
            if template.startswith(quote, i):
                out.append(quote)
                i += len(quote)
                quote = None
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def check_expression(expression: str) -> None:
    """Refuse les expressions qui touchent aux éléments internes de Python (`__class__`, `_module`…).

    `__builtins__` restreint n'est pas un bac à sable : `().__class__.__base__.__subclasses__()` mène à
    n'importe quel module. Lève ValueError (message lisible) ou SyntaxError."""
    tree = ast.parse(expression.strip(), mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"accès à l'attribut interne « {node.attr} » refusé")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(f"nom interne « {node.id} » refusé")
