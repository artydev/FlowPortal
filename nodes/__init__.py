"""
nodes/__init__.py
Node registry — each node module self-registers by importing this and
using the @register() decorator.
"""

REGISTRY: dict = {}
SUMMARIES: dict = {}   # {type: résumé en une ligne}
PARAMS:   dict = {}     # {type: {param: (description, requis)}} — alimente --nodes, --check, --schema


def register(*names, params=None, summary=""):
    """
    Decorator: bind one or more node type names to a handler function.

    params declares the YAML keys the node understands, as
        {"cle": ("description", True/False pour « obligatoire »)}
    It is the single source of truth for validation and documentation.
    """
    def decorator(fn):
        for name in names:
            REGISTRY[name] = fn
            PARAMS[name]   = dict(params or {})
            SUMMARIES[name] = summary
        return fn
    return decorator


# Import every node module so they run their @register() decorators.
from . import (   # noqa: E402, F401
    file_node,
    shell_node,
    python_node,
    http_node,
    agent_node,
    log_node,
    transform_node,
    human_approval_node,
    user_input_node,
    mcp_node,
)
