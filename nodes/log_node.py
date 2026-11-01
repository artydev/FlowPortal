"""nodes/log_node.py — print a message to the terminal."""

from . import register

_COLOURS = {
    "info":    "\033[1m",
    "success": "\033[92m",
    "warn":    "\033[93m",
    "error":   "\033[91m",
}
_RST = "\033[0m"


@register("log", summary="Affiche un message dans la console et dans le log de l'interface.", params={
    "message": ("texte affiché (accepte les {{variables}})", True),
    "level":   ("info | success | warn | error (défaut info)", False),
})
def log(params: dict) -> dict:
    """
    Print a message to the terminal.

    Params:
        message (str):  text to display (supports {{variables}})
        level   (str):  info | success | warn | error  (default: info)

    Output fields:
        output: the message that was printed
    """
    message = params.get("message", "")
    level   = params.get("level", "info")
    col     = _COLOURS.get(level, _COLOURS["info"])

    print()
    for line in message.splitlines():
        print(f"  {col}{line}{_RST}")
    print()

    return {"output": message, "status": "ok"}
