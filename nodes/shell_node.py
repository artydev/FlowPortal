"""nodes/shell_node.py — run a shell command."""

import os
import shutil
import subprocess
import sys
from . import register


def _decode(raw: bytes) -> str:
    """Decode process output: UTF-8 first, then the console's OEM code page on Windows."""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("oem" if sys.platform == "win32" else "latin-1", errors="replace")


@register("shell", summary="Exécute une commande shell (cmd.exe sous Windows, bash ailleurs).", params={
    "cmd":     ("commande exécutée par cmd.exe (Windows) ou bash ; sorties : output, stdout, stderr, returncode", True),
    "timeout": ("secondes avant arrêt du processus (défaut 60)", False),
    "shell":   ("shell explicite : « bash », « sh » ou « pwsh » (défaut : le shell du système)", False),
})
def shell(params: dict) -> dict:
    """
    Execute a shell command (bash on Unix, cmd.exe on Windows).

    Params:
        cmd     (str): the command to run
        timeout (int): seconds before the process is killed (default 60)
        shell   (str): force a specific shell ("bash", "sh" or "pwsh") instead of
                       the platform default — useful for cross-platform workflows

    Output fields:
        output:     stdout + stderr combined
        stdout:     stdout only
        stderr:     stderr only
        returncode: exit code as int (0 = success)

    Status is "ok" only when returncode == 0.
    Set `continue_on_error: true` in the step to keep the workflow running
    even when the command fails (useful for capturing the error in a later step).

    Windows notes:
      - Python child processes are forced to UTF-8 (PYTHONUTF8 / PYTHONIOENCODING).
      - Output of native cmd commands (dir, type, ...) is decoded with the OEM code page.
      - In YAML use single quotes for commands containing backslashes:
            cmd: 'python tmp\\script.py'
    """
    cmd     = params.get("cmd", "")
    timeout = int(params.get("timeout", 60))

    forced = str(params.get("shell", "")).strip().lower()
    if forced:
        if forced not in ("bash", "sh", "pwsh"):
            return {"output": f"shell « {forced} » non pris en charge (valeurs : bash, sh, pwsh)",
                    "returncode": -1, "status": "error"}
        exe = shutil.which(forced)
        if not exe:
            return {"output": f"shell « {forced} » introuvable sur cette machine",
                    "returncode": -1, "status": "error"}
        command, use_shell = [exe, "-c", cmd], False
    else:
        command, use_shell = cmd, True

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    extra = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.run(
            command,
            shell=use_shell,
            capture_output=True,       # bytes — decoded below
            timeout=timeout,
            env=env,
            **extra,
        )
        stdout   = _decode(proc.stdout)
        stderr   = _decode(proc.stderr)
        combined = (stdout + stderr).strip()
        status   = "ok" if proc.returncode == 0 else "error"
        return {
            "output":     combined,
            "stdout":     stdout.strip(),
            "stderr":     stderr.strip(),
            "returncode": proc.returncode,   # int — use  {{step.returncode}} == 0
            "status":     status,
        }
    except subprocess.TimeoutExpired:
        return {
            "output":     f"Command timed out after {timeout}s",
            "returncode": -1,
            "status":     "error",
        }
    except Exception as exc:
        return {"output": str(exc), "returncode": -1, "status": "error"}
