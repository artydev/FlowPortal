#!/usr/bin/env python3
"""
engine.py — Poor Man's n8n
──────────────────────────
Runs a YAML workflow file as a directed graph.

Steps are connected via `next`, `on_approve` and `on_reject` keys.
If no `next` is specified the engine falls back to array order
(backward-compatible with linear workflows).

Usage:
    python engine.py workflows/demo_autofix.yaml
    python engine.py workflows/demo_code_review.yaml
"""

import glob
import os
import re
import sys
import time
import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Missing dependency — run: pip install pyyaml")
    sys.exit(1)

from nodes import REGISTRY
import schema

# ── Windows : sortie UTF-8 + séquences ANSI (couleurs) dans la console ────────
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if os.name == "nt":
    os.system("")          # active le mode VT (couleurs ANSI) de la console Windows

# ── ANSI colours ──────────────────────────────────────────────────────────────
RST  = "\033[0m";  BOLD = "\033[1m";  DIM  = "\033[2m"
RED  = "\033[91m"; GRN  = "\033[92m"; YLW  = "\033[93m"; CYN = "\033[96m"

def c(col, text): return f"{col}{text}{RST}"
def rule():        print(c(DIM, "─" * 64))


# ── template resolution ───────────────────────────────────────────────────────
_VAR_RE = re.compile(r"\{\{(.+?)\}\}")


def _builtin(name: str):
    if name == "today": return datetime.date.today().isoformat()
    if name == "now":   return datetime.datetime.now().isoformat(timespec="seconds")
    if name.startswith("env."):
        return os.environ.get(name[4:], "")
    return None


def resolve(template, ctx: dict) -> str:
    if not isinstance(template, str):
        return template

    def _sub(m):
        expr = m.group(1).strip()
        bi = _builtin(expr)
        if bi is not None:
            return bi
        parts = expr.split(".", 1)
        sid   = parts[0]
        field = parts[1] if len(parts) > 1 else "output"
        return str(ctx.get(sid, {}).get(field, ""))

    return _VAR_RE.sub(_sub, template)


def resolve_all(obj, ctx: dict):
    if isinstance(obj, str):  return resolve(obj, ctx)
    if isinstance(obj, dict): return {k: resolve_all(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list): return [resolve_all(item, ctx) for item in obj]
    return obj


def eval_expr(expr: str, ctx: dict) -> bool:
    resolved = resolve(expr, ctx)
    try:
        safe = {"__builtins__": {}, "true": True, "false": False}
        return bool(eval(resolved, safe, {}))
    except Exception:
        return False


# ── engine ────────────────────────────────────────────────────────────────────
# Keys stripped from step dict before passing params to a node handler.
_RESERVED = {
    "id", "type", "skip_if", "continue_on_error", "description",
    "next", "on_approve", "on_reject",
}


class WorkflowEngine:

    def __init__(self, workflow_path: str,
                 event_callback=None,
                 approval_wait=None,
                 input_wait=None):
        """
        event_callback : callable(event: dict) — UI event bus (optional)
        approval_wait  : callable() -> str — blocks until human approves/rejects;
                         returns "approved" or "rejected" (optional; falls back
                         to terminal prompt)
        input_wait     : callable(request: dict) -> dict — blocks until the human submits
                         the text of a user_input step ({"text": ...} or {"cancelled": True})
        """
        path = Path(workflow_path)
        if not path.exists():
            print(c(RED, f"\n  Workflow not found: {workflow_path}\n"))
            sys.exit(1)
        self.path = path
        self._text = path.read_text(encoding="utf-8")
        self.wf = yaml.safe_load(self._text)

        self.ctx: dict = {}
        self._cb            = event_callback
        self._approval_wait = approval_wait
        self._input_wait    = input_wait

        # Pre-seed ctx with workflow inputs
        for k, v in self.wf.get("inputs", {}).items():
            self.ctx[k] = {"output": str(v), "value": v}

    # ── event helper ──────────────────────────────────────────────────────────
    def _emit(self, event: dict):
        if self._cb:
            self._cb(event)

    # ── graph helpers ─────────────────────────────────────────────────────────
    def _build_index(self, steps: list) -> dict:
        """Build {step_id: step_dict} and inject implicit `_array_next` links."""
        index = {}
        for i, step in enumerate(steps):
            sid = step.get("id", f"step_{i+1}")
            s   = dict(step)
            s["_array_next"] = steps[i + 1].get("id") if i + 1 < len(steps) else None
            index[sid] = s
        return index

    def _next_id(self, step: dict, result: dict) -> str | None:
        """Determine which step to execute next based on the step definition."""
        stype = step.get("type", "")

        # Terminal node — always stop
        if stype == "end":
            return None

        # Branch node — follow the approval answer
        if stype == "human_approval":
            answer = result.get("answer", "rejected")
            return step.get("on_approve") if answer == "approved" \
                   else step.get("on_reject")

        # Explicit next
        if "next" in step:
            return step["next"]

        # Backward-compat: implicit array order
        return step.get("_array_next")

    # ── main run loop ─────────────────────────────────────────────────────────
    def run(self):
        try:
            self._run()
        finally:
            try:                                  # ferme les serveurs MCP ouverts par ce workflow
                import mcp_client
                mcp_client.close_all()
            except Exception:
                pass

    def _run(self):
        name  = self.wf.get("name", "workflow")
        desc  = self.wf.get("description", "")
        steps = self.wf.get("steps", [])

        index = self._build_index(steps)

        self._emit({"type": "workflow_start", "name": name,
                    "description": desc.strip(),
                    "total": len(steps), "steps": [
                        {"id": s.get("id", f"step_{i+1}"),
                         "type": s.get("type", "unknown"),
                         "description": s.get("description", ""),
                         "next": s.get("next"),
                         "on_approve": s.get("on_approve"),
                         "on_reject": s.get("on_reject")}
                        for i, s in enumerate(steps)
                    ]})

        # ── pré-contrôle du fichier : fautes de frappe, types inconnus, cibles manquantes ──
        issues = schema.check(self._text)
        if issues:
            print(c(YLW, f"\n  Contrôle de {self.path.name} :"))
            print(schema.format_issues(self.path.name, issues))
            for level, line, msg in issues:
                where = f"ligne {line} : " if line else ""
                self._emit({"type": "workflow_warning" if level == "warn" else "workflow_error",
                            "message": f"{where}{msg}"})
            if any(level == "error" for level, _, _ in issues):
                print(c(RED, "\n  ✗  Workflow non exécuté : corrige les erreurs ci-dessus "
                             "(python engine.py --check <fichier>).\n"))
                return

        print(c(BOLD, "\n╔══════════════════════════════════════════════════════════╗"))
        print(c(BOLD, f"║  🔧  {name:<54}║"))
        if desc:
            line = desc.strip().splitlines()[0][:54]
            print(c(BOLD, f"║  {c(DIM, line):<62}║"))
        print(c(BOLD, "╚══════════════════════════════════════════════════════════╝\n"))

        step_num = 0
        total    = len(steps)
        # Start from the first step
        current_id = steps[0].get("id", "step_1") if steps else None

        while current_id:
            step = index.get(current_id)
            if step is None:
                print(c(RED, f"  ✗  Unknown step id: '{current_id}'"))
                self._emit({"type": "workflow_error",
                            "message": f"Unknown step id: '{current_id}'"})
                return

            step_num += 1
            sid   = step.get("id",   current_id)
            stype = step.get("type", "unknown")

            rule()
            print(c(BOLD, f"  [{step_num}/{total}] {sid}") + c(DIM, f"  ({stype})"))

            # ── skip_if ────────────────────────────────────────────────────
            skip_expr = step.get("skip_if")
            if skip_expr and eval_expr(str(skip_expr), self.ctx):
                print(c(YLW, "  ⏭  skipped"))
                self.ctx[sid] = {"output": "", "status": "skipped"}
                self._emit({"type": "step_skip", "step_id": sid, "index": step_num,
                            "total": total, "reason": str(skip_expr)})
                current_id = self._next_id(step, {"answer": "rejected"})
                continue

            self._emit({"type": "step_start", "step_id": sid,
                        "step_type": stype, "index": step_num, "total": total})

            # ── handle terminal `end` type inline ─────────────────────────
            if stype == "end":
                msg = resolve(step.get("message", ""), self.ctx)
                if msg:
                    print(c(GRN, f"  {msg}"))
                self.ctx[sid] = {"output": msg, "status": "ok"}
                self._emit({"type": "step_done", "step_id": sid, "output": msg})
                rule()
                print(c(BOLD, c(GRN, "\n  ✅  Workflow complete.\n")))
                self._emit({"type": "workflow_done"})
                return

            # ── resolve params & dispatch ──────────────────────────────────
            params  = resolve_all(
                {k: v for k, v in step.items() if k not in _RESERVED},
                self.ctx,
            )
            # Inject runtime callbacks into params (not visible in YAML)
            if self._cb:
                params["_callback"] = lambda ev, _sid=sid: self._cb(
                    {**ev, "step_id": _sid})
            if self._approval_wait:
                params["_wait_approval"] = self._approval_wait
            if self._input_wait:
                params["_wait_input"] = self._input_wait
            params["_step_id"] = sid

            handler = REGISTRY.get(stype)
            if handler is None:
                msg = f"Unknown node type: '{stype}'"
                print(c(RED, f"  ✗  {msg}"))
                self.ctx[sid] = {"output": "", "status": "error"}
                self._emit({"type": "step_error", "step_id": sid, "output": msg})
                if not step.get("continue_on_error"):
                    self._abort()
                    return
                current_id = self._next_id(step, {})
                continue

            t0     = time.time()
            result = handler(params)
            dur    = round(time.time() - t0, 1)
            self.ctx[sid] = result

            # ── print & emit outcome ───────────────────────────────────────
            if result.get("status") == "error":
                raw  = str(result.get("output", "error"))
                prev = raw[:200].replace("\n", " ⏎ ")
                print(c(RED, f"  ✗  {prev}"))
                self._emit({"type": "step_error", "step_id": sid, "output": raw,
                            "duration": dur, "returncode": result.get("returncode")})
                if not step.get("continue_on_error"):
                    self._abort()
                    return
            else:
                raw  = str(result.get("output", ""))
                prev = raw[:120].replace("\n", " ⏎ ")
                if len(raw) > 120:
                    prev += "…"
                print(c(GRN, f"  ✓  {prev}"))
                self._emit({"type": "step_done", "step_id": sid, "output": raw,
                            "duration": dur, "returncode": result.get("returncode")})

            current_id = self._next_id(step, result)

        rule()
        print(c(BOLD, c(GRN, "\n  ✅  Workflow complete.\n")))
        self._emit({"type": "workflow_done"})

    def _abort(self):
        rule()
        print(c(RED, "\n  ✗  Workflow aborted.\n"))
        self._emit({"type": "workflow_error", "message": "Workflow aborted"})


# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--nodes" in args:                      # aide : types d'étape et paramètres
        print(schema.describe(markdown="--md" in args))
        sys.exit(0)

    if "--schema" in args:                     # schéma JSON pour l'éditeur (VS Code)
        import json
        print(json.dumps(schema.json_schema(), indent=2, ensure_ascii=False))
        sys.exit(0)

    if "--check" in args:                      # validation sans exécution
        files = []
        for a in args:
            if a.startswith("--"):
                continue
            # cmd.exe (Windows) ne développe pas « * » : on le fait ici ; bash l'a déjà fait sous Linux
            files += sorted(glob.glob(a)) or [a]
        if not files:
            print("\nUsage:  python engine.py --check workflows\\*.yaml\n")
            sys.exit(1)
        failed = False
        for f in files:
            issues = schema.check(Path(f).read_text(encoding="utf-8"))
            name = Path(f).name
            if not issues:
                print(c(GRN, f"  ✓ {name}  aucun problème"))
                continue
            print(schema.format_issues(name, issues))
            failed = failed or any(level == "error" for level, _, _ in issues)
        sys.exit(1 if failed else 0)

    if not args:
        print("\nUsage:")
        print("  python engine.py <workflow.yaml>        exécuter un workflow")
        print("  python engine.py --check <f.yaml> ...   valider sans exécuter")
        print("  python engine.py --nodes [--md]         lister les types d'étape et leurs paramètres")
        print("  python engine.py --schema               schéma JSON (autocomplétion VS Code)\n")
        sys.exit(1)
    WorkflowEngine(args[0]).run()
