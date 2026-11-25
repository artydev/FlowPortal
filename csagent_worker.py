"""
csagent_worker.py — FlowPortal démarre et arrête lui-même le worker CSAgent (mode API).

Avant : l'utilisateur lançait start_agent.bat / .sh, qui fixait le port (8080) et la clé (demo-secret).
Maintenant : au lancement, FlowPortal cherche CSAgent, choisit un port libre, génère une clé, lance
`csagent --api` dans son propre dossier (CSAgent n'écrit que là : il partage output/ et documents/ avec
FlowPortal), attend qu'il réponde, puis l'arrête à la fermeture.

Mode « externe » : si CSAGENT_URL est défini (cas des scripts start_agent / start_csagent, ou d'un CSAgent
lancé à la main), FlowPortal n'en démarre pas un second ; CSFLOW_NO_AGENT=1 fait de même.

États (`status()["state"]`) :
    starting  le worker est lancé, il ne répond pas encore
    ready     le worker répond
    external  CSAgent est géré hors de FlowPortal (CSAGENT_URL, CSFLOW_NO_AGENT)
    missing   exécutable CSAgent introuvable
    failed    le worker n'a pas démarré, ou s'est arrêté
    stopped   arrêté par FlowPortal (fermeture)
"""
from __future__ import annotations

import atexit
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import nodes.agent_node as agent_node


# ── mémoire du worker ─────────────────────────────────────────────────────────
# Chaque lancement de FlowPortal démarre un worker à la mémoire vierge : une mémoire fixe héritait des
# échanges des sessions précédentes (étapes agent qui dérivent, résultats non reproductibles, contexte gaspillé).
MEMORY_PREFIX = "csflow_worker"
KEEP_MEMORIES = 5                                   # anciennes mémoires gardées pour inspection (issue #8)
_MEMORY_RE = re.compile(r"^csflow_worker(_\d{8}-\d{6}(_[0-9a-f]+)?)?$")


def new_session_memory() -> str:
    """Nom d'une mémoire neuve pour cette session : csflow_worker_AAAAMMJJ-HHMMSS_xxxx (triable par date)."""
    return f"{MEMORY_PREFIX}_{datetime.now():%Y%m%d-%H%M%S}_{secrets.token_hex(2)}"


def prune_old_memories(folder: Path, keep: int = KEEP_MEMORIES, current: str = "") -> list[str]:
    """Supprime les plus anciennes mémoires de worker de `folder`, en gardant les `keep` plus récentes.

    Ne touche qu'aux dossiers dont le nom suit exactement le motif des mémoires de worker, jamais à la
    mémoire `current` ni à un fichier. Rend les noms supprimés."""
    try:
        found = sorted(p.name for p in Path(folder).iterdir() if p.is_dir() and _MEMORY_RE.match(p.name))
    except OSError:
        return []
    found = [name for name in found if name != current]
    removed = found[:max(0, len(found) - max(0, keep - 1))] if keep > 0 else found
    for name in removed:
        shutil.rmtree(Path(folder) / name, ignore_errors=True)
    return removed

START_TIMEOUT = 45.0          # secondes d'attente maximale avant de déclarer l'échec du démarrage
STOP_GRACE = 4.0              # secondes accordées à l'arrêt « poli » avant de forcer
_IS_WINDOWS = sys.platform.startswith("win")


def free_port() -> int:
    """Port TCP libre, choisi par le système."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(url: str, timeout: float = 1.0) -> bool:
    """Quelque chose écoute-t-il à cette adresse ? (simple connexion TCP, sans requête d'API)."""
    target = urlparse(url)
    try:
        with socket.create_connection((target.hostname or "localhost", target.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def find_binary(app_dir: Path, res_dir: Path | None = None) -> list | None:
    """Commande de CSAgent : CSAGENT_BIN, sinon à côté de FlowPortal, sinon dans le PATH."""
    names = ("csagent.exe", "csagent") if _IS_WINDOWS else ("csagent",)
    wanted = os.environ.get("CSAGENT_BIN", "").strip()
    if wanted:
        found = shutil.which(wanted) or (wanted if Path(wanted).is_file() else None)
        return [found] if found else None
    for folder in {app_dir, res_dir or app_dir}:
        for sub in ("", "csagent"):
            for name in names:
                candidate = folder / sub / name if sub else folder / name
                if candidate.is_file():
                    return [str(candidate)]
    found = shutil.which("csagent")
    return [found] if found else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _IS_WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_text(pid: int) -> str:
    """Nom / ligne de commande du processus, en minuscules ('' si inconnu) : sert à ne tuer que CSAgent."""
    try:
        if _IS_WINDOWS:
            return subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                  capture_output=True, text=True, creationflags=0x08000000).stdout.lower()
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").lower()
    except OSError:
        return ""


def _kill_tree(pid: int, force: bool) -> None:
    if _IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []),
                       capture_output=True, creationflags=0x08000000)
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        # Le groupe n'est visé que si ce processus en est le chef (lancé par nous avec start_new_session) :
        # sinon on tuerait aussi FlowPortal, qui partage son groupe.
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class AgentWorker:
    """Le worker CSAgent géré par FlowPortal. Un seul par processus."""

    def __init__(self, app_dir: Path, res_dir: Path | None = None, command: list | None = None):
        self.app_dir = Path(app_dir)
        self.res_dir = Path(res_dir) if res_dir else self.app_dir
        self._command = command                     # imposé par les tests ; sinon find_binary()
        self.log_path = self.app_dir / "csagent.log"
        self.pid_path = self.app_dir / "csagent.pid"
        self.state = "idle"
        self.message = ""
        self.url = agent_node.current_url()
        self.key = agent_node.current_key()
        self.memory = ""                            # mémoire neuve de la session en cours (--mem)
        self.managed = False                        # vrai : c'est FlowPortal qui a lancé le worker
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._settled = threading.Event()           # levé quand l'état n'est plus « starting »
        self._stopping = False
        self._registered = False

    # ── démarrage ──────────────────────────────────────────────────────────────
    def start_async(self) -> str:
        """Décide du mode puis, si besoin, lance le worker en arrière-plan. Rend l'état tout de suite."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return self.state
            self._settled.clear()
            self._stopping = False
            if os.environ.get("CSFLOW_NO_AGENT") == "1" or os.environ.get("CSAGENT_URL", "").strip():
                self._set("external", "CSAgent est géré en dehors de FlowPortal (CSAGENT_URL).")
                return self.state
            command = self._command or find_binary(self.app_dir, self.res_dir)
            if not command:
                self._set("missing", "CSAgent est introuvable : placez csagent à côté de FlowPortal, "
                                     "ajoutez-le au PATH ou indiquez son chemin avec la variable CSAGENT_BIN.")
                return self.state
            self._kill_stale()
            port, self.key = free_port(), secrets.token_hex(24)
            self.url = f"http://localhost:{port}"
            agent_node.configure(self.url, self.key)
            args = list(command) + ["--api", "--yes", "--port", str(port), "--api-key", self.key]
            model = os.environ.get("CSAGENT_MODEL", "").strip()
            if model:
                args += ["--model", model]
            self.memory = new_session_memory()
            prune_old_memories(self.app_dir, current=self.memory)
            args += ["--no-route", "--mem", self.memory, "--no-distill"]
            try:
                log = open(self.log_path, "wb")
                kwargs = {"creationflags": 0x08000000 | 0x00000200} if _IS_WINDOWS else {"start_new_session": True}
                self._proc = subprocess.Popen(args, cwd=str(self.app_dir), stdin=subprocess.DEVNULL,
                                              stdout=log, stderr=subprocess.STDOUT, **kwargs)
            except OSError as exc:
                self._set("failed", f"CSAgent n'a pas pu être lancé : {exc}")
                return self.state
            finally:
                try:
                    log.close()
                except Exception:
                    pass
            self.managed = True
            self.pid_path.write_text(json.dumps({"pid": self._proc.pid, "port": port}), encoding="utf-8")
            if not self._registered:
                atexit.register(self.stop)
                self._registered = True
            self._set("starting", "Démarrage de CSAgent…")
            agent_node.READY_WAIT = self.wait_ready
            threading.Thread(target=self._watch_start, args=(self._proc, port), daemon=True).start()
            return self.state

    def _watch_start(self, proc: subprocess.Popen, port: int) -> None:
        timeout = float(os.environ.get("CSFLOW_AGENT_START_TIMEOUT") or START_TIMEOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                self._set("failed", f"CSAgent s'est arrêté au démarrage (code {proc.returncode}).{self._log_tail()}")
                return
            if port_open(f"http://localhost:{port}", 0.3):
                self._set("ready", "CSAgent actif.")
                return
            time.sleep(0.2)
        self.stop()
        self._set("failed", f"CSAgent ne répond pas après {int(timeout)} s.{self._log_tail()}")

    def _log_tail(self, lines: int = 6) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        text = [t for t in text if t.strip()][-lines:]
        return ("\nDernières lignes de csagent.log :\n" + "\n".join(text)) if text else ""

    def _set(self, state: str, message: str) -> None:
        self.state, self.message = state, message
        if state != "starting":
            self._settled.set()

    # ── attente par les étapes « agent » ───────────────────────────────────────
    def wait_ready(self, cancel=None, timeout: float = 60.0):
        """Bloque tant que le worker démarre. Rend (True, '') s'il est prêt (ou externe), sinon (False, raison)."""
        end = time.time() + timeout
        while self.state == "starting" and time.time() < end:
            if cancel is not None and cancel.is_set():
                return False, "Arrêté par l'utilisateur"
            self._settled.wait(0.2)
        if self.state in ("ready", "external"):
            return True, ""
        if self.state == "starting":
            return False, "CSAgent démarre encore : réessayez dans un instant."
        return False, self.message

    # ── état ───────────────────────────────────────────────────────────────────
    def status(self) -> dict:
        """État pour l'interface. Détecte un worker mort après coup."""
        if self.managed and self.state == "ready" and self._proc is not None and self._proc.poll() is not None:
            self._set("failed", f"CSAgent s'est arrêté (code {self._proc.returncode}).{self._log_tail()}")
        url = agent_node.current_url()
        return {"state": self.state, "message": self.message, "url": url, "managed": self.managed,
                "reachable": port_open(url, 1.5)}

    # ── arrêt ──────────────────────────────────────────────────────────────────
    def stop(self) -> None:
        """Arrête le worker (et ses processus enfants). Sans effet si FlowPortal ne l'a pas lancé."""
        proc = self._proc
        if proc is None or self._stopping:
            return
        self._stopping = True
        if proc.poll() is None:
            _kill_tree(proc.pid, force=False)
            try:
                proc.wait(STOP_GRACE)
            except subprocess.TimeoutExpired:
                _kill_tree(proc.pid, force=True)
                try:
                    proc.wait(2)
                except subprocess.TimeoutExpired:
                    pass
        try:
            self.pid_path.unlink()
        except OSError:
            pass
        if self.state in ("starting", "ready"):
            self._set("stopped", "CSAgent arrêté.")

    def restart(self) -> str:
        self.stop()
        self._proc, self.managed = None, False
        return self.start_async()

    def _kill_stale(self) -> None:
        """Un worker laissé par un plantage précédent : on ne tue que ce qui ressemble bien à CSAgent."""
        try:
            info = json.loads(self.pid_path.read_text(encoding="utf-8"))
            pid = int(info["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        try:
            if _pid_alive(pid) and "csagent" in _process_text(pid):
                _kill_tree(pid, force=False)
                time.sleep(0.5)
                if _pid_alive(pid):
                    _kill_tree(pid, force=True)
        finally:
            try:
                self.pid_path.unlink()
            except OSError:
                pass


def install_signal_handlers(worker: AgentWorker) -> None:
    """Ctrl-C, fermeture de la console, arrêt système : le worker s'arrête avec FlowPortal."""
    def _bye(signum, frame):
        worker.stop()
        raise SystemExit(0)
    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _bye)
            except (ValueError, OSError):
                pass
