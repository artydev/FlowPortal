#!/usr/bin/env python3
"""
app.py — EEL desktop UI pour CSFlow
─────────────────────────────────────
Requirements:
    pip install eel pyyaml

Usage (depuis le dossier workflow/) :
    python app.py
"""

import os
import shutil
import sys
import threading
from pathlib import Path

# Mode « interpréteur » : l'exe se rappelle lui-même pour exécuter un script Python
# (nœud `python`) — aucun Python installé requis. Doit passer avant tout le reste.
if len(sys.argv) > 2 and sys.argv[1] == "--csflow-python":
    from nodes.python_node import run_python_cli
    sys.exit(run_python_cli(sys.argv[2:]))


def _app_dirs() -> tuple:
    """(RES_DIR, APP_DIR)
    RES_DIR : ressources livrées avec le programme (ui, workflows d'exemple), en lecture seule.
    APP_DIR : dossier des données de l'utilisateur (workflows, tmp), qui doit survivre à la fermeture.

    Depuis les sources, les deux sont le dossier de app.py. Avec un exécutable Nuitka --onefile,
    __file__ pointe vers un dossier TEMPORAIRE (extrait au lancement, supprimé à la sortie) :
    les données de l'utilisateur vivent à côté de l'exe (__compiled__.containing_dir).
    PyInstaller : même idée avec sys.executable."""
    res = Path(__file__).resolve().parent
    try:
        return res, Path(__compiled__.containing_dir).resolve()   # noqa: F821 — défini par Nuitka
    except NameError:
        pass
    if getattr(sys, "frozen", False):                              # PyInstaller
        return res, Path(sys.executable).resolve().parent
    return res, res


RES_DIR, APP_DIR = _app_dirs()


def _seed(name: str) -> None:
    """Premier lancement : copie à côté de l'exe ce qui est embarqué (sans jamais écraser)."""
    src, dst = RES_DIR / name, APP_DIR / name
    if RES_DIR == APP_DIR or not src.exists() or dst.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


for _name in ("workflows", "csflow.schema.json", "CSFLOW_LLM_GUIDE.md", "REFERENCE.md"):
    _seed(_name)
(APP_DIR / "tmp").mkdir(exist_ok=True)

# Les chemins relatifs des workflows (tmp\...) sont résolus depuis ce dossier,
# quel que soit l'endroit d'où app.py est lancé. CSAgent doit tourner depuis ici aussi.
os.chdir(APP_DIR)

# Mode « serveur MCP » (stdio) : CSAgent lance lui-même `CSFlow --mcp` pour piloter les workflows
# sans interface. Doit passer avant `import eel` : aucune interface n'est démarrée.
if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
    from mcp_server import serve
    sys.exit(serve(APP_DIR / "workflows"))

try:
    import eel
except ImportError:
    print("Missing dependency — run:  pip install eel")
    raise

try:
    import yaml
except ImportError:
    print("Missing dependency — run:  pip install pyyaml")
    raise

from engine import WorkflowEngine
import audio_client

WORKFLOWS_DIR = APP_DIR / "workflows"

eel.init(str(RES_DIR / "ui"))

# ── human approval gate ───────────────────────────────────────────────────────
_approval_event:  threading.Event | None = None
_approval_result: dict = {}

# ── saisie de texte (étape user_input) ────────────────────────────────────────
_input_event:   threading.Event | None = None
_input_result:  dict = {}
_input_request: dict = {}


# ── fonctions exposées au JS ──────────────────────────────────────────────────

@eel.expose
def list_workflows() -> list:
    files = sorted(WORKFLOWS_DIR.glob("*.yaml"))
    result = []
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                wf = yaml.safe_load(fh)
            result.append({
                "filename":    f.name,
                "path":        str(f),
                "name":        wf.get("name", f.stem),
                "description": wf.get("description", ""),
            })
        except Exception:
            result.append({"filename": f.name, "path": str(f),
                           "name": f.stem, "description": ""})
    return result


# Clés lues par le moteur : tout le reste est un paramètre propre au type d'étape
_STEP_META = {"id", "type", "description", "next", "on_approve", "on_reject",
              "skip_if", "continue_on_error"}


def _step_lines(text: str) -> list:
    """[(début, fin)] : lignes (à partir de 1) occupées par chaque étape dans le fichier."""
    try:
        root = yaml.compose(text)
        lines = text.splitlines()
        for key, node in root.value:
            if key.value == "steps" and isinstance(node, yaml.SequenceNode):
                spans = []
                for item in node.value:
                    start = item.start_mark.line + 1
                    end = min(item.end_mark.line, len(lines))
                    # le bloc YAML s'arrête avant le prochain jeton : on retire les
                    # lignes vides / commentaires qui précèdent l'étape suivante
                    while end > start and (not lines[end - 1].strip()
                                           or lines[end - 1].lstrip().startswith("#")):
                        end -= 1
                    spans.append((start, max(end, start)))
                return spans
    except Exception:
        pass
    return []


@eel.expose
def load_workflow(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        wf = yaml.safe_load(text)
        spans = _step_lines(text)
        steps = []
        for i, step in enumerate(wf.get("steps", []), 1):
            span = spans[i - 1] if i - 1 < len(spans) else (0, 0)
            steps.append({
                "index":       i,
                "id":          step.get("id", f"step_{i}"),
                "type":        step.get("type", "unknown"),
                "description": step.get("description", ""),
                "prompt":      step.get("prompt", ""),
                "message":     step.get("message", ""),
                "next":        step.get("next", ""),
                "on_approve":  step.get("on_approve", ""),
                "on_reject":   step.get("on_reject", ""),
                "skip_if":     step.get("skip_if", ""),
                "continue_on_error": bool(step.get("continue_on_error", False)),
                # paramètres propres au type (cmd, path, prompt…), en texte
                "params":      {k: ("" if v is None else str(v).rstrip())
                                for k, v in step.items() if k not in _STEP_META},
                "line_start":  span[0],
                "line_end":    span[1],
            })
        return {
            "ok":          True,
            "name":        wf.get("name", Path(path).stem),
            "description": wf.get("description", ""),
            "steps":       steps,
            "yaml":        text,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@eel.expose
def run_workflow(path: str) -> dict:
    global _approval_event, _approval_result, _input_event, _input_result, _input_request
    _approval_event  = None
    _approval_result = {}
    _input_event, _input_result, _input_request = None, {}, {}

    def on_event(event: dict):
        try:
            eel.receiveEvent(event)    # Python → JS direct
        except Exception as exc:
            print(f"[EEL] receiveEvent échoué : {exc}", flush=True)

    def wait_approval() -> str:
        global _approval_event, _approval_result
        ev     = threading.Event()
        result = {}
        _approval_event  = ev
        _approval_result = result
        ev.wait(timeout=600)
        return result.get("answer", "rejected")

    def wait_input(request: dict) -> dict:
        global _input_event, _input_result, _input_request
        ev     = threading.Event()
        result = {}
        _input_event, _input_result, _input_request = ev, result, dict(request)
        ev.wait(timeout=600)
        return result or {"cancelled": True}

    _wf_done = threading.Event()

    def _run():
        try:
            engine = WorkflowEngine(path,
                                    event_callback=on_event,
                                    approval_wait=wait_approval,
                                    input_wait=wait_input)
            engine.run()
        except Exception as exc:
            try:
                eel.receiveEvent({"type": "workflow_error", "message": str(exc)})
            except Exception:
                print(f"[ENGINE] erreur : {exc}", flush=True)
        finally:
            _wf_done.set()

    def _watchdog():
        """Timeout 10 min — émet workflow_error si le thread engine se bloque."""
        if not _wf_done.wait(timeout=600):
            try:
                eel.receiveEvent({"type": "workflow_error",
                                  "message": "Timeout : workflow bloqué > 10 min"})
            except Exception:
                pass

    threading.Thread(target=_run,      daemon=True).start()
    threading.Thread(target=_watchdog, daemon=True).start()
    return {"status": "started"}


@eel.expose
def dev_mode() -> bool:
    """CSFLOW_DEBUG=1 : garde la console de développement du navigateur (F12, clic droit › Inspecter)."""
    return os.environ.get("CSFLOW_DEBUG", "") not in ("", "0")


@eel.expose
def resolve_approval(answer: str) -> dict:
    global _approval_event, _approval_result
    if _approval_event:
        _approval_result["answer"] = answer
        _approval_event.set()
        _approval_event = None
    return {"ok": True}


def _remove_audio(*paths) -> None:
    """Supprime l'enregistrement et sa transcription, seulement dans recordings/ et transcripts/."""
    for p in paths:
        q = str(p or "").replace("\\", "/")
        if q.startswith(("recordings/", "transcripts/")) and ".." not in q:
            try:
                os.remove(q)
            except OSError:
                pass


@eel.expose
def audio_upload(b64: str, mime: str) -> dict:
    """Dictée : l'interface envoie l'enregistrement (base64) ; la transcription arrive par événements."""
    import base64
    req = dict(_input_request)
    if not req.get("voice"):
        return {"ok": False, "error": "La dictée n'est pas activée pour cette étape."}
    try:
        audio = base64.b64decode(b64 or "", validate=False)
    except Exception:
        return {"ok": False, "error": "Audio illisible."}
    if not audio:
        return {"ok": False, "error": "Enregistrement vide."}

    def _work():
        try:
            text, audio_path, saved = audio_client.transcribe(
                req["csagent_url"], req["csagent_key"], audio, mime, req.get("language", "fr"),
                progress=lambda part, total: eel.receiveEvent({"type": "user_input_progress", "part": part, "total": total}))
            if not req.get("keep_audio"):
                _remove_audio(audio_path, saved)
            eel.receiveEvent({"type": "user_input_transcribed", "text": text})
        except audio_client.AudioError as exc:
            eel.receiveEvent({"type": "user_input_error", "message": str(exc)})
        except Exception as exc:                                   # noqa: BLE001
            eel.receiveEvent({"type": "user_input_error", "message": "Transcription impossible : %s" % exc})

    threading.Thread(target=_work, daemon=True).start()
    return {"ok": True}


@eel.expose
def submit_input(text: str, cancelled: bool = False) -> dict:
    """Réponse de l'interface à une étape user_input."""
    global _input_event
    if _input_event:
        _input_result["text"] = str(text or "")
        if cancelled:
            _input_result["cancelled"] = True
        _input_event.set()
        _input_event = None
    return {"ok": True}


# ── entry point ───────────────────────────────────────────────────────────────

def _free_port() -> int:
    """Port libre choisi par le système : deux instances (ou une instance restée ouverte) ne se gênent pas."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _pick_browser():
    """Edge (toujours présent sous Windows 10/11), sinon Chrome, sinon None (navigateur par défaut)."""
    try:
        import eel.browsers as _b
        for mode in ("edge", "chrome"):
            if _b._browser_modules[mode].find_path():
                return mode
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # UNE seule fenêtre. Eel ouvre le navigateur AVANT de démarrer le serveur : avec l'ancienne boucle
    # « essayer chrome, puis edge, puis le navigateur par défaut », la moindre erreur système au démarrage
    # du serveur (port occupé…) relançait la tentative suivante et ouvrait une 2e fenêtre.
    _port = int(os.environ.get("CSFLOW_PORT") or 0) or _free_port()
    _url = f"http://localhost:{_port}/index.html"
    _headless = os.environ.get("CSFLOW_NO_BROWSER") == "1"      # WSL / serveur : on ouvre l'URL soi-même
    _windows = sys.platform.startswith("win")
    _mode = None if (_headless or not _windows) else _pick_browser()
    print(f"CSFlow : {_url}", flush=True)
    # Windows : Eel ouvre Edge ou Chrome. Linux / WSL / macOS : ui_launcher choisit (Chromium, navigateur de
    # Windows sous WSL, Firefox…) et évite les navigateurs qui affichent mal la page (Epiphany).
    eel.start("index.html", mode=_mode or False, port=_port, size=(1440, 820), block=False)
    if not _headless:
        if _windows:
            if _mode is None:
                import webbrowser
                webbrowser.open(_url)
        else:
            from ui_launcher import open_ui, HINT
            eel.sleep(0.5)                # laisse le serveur démarrer avant d'ouvrir la page
            if open_ui(_url, (1440, 820)) == "none":
                print(HINT, flush=True)
    while True:                      # eel.sleep (et non time.sleep) : le serveur tourne dans un greenlet
        eel.sleep(1.0)
