"""nodes/transform_node.py — evaluate a Python expression to reshape data."""

from . import register

# Safe built-ins exposed inside transform expressions
_SAFE_BUILTINS = {
    "len": len, "int": int, "float": float, "str": str,
    "bool": bool, "round": round,
    "sum": sum, "min": min, "max": max, "abs": abs,
    "list": list, "dict": dict, "set": set, "tuple": tuple,
    "sorted": sorted, "reversed": reversed, "enumerate": enumerate,
    "zip": zip, "range": range, "map": map, "filter": filter,
    "any": any, "all": all,
    "True": True, "False": False, "None": None,
}


@register("transform", summary="Évalue une expression Python pour transformer des données.", params={
    "expression": ("expression Python évaluée ; les {{variables}} sont substituées avant", True),
})
def transform(params: dict) -> dict:
    """
    Evaluate a Python expression and return the result as the step output.
    Variables are already resolved before the expression is evaluated, so you
    can embed {{step.output}} directly in the expression string.

    Params:
        expression (str): any Python expression whose result is serialisable

    Output fields:
        output: str(result)
        value:  the raw Python value (int, list, …)

    Example:
        expression: "len('{{prev.output}}'.splitlines())"
        expression: "round(float('{{sales.revenue}}') * 1.2, 2)"
    """
    expression = params.get("expression", "")
    try:
        value = eval(expression, {"__builtins__": _SAFE_BUILTINS}, {})
        return {"output": str(value), "value": value, "status": "ok"}
    except Exception as exc:
        return {"output": f"Transform error: {exc}", "status": "error"}
