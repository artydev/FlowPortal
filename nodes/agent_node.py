"""
nodes/agent_node.py — CSAgent node
────────────────────────────────────
Sends a natural-language prompt to a running CSAgent --api instance and
streams the SSE events back, printing thoughts, tool calls, and results
in real time.

CSAgent must be started separately (from the workflow/ directory):
    csagent --api --yes --port 8080 --api-key demo-secret

Confirmations
─────────────
Without --yes, CSAgent sends a `confirm` event before every destructive tool
(write_file, edit_file, ...) and WAITS for `POST /api/confirm` (body true/false).
This node answers it:
  • default            → asks the human (UI approval bar, or terminal y/n)
  • auto_confirm: true → approves automatically (same as starting CSAgent with --yes)

Default connection settings can be overridden per-step:
    csagent_url:  http://localhost:9090
    csagent_key:  my-other-key
    auto_confirm: true
"""

import json
import os
import socket
import threading
import time
from urllib.request import urlopen, Request
from urllib.error   import URLError, HTTPError
from urllib.parse   import quote
from . import register

# ── defaults (match start_agent scripts) ──────────────────────────────────────
DEFAULT_URL = "http://localhost:8080"
DEFAULT_KEY = "demo-secret"

IDLE_NOTICE_EVERY = 15     # seconds of silence before a "still waiting" notice

# ── ANSI colours (re-declared locally so this module is self-contained) ───────
_RST = "\033[0m"; _DIM = "\033[2m"; _CYN = "\033[96m"
_BLU = "\033[94m"; _GRN = "\033[92m"; _RED = "\033[91m"; _YLW = "\033[93m"


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _fmt_args(a, limit: int = 90) -> str:
    """CSAgent sends call arguments as a JSON *string*: show it compactly."""
    text = a if isinstance(a, str) else json.dumps(a or {}, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "…"


def _describe_call(tool: str, last_call: dict | None) -> str:
    """Short human label for the tool awaiting confirmation (e.g. its path)."""
    detail = ""
    if last_call and last_call.get("n") == tool:
        a = last_call.get("a")
        try:
            args = json.loads(a) if isinstance(a, str) else (a or {})
        except Exception:
            args = {}
        if isinstance(args, dict):
            for k in ("path", "file", "cmd", "command", "name"):
                if args.get(k):
                    detail = " ".join(str(args[k]).split())[:80]
                    break
    return f" sur {detail}" if detail else ""


def _post_confirm(url: str, key: str, approved: bool):
    """Answer a pending `confirm` event. Returns (ok, error_message)."""
    req = Request(
        f"{url}/api/confirm",
        data=b"true" if approved else b"false",
        method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=15) as resp:
            resp.read()
        return True, ""
    except Exception as exc:                                   # noqa: BLE001
        return False, str(exc)


@register("agent", summary="Envoie une instruction à CSAgent et suit son raisonnement en direct ; sortie : sa dernière réponse.", params={
    "prompt":       ("instruction en langage naturel envoyée à CSAgent", True),
    "csagent_url":  ("URL de CSAgent (défaut http://localhost:8080)", False),
    "csagent_key":  ("clé d'API (défaut demo-secret)", False),
    "timeout":      ("secondes sans donnée avant abandon (défaut 180)", False),
    "auto_confirm": ("true = approuver automatiquement les demandes de confirmation de CSAgent", False),
})
def agent(params: dict) -> dict:
    """
    Send a prompt to CSAgent and stream its response.

    Params:
        prompt       (str):  natural-language instruction for the agent
        csagent_url  (str):  override the default CSAgent URL
        csagent_key  (str):  override the default API key
        timeout      (int):  max seconds without data from CSAgent (default 180)
        auto_confirm (bool): approve CSAgent's `confirm` requests automatically
                             (default false → ask the human). Also enabled by the
                             environment variable CSFLOW_AUTO_CONFIRM=1.

    Output fields:
        output:   last thought the agent produced (its final answer)
        thoughts: all thoughts joined with newlines
    """
    prompt   = params.get("prompt", "").strip()
    url      = params.get("csagent_url", DEFAULT_URL)
    key      = params.get("csagent_key", DEFAULT_KEY)
    timeout  = int(params.get("timeout", 180))
    auto     = _truthy(params.get("auto_confirm", os.environ.get("CSFLOW_AUTO_CONFIRM", "0")))
    # Injected by the engine when a UI is attached.
    emit     = params.get("_callback")        # callback({"type": "agent_*", ...})
    wait_fn  = params.get("_wait_approval")   # blocks until the UI answers

    endpoint = f"{url}/api/chat?prompt={quote(prompt, safe='')}"
    req      = Request(endpoint, headers={"Authorization": f"Bearer {key}"})

    thoughts: list[str] = []
    last_call: dict | None = None

    def _print_or_emit(event: dict):
        """Forward to UI callback if present, otherwise print to terminal."""
        if emit:
            emit(event)
            return
        etype = event.get("type", "")
        data  = event.get("data", "")
        if etype == "agent_thought":
            text = str(data)
            if len(text) > 220:
                text = text[:217] + "…"
            for tline in text.splitlines():
                if tline.strip():
                    print(f"  {_CYN}💭 {tline}{_RST}")
        elif etype == "agent_call":
            tool = data.get("n", "?")
            print(f"  {_BLU}🔧 {tool}{_RST}  {_DIM}{_fmt_args(data.get('a', {}))}{_RST}")
        elif etype == "agent_result":
            ok      = data.get("ok", True)
            snippet = str(data.get("output", ""))[:110].replace("\n", " ")
            col = _GRN if ok else _RED
            print(f"  {col}{'✓' if ok else '✗'}  {snippet}{_RST}")
        elif etype == "agent_model":
            print(f"  {_DIM}[model: {data}]{_RST}")
        elif etype == "agent_warning":
            print(f"  {_YLW}⚠  {data}{_RST}")

    # ── silence watcher: tells the user CSAgent is still being waited on ──────
    last_event = [time.time()]
    awaiting   = [False]            # True while waiting for a human answer
    stop_idle  = threading.Event()

    def _idle_watch():
        while not stop_idle.wait(IDLE_NOTICE_EVERY):
            silent = int(time.time() - last_event[0])
            if not awaiting[0] and silent >= IDLE_NOTICE_EVERY:
                _print_or_emit({"type": "agent_warning",
                                "data": f"⏳ en attente de CSAgent depuis {silent}s…"})

    def _ask_confirm(tool: str) -> bool:
        """Answer a `confirm` event: auto, or ask the human."""
        what = f"« {tool} »{_describe_call(tool, last_call)}"
        if auto:
            _print_or_emit({"type": "agent_warning",
                            "data": f"✓ confirmation auto-approuvée : {what}"})
            return True

        msg = f"CSAgent demande l'autorisation d'exécuter {what} — approuver ?"
        if emit:
            emit({"type": "human_approval_required", "message": msg})
        else:
            print(f"\n  {_YLW}❓  {msg}{_RST}")

        if wait_fn:
            answer = wait_fn()                       # blocks until the UI resolves
        else:
            try:
                answer = "approved" if input("  Approve? [y/n]: ").strip().lower() == "y" \
                         else "rejected"
            except EOFError:
                answer = "rejected"
        if not emit:
            col = _GRN if answer == "approved" else _RED
            print(f"  {col}{'✓' if answer == 'approved' else '✗'}  {answer.capitalize()}{_RST}")
        return answer == "approved"

    threading.Thread(target=_idle_watch, daemon=True).start()

    try:
        with urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                last_event[0] = time.time()
                etype = ev.get("type", "")
                data  = ev.get("data", "")

                if etype == "thought":
                    thoughts.append(str(data))
                    _print_or_emit({"type": "agent_thought", "data": str(data)})

                elif etype == "call":
                    last_call = data if isinstance(data, dict) else None
                    _print_or_emit({"type": "agent_call", "data": data})

                elif etype == "result":
                    ok      = not data.get("e", False)
                    out_str = str(data.get("r", ""))
                    _print_or_emit({"type": "agent_result",
                                    "data": {"ok": ok, "output": out_str}})

                elif etype == "model":
                    _print_or_emit({"type": "agent_model", "data": data})

                elif etype in ("warning", "danger"):
                    _print_or_emit({"type": "agent_warning", "data": data})

                elif etype == "confirm":
                    tool = data.get("tool", "?") if isinstance(data, dict) else str(data)
                    awaiting[0] = True
                    try:
                        approved = _ask_confirm(tool)
                    finally:
                        awaiting[0] = False
                        last_event[0] = time.time()
                    ok, err = _post_confirm(url, key, approved)
                    if not ok:
                        return {"output": f"POST /api/confirm a échoué : {err}",
                                "status": "error"}

                elif etype == "step":
                    pass            # {"n": 1, "m": 30} — progression interne, sans intérêt ici

                elif etype == "done":
                    final = thoughts[-1] if thoughts else "done"
                    return {
                        "output":   final,
                        "thoughts": "\n".join(thoughts),
                        "status":   "ok",
                    }

                elif etype == "error":
                    return {"output": str(data), "status": "error"}

                else:               # événement inconnu : on l'affiche plutôt que de l'ignorer
                    _print_or_emit({"type": "agent_warning",
                                    "data": f"événement CSAgent « {etype} » : {str(data)[:120]}"})

        # Stream closed without a "done" event
        final = thoughts[-1] if thoughts else ""
        return {"output": final, "thoughts": "\n".join(thoughts), "status": "ok"}

    except (TimeoutError, socket.timeout):
        msg = (f"Aucune donnée de CSAgent depuis {timeout}s — abandon.\n"
               f"  → CSAgent attend peut-être une confirmation, ou le modèle ne répond plus.")
        _print_or_emit({"type": "agent_warning", "data": msg})
        return {"output": msg, "status": "error"}

    except HTTPError as exc:
        msg = f"CSAgent a répondu HTTP {exc.code} ({exc.reason}) — clé d'API ?"
        _print_or_emit({"type": "agent_warning", "data": msg})
        return {"output": msg, "status": "error"}

    except URLError as exc:
        msg = (
            f"Cannot reach CSAgent at {url}\n"
            f"  → {exc}\n"
            f"  → Is CSAgent running?  See start_agent.bat / start_agent.sh"
        )
        print(f"\n  {_RED}{msg}{_RST}\n")
        return {"output": str(exc), "status": "error"}

    finally:
        stop_idle.set()
