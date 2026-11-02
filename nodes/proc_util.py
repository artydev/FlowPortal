"""nodes/proc_util.py — lancer un processus, avec délai maximal et arrêt à la demande.

`run()` remplace `subprocess.run(..., capture_output=True, timeout=...)` pour les nœuds `shell` et `python` :
même résultat (CompletedProcess) et même exception à l'expiration du délai, mais le processus peut aussi
être arrêté, avec ses processus fils, quand l'événement `cancel` est levé (bouton « Arrêter » du portail).
"""

import os
import signal
import subprocess
import sys
import time

_POLL = 0.2


class Cancelled(Exception):
    """Le processus a été arrêté à la demande de l'utilisateur."""


def _kill(proc: subprocess.Popen) -> None:
    """Arrête le processus et ses fils."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:                                  # noqa: BLE001 — repli : au moins le processus lui-même
        pass
    try:
        proc.kill()
    except Exception:                                  # noqa: BLE001
        pass


def run(cmd, *, timeout: float, cancel=None, **kwargs) -> subprocess.CompletedProcess:
    """Comme subprocess.run(capture_output=True, timeout=...), arrêtable par `cancel` (threading.Event).

    Lève subprocess.TimeoutExpired si le délai est dépassé, Cancelled si `cancel` est levé.
    """
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    if sys.platform != "win32":
        kwargs.setdefault("start_new_session", True)   # un groupe de processus à part : on peut tout arrêter
    proc = subprocess.Popen(cmd, **kwargs)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=_POLL)
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    _kill(proc)
                    proc.communicate()
                    raise Cancelled()
                if time.monotonic() >= deadline:
                    _kill(proc)
                    proc.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout)
    except BaseException:
        if proc.poll() is None:
            _kill(proc)
            proc.communicate()
        raise
