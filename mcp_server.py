"""
mcp_server.py — CSFlow comme serveur MCP (stdio)
────────────────────────────────────────────────
Permet à CSAgent (ou tout client MCP) de lister, valider et lancer les workflows
du dossier workflows/, sans interface :

    csagent --mcp-config mcp.json           (voir mcp.json.example)
    CSFlow --mcp                            (ce que CSAgent lance lui-même)

Pourquoi des appels courts ?
    CSAgent coupe toute requête MCP à 60 s. csflow_run et csflow_status attendent donc
    au plus 55 s puis rendent la main avec un état « running » et un run_id ; l'agent
    rappelle csflow_status jusqu'à la fin. Le workflow, lui, tourne dans un thread.

Garde-fous (volontairement stricts) :
    • seuls les fichiers de workflows/ (aucun chemin, aucun « .. ») ;
    • un seul workflow à la fois ;
    • un workflow qui contient une étape human_approval ou user_input est REFUSÉ avant de démarrer
      (la validation humaine se fait dans l'interface de CSFlow, jamais par l'agent) ;
    • le workflow est validé (schema.check) avant l'exécution.

Protocole : JSON-RPC 2.0, un message JSON par ligne sur stdin/stdout.
stdout ne porte QUE le protocole : tout le reste (journal du moteur) va sur stderr.
"""

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import yaml

import schema
from engine import WorkflowEngine

VERSION = "1.0"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_WAIT = 50          # secondes ; CSAgent coupe à 60 s
MAX_WAIT = 55
KEEP_RUNS = 20
PREVIEW = 300              # caractères gardés par sortie d'étape
RESULT_MAX = 2000

WORKFLOWS: Path = Path("workflows")


# ── état d'une exécution ──────────────────────────────────────────────────────
def _clip(value, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


class Run:
    """Suivi d'un workflow lancé en arrière-plan (alimenté par les événements du moteur)."""

    def __init__(self, workflow: str):
        self.id = uuid.uuid4().hex[:8]
        self.workflow = workflow
        self.status = "running"            # running | done | error
        self.started = time.time()
        self.ended = None
        self.total = 0
        self.steps = []                    # [{"id","type","status","duration","returncode","output"}]
        self.message = ""                  # message d'erreur du workflow
        self.warnings = []
        self.thought = ""                  # dernière réflexion de CSAgent (étape agent en cours)
        self.finished = threading.Event()
        self._lock = threading.Lock()

    # -- événements du moteur ------------------------------------------------
    def on_event(self, ev: dict) -> None:
        kind = ev.get("type", "")
        with self._lock:
            if kind == "workflow_start":
                self.total = int(ev.get("total") or 0)
            elif kind == "step_start":
                self.thought = ""
                self.steps.append({"id": ev.get("step_id"), "type": ev.get("step_type"), "status": "running"})
            elif kind in ("step_done", "step_error"):
                step = self._last(ev.get("step_id"))
                if step is not None:
                    step["status"] = "ok" if kind == "step_done" else "error"
                    step["duration"] = ev.get("duration")
                    step["returncode"] = ev.get("returncode")
                    step["output"] = _clip(ev.get("output"), PREVIEW)
                    step["_full"] = _clip(ev.get("output"), RESULT_MAX)
            elif kind == "step_skip":
                self.steps.append({"id": ev.get("step_id"), "type": "?", "status": "skipped"})
            elif kind == "workflow_done":
                self.status = "done"
            elif kind == "workflow_error":
                self.status = "error"
                self.message = _clip(ev.get("message"), 500)
            elif kind == "workflow_warning":
                self.warnings.append(_clip(ev.get("message"), 200))
            elif kind == "agent_thought":
                self.thought = _clip(ev.get("data"), PREVIEW)

    def _last(self, step_id):
        for step in reversed(self.steps):
            if step.get("id") == step_id:
                return step
        return None

    def fail(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.message = _clip(message, 500)

    def finish(self) -> None:
        with self._lock:
            if self.status == "running":        # le moteur s'est arrêté sans événement final
                self.status = "error"
                self.message = self.message or "le workflow s'est arrêté sans signaler sa fin"
            self.ended = time.time()
        self.finished.set()

    # -- instantané lisible par l'agent --------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            now = self.ended or time.time()
            done = sum(1 for s in self.steps if s["status"] in ("ok", "error", "skipped"))
            out = {
                "run_id": self.id,
                "workflow": self.workflow,
                "status": self.status,
                "elapsed_s": round(now - self.started, 1),
                "progress": f"{done}/{self.total}" if self.total else str(done),
                "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in self.steps],
            }
            running = next((s for s in reversed(self.steps) if s["status"] == "running"), None)
            if self.status == "running":
                if running:
                    out["current_step"] = running.get("id")
                if self.thought:
                    out["agent_last_thought"] = self.thought
                out["next"] = f"call csflow_status with run_id={self.id} to wait for the end"
            else:
                last = next((s for s in reversed(self.steps)
                             if s["status"] in ("ok", "error") and s.get("type") not in ("log", "end")), None) \
                    or next((s for s in reversed(self.steps) if s["status"] in ("ok", "error")), None)
                if last:
                    out["result"] = {"step": last.get("id"), "output": last.get("_full", "")}
            if self.message:
                out["error"] = self.message
            failed = next((x for x in reversed(self.steps) if x["status"] == "error"), None)
            if failed and self.status == "error":
                out["failed_step"] = {"id": failed.get("id"), "returncode": failed.get("returncode"),
                                      "output": failed.get("_full", failed.get("output", ""))}
            if self.warnings:
                out["warnings"] = self.warnings[:5]
            return out


_RUNS: dict = {}
_ORDER: list = []
_ACTIVE = None
_STATE_LOCK = threading.Lock()


def _no_approval() -> str:
    raise RuntimeError("human approval is not available over MCP (run this workflow from the CSFlow UI)")


def _worker(run: Run, path: Path) -> None:
    try:
        WorkflowEngine(str(path), event_callback=run.on_event, approval_wait=_no_approval).run()
    except BaseException as exc:                       # noqa: BLE001 — jamais de thread qui meurt en silence
        run.fail(f"{type(exc).__name__}: {exc}")
    finally:
        run.finish()


# ── accès aux workflows (dossier workflows/ uniquement) ───────────────────────
class ToolError(Exception):
    pass


def _resolve(name) -> Path:
    raw = str(name or "").strip().replace("\\", "/")
    if raw.startswith("workflows/"):
        raw = raw[len("workflows/"):]
    if not raw or "/" in raw or raw.startswith(".") or ".." in raw:
        raise ToolError("workflow must be a file name inside the workflows folder, e.g. 'demo_report.yaml'")
    if not raw.lower().endswith((".yaml", ".yml")):
        raw += ".yaml"
    base = WORKFLOWS.resolve()
    path = (base / raw).resolve()
    if path.parent != base or not path.is_file():
        raise ToolError(f"workflow '{raw}' not found in {base.name}/ (use csflow_list)")
    return path


def _inspect(path: Path) -> dict:
    """Informations utiles à l'agent : nom, description, étapes, validation humaine, étape agent."""
    text = path.read_text(encoding="utf-8")
    info = {"file": path.name, "name": path.stem, "description": "", "steps": 0,
            "needs_human": False, "uses_agent": False}
    try:
        wf = yaml.safe_load(text) or {}
        steps = [s for s in (wf.get("steps") or []) if isinstance(s, dict)]
        info.update(name=wf.get("name", path.stem), description=_clip(str(wf.get("description", "")).strip(), 200),
                    steps=len(steps),
                    needs_human=any(s.get("type") in ("human_approval", "user_input") for s in steps),
                    uses_agent=any(s.get("type") == "agent" for s in steps))
    except Exception as exc:                            # noqa: BLE001
        info["invalid_yaml"] = _clip(exc, 200)
    return info, text


def _issues(text: str) -> list:
    return [{"level": lv, "line": ln, "message": _clip(msg, 300)} for lv, ln, msg in schema.check(text)]


def _wait_seconds(args: dict) -> float:
    try:
        value = float(args.get("wait_seconds", DEFAULT_WAIT))
    except (TypeError, ValueError):
        value = DEFAULT_WAIT
    return max(0.0, min(value, MAX_WAIT))


# ── outils ────────────────────────────────────────────────────────────────────
def tool_list(args: dict):
    items = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        if path.suffix.lower() in (".yaml", ".yml") and path.is_file():
            info, _ = _inspect(path)
            items.append(info)
    return {"cwd": str(Path.cwd()), "workflows": items}


def tool_check(args: dict):
    path = _resolve(args.get("workflow"))
    info, text = _inspect(path)
    issues = _issues(text)
    errors = sum(1 for i in issues if i["level"] == "error")
    return {"workflow": path.name, "ok": errors == 0, "errors": errors,
            "warnings": len(issues) - errors, "needs_human": info["needs_human"],
            "uses_agent": info["uses_agent"], "issues": issues}


def tool_run(args: dict):
    global _ACTIVE
    path = _resolve(args.get("workflow"))
    info, text = _inspect(path)
    if info["needs_human"]:
        raise ToolError(f"'{path.name}' contains a human step (approval or text input): it is not run over MCP. "
                        "Tell the user to run it from the CSFlow interface.")
    issues = _issues(text)
    if any(i["level"] == "error" for i in issues):
        return {"started": False, "reason": "validation errors", "issues": issues}

    with _STATE_LOCK:
        if _ACTIVE is not None and not _ACTIVE.finished.is_set():
            active = _ACTIVE.snapshot()
            active["started"] = False
            active["reason"] = "another workflow is still running; wait for it with csflow_status"
            return active
        run = Run(path.name)
        _RUNS[run.id] = run
        _ORDER.append(run.id)
        while len(_ORDER) > KEEP_RUNS:
            _RUNS.pop(_ORDER.pop(0), None)
        _ACTIVE = run
    threading.Thread(target=_worker, args=(run, path), daemon=True).start()
    run.finished.wait(_wait_seconds(args))
    return run.snapshot()


def tool_status(args: dict):
    run_id = str(args.get("run_id") or "").strip()
    if run_id:
        run = _RUNS.get(run_id)
        if run is None:
            raise ToolError(f"unknown run_id '{run_id}'")
    else:
        if not _ORDER:
            raise ToolError("no workflow has been run yet")
        run = _RUNS[_ORDER[-1]]
    run.finished.wait(_wait_seconds(args))
    return run.snapshot()


_WORKFLOW_PARAM = {"type": "string", "description": "File name inside the workflows folder, e.g. 'demo_report.yaml'"}
_WAIT_PARAM = {"type": "number", "description": f"Seconds to wait before returning (default {DEFAULT_WAIT}, max {MAX_WAIT})"}

TOOLS = {
    "csflow_list": (tool_list, {
        "description": "List the CSFlow workflows available (name, description, number of steps, needs_human, uses_agent). Read-only.",
        "inputSchema": {"type": "object", "properties": {}}}),
    "csflow_check": (tool_check, {
        "description": "Validate a CSFlow workflow without running it. Returns ok, errors and warnings.",
        "inputSchema": {"type": "object", "properties": {"workflow": _WORKFLOW_PARAM}, "required": ["workflow"]}}),
    "csflow_run": (tool_run, {
        "description": ("Run a CSFlow workflow in the background and wait up to wait_seconds. If it is not finished, "
                        "status is 'running' with a run_id: call csflow_status until status is 'done' or 'error'. "
                        "One workflow at a time. Workflows with a human approval step are refused."),
        "inputSchema": {"type": "object", "properties": {"workflow": _WORKFLOW_PARAM, "wait_seconds": _WAIT_PARAM},
                        "required": ["workflow"]}}),
    "csflow_status": (tool_status, {
        "description": "Wait up to wait_seconds for a running workflow, then return its state, steps and result. Without run_id: the latest run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string", "description": "Id returned by csflow_run"},
                                                         "wait_seconds": _WAIT_PARAM}}}),
}


# ── protocole JSON-RPC (stdio) ────────────────────────────────────────────────
_out = None
_out_lock = threading.Lock()


def _send(message: dict) -> None:
    data = (json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
    with _out_lock:
        _out.write(data)
        _out.flush()


def _reply(msg_id, result=None, error=None) -> None:
    body = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result if result is not None else {}
    _send(body)


def _handle(msg: dict) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if method is None:                                   # réponse du client : rien à faire
        return
    if msg_id is None:                                   # notification (initialized, cancelled…)
        return

    if method == "initialize":
        wanted = params.get("protocolVersion")
        _reply(msg_id, {
            "protocolVersion": wanted if wanted in SUPPORTED else SUPPORTED[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "csflow", "version": VERSION},
            "instructions": "Run CSFlow workflows: csflow_list, csflow_check, csflow_run, then csflow_status until done.",
        })
    elif method == "ping":
        _reply(msg_id, {})
    elif method == "tools/list":
        _reply(msg_id, {"tools": [{"name": n, **spec} for n, (_, spec) in TOOLS.items()]})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        entry = TOOLS.get(name)
        if entry is None:
            _reply(msg_id, error={"code": -32602, "message": f"unknown tool '{name}'"})
            return
        try:
            payload = entry[0](args if isinstance(args, dict) else {})
            text, is_error = json.dumps(payload, ensure_ascii=False, indent=1), False
        except ToolError as exc:
            text, is_error = f"Error: {exc}", True
        except Exception as exc:                         # noqa: BLE001
            print(f"[mcp] {name} a échoué : {exc!r}", file=sys.stderr, flush=True)
            text, is_error = f"Error: {type(exc).__name__}: {exc}", True
        _reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
    else:
        _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def serve(workflows_dir) -> int:
    """Boucle principale : lit stdin jusqu'à sa fermeture. Retourne le code de sortie."""
    global WORKFLOWS, _out
    WORKFLOWS = Path(workflows_dir)

    # stdout = protocole seulement. Le moteur et les nœuds impriment : on les envoie sur stderr.
    try:
        _out = open(1, "wb", closefd=False)
        stdin = open(0, "rb", closefd=False)
    except OSError:                                    # descripteurs absents (exe sans console)
        _out, stdin = sys.stdout.buffer, sys.stdin.buffer
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stderr

    for raw in stdin:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        for item in (message if isinstance(message, list) else [message]):
            if isinstance(item, dict):
                try:
                    _handle(item)
                except Exception as exc:                 # noqa: BLE001
                    print(f"[mcp] erreur interne : {exc!r}", file=sys.stderr, flush=True)
                    if item.get("id") is not None:
                        _reply(item["id"], error={"code": -32603, "message": "internal error"})
    # stdin ferme : sortie immediate. Un workflow encore en cours (thread ou processus fils)
    # ne doit pas retenir le serveur, sinon CSAgent croit qu'il ne s'arrete pas.
    for stream in (_out, sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:                                 # noqa: BLE001
            pass
    os._exit(0)
