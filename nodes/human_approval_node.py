"""
nodes/human_approval_node.py — pause the workflow for a human decision.

In terminal mode: prompts y/n.
In UI mode:       emits `human_approval_required` and blocks until
                  app.py calls the `_wait_approval` function with
                  "approved" or "rejected".

Step YAML:
    - id: approval
      type: human_approval
      description: Validation humaine
      message: "The analysis is done. Approve the fixes?"
      on_approve: fix
      on_reject:  stop
"""

from . import register

_RST = "\033[0m"; _YLW = "\033[93m"; _GRN = "\033[92m"; _RED = "\033[91m"


@register("human_approval", summary="Met le workflow en pause jusqu'à ce qu'un humain approuve ou rejette.", params={
    "message": ("question posée à l'humain (défaut « Approve to continue? »)", False),
})
def human_approval(params: dict) -> dict:
    """
    Pause and wait for a human approve / reject decision.

    Params:
        message (str): question shown to the user

    Output fields:
        output:  "approved" or "rejected"
        answer:  same (used by engine to choose on_approve / on_reject branch)
    """
    message  = params.get("message", "Approve to continue?")
    emit     = params.get("_callback")
    wait_fn  = params.get("_wait_approval")

    # Notify the UI (or just print in terminal mode)
    if emit:
        emit({"type": "human_approval_required", "message": message})
    else:
        print(f"\n  {_YLW}❓  {message}{_RST}")

    # Wait for the answer
    if wait_fn:
        answer = wait_fn()          # blocks until UI resolves
    else:
        # Terminal fallback
        raw = input("  Approve? [y/n]: ").strip().lower()
        answer = "approved" if raw == "y" else "rejected"

    col = _GRN if answer == "approved" else _RED
    sym = "✓" if answer == "approved" else "✗"
    print(f"  {col}{sym}  {answer.capitalize()}{_RST}")

    return {"output": answer, "answer": answer, "status": "ok"}
