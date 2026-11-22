"""jsonlogic.py — évaluateur JsonLogic (https://jsonlogic.com), Python 3, sans dépendance.

Adapté du port `json-logic` 0.6.3 (MIT, Python 2 seulement) : mêmes opérateurs,
corrigé pour Python 3. Un opérateur inconnu lève ValueError ; `if` est paresseux.

    jsonlogic.apply({"!=": [{"var": "statut.output"}, "ROUGE"]}, ctx)
"""

from functools import reduce


def _truthy(a):
    return bool(a)  # JsonLogic JS : [] est faux, comme en Python


def _var(data, path, default=None):
    if path in ("", None):
        return data
    cur = data
    for key in str(path).split("."):
        if isinstance(cur, dict):
            if key not in cur:
                return default
            cur = cur[key]
        elif isinstance(cur, (list, tuple)) and key.lstrip("-").isdigit():
            try:
                cur = cur[int(key)]
            except IndexError:
                return default
        else:
            return default
    return cur


def _num(a):
    return float(a)


def _cmp(fn_ab):
    def op(a, b, c=None):
        if c is None:
            return fn_ab(a, b)
        return fn_ab(a, b) and fn_ab(b, c)   # forme « entre »
    return op


_OPS = {
    "==":  lambda a, b: a == b,
    "===": lambda a, b: type(a) is type(b) and a == b,   # strict : le type compte
    "!=":  lambda a, b: a != b,
    "!==": lambda a, b: not (type(a) is type(b) and a == b),
    ">":   lambda a, b: a > b,
    ">=":  lambda a, b: a >= b,
    "<":   _cmp(lambda a, b: a < b),
    "<=":  _cmp(lambda a, b: a <= b),
    "!":   lambda a: not _truthy(a),
    "!!":  lambda a: _truthy(a),
    "%":   lambda a, b: a % b,
    "?:":  lambda a, b, c: b if _truthy(a) else c,
    "in":  lambda a, b: a in b if isinstance(b, (str, list, tuple, dict)) else False,
    "cat": lambda *a: "".join(str(x) for x in a),
    "+":   lambda *a: reduce(lambda t, x: t + _num(x), a, 0.0),
    "*":   lambda *a: reduce(lambda t, x: t * _num(x), a, 1.0),
    "-":   lambda a, b=None: -_num(a) if b is None else _num(a) - _num(b),
    "/":   lambda a, b=None: a if b is None else _num(a) / _num(b),
    "min": lambda *a: min(a),
    "max": lambda *a: max(a),
    "count": lambda *a: sum(1 for x in a if _truthy(x)),
}


def apply(rule, data=None):
    """Évalue `rule` (dict JsonLogic) sur `data`. Une valeur non-dict est renvoyée telle quelle."""
    if isinstance(rule, list):
        return [apply(r, data) for r in rule]
    if not isinstance(rule, dict):
        return rule
    if len(rule) != 1:
        raise ValueError("une règle JsonLogic a exactement une clé (l'opérateur)")
    op, values = next(iter(rule.items()))
    if not isinstance(values, (list, tuple)):
        values = [values]

    # opérateurs paresseux : on n'évalue que ce qui est nécessaire
    if op == "and":
        res = True
        for v in values:
            res = apply(v, data)
            if not _truthy(res):
                return res
        return res
    if op == "or":
        res = False
        for v in values:
            res = apply(v, data)
            if _truthy(res):
                return res
        return res
    if op in ("if", "?:"):
        vals = list(values)
        i = 0
        while i + 1 < len(vals):
            if _truthy(apply(vals[i], data)):
                return apply(vals[i + 1], data)
            i += 2
        return apply(vals[i], data) if i < len(vals) else None

    args = [apply(v, data) for v in values]
    if op == "var":
        return _var(data, *args[:2])
    if op not in _OPS:
        raise ValueError(f"opérateur JsonLogic inconnu : {op}")
    return _OPS[op](*args)
