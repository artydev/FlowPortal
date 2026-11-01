"""nodes/python_node.py — run a Python script with the interpreter embedded in CSFlow.

No Python installation is needed on the machine: when CSFlow is compiled
(Nuitka / PyInstaller), the script is executed by the compiled program itself
through its hidden `--csflow-python` entry point (see app.py / run_python_cli).
When running from source, the current interpreter is used.
"""

import os
import subprocess
import sys
from . import register
from .shell_node import _decode

FLAG = "--csflow-python"


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _exe() -> str:
    """Chemin de l'exe compilé. En Nuitka onefile, sys.executable peut pointer vers un
    fichier absent : on essaie plusieurs sources et on garde la première qui existe."""
    comp = globals().get("__compiled__")
    cands = [
        os.environ.get("CSFLOW_EXE", ""),
        getattr(comp, "original_argv0", "") or "",
        sys.argv[0] if sys.argv else "",
        sys.executable,
        getattr(comp, "containing_dir", "") and os.path.join(comp.containing_dir, "CSFlow.exe" if sys.platform == "win32" else "CSFlow"),
    ]
    import shutil
    for c in cands:
        if not c:
            continue
        for path in (os.path.abspath(c), shutil.which(c) or ""):
            if path and os.path.isfile(path) and path.lower().endswith((".exe", "")) and \
               os.path.basename(path).lower() not in ("python.exe", "pythonw.exe", "python", "python3"):
                return path
    return ""


def run_python_cli(argv: list) -> int:
    """Entry point used by the compiled exe:  CSFlow.exe --csflow-python <script|-c code> [args]"""
    import runpy
    import traceback
    if argv and argv[0] == "-c":
        code, rest = argv[1], argv[2:]
        sys.argv = ["<code>"] + rest
        sys.path.insert(0, os.getcwd())
        try:
            exec(compile(code, "<workflow>", "exec"), {"__name__": "__main__"})
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except BaseException:
            traceback.print_exc()
            return 1
        return 0
    script, rest = argv[0], argv[1:]
    sys.argv = [script] + rest
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))
    sys.path.append(os.getcwd())                 # comme avec -c : les modules du dossier de travail restent importables
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


@register("python", summary="Exécute un script Python (fichier ou code inline) avec l'interpréteur embarqué : aucun Python à installer.", params={
    "script":  ("chemin d'un fichier .py (relatif au dossier de travail)", False),
    "code":    ("code Python inline (alternative à script)", False),
    "args":    ("arguments passés au script (liste YAML ou chaîne)", False),
    "cwd":     ("dossier d'exécution (défaut : dossier de travail)", False),
    "timeout": ("secondes avant arrêt du processus (défaut 60)", False),
})
def python(params: dict) -> dict:
    """
    Run a Python script without requiring Python on the machine.

    Params: script (file) or code (inline), args (list or string), timeout.
    Outputs: output, stdout, stderr, returncode, status — same as `shell`.
    Only the standard library bundled in the executable is available.
    """
    script = str(params.get("script", "") or "").strip()
    code   = params.get("code")
    args   = params.get("args", [])
    timeout = int(params.get("timeout", 60))
    cwd     = str(params.get("cwd", "") or "") or None
    if cwd and script and not os.path.isabs(script):
        script = os.path.abspath(script)       # le chemin est relatif au dossier de travail, pas à cwd
    if isinstance(args, str):
        import shlex
        args = shlex.split(args, posix=(sys.platform != "win32"))
    args = [str(a) for a in args]

    if not script and not code:
        return {"output": "python: 'script' ou 'code' requis", "returncode": -1, "status": "error"}
    target = ["-c", str(code)] if code and not script else [script]
    tmp_script = ""

    if _frozen():
        exe = _exe()
        if not exe:
            return {"output": "python: exécutable CSFlow introuvable (sys.executable=%r, argv0=%r)"
                              % (sys.executable, sys.argv[:1]), "returncode": -1, "status": "error"}
        if target[0] == "-c":
            # Nuitka refuse qu'un programme compilé se rappelle lui-même avec l'argument « -c »
            # (garde « self-execution ») ; en plus, la ligne de commande Windows est limitée en taille
            # et capricieuse avec les accents. On écrit donc le code dans un fichier temporaire (UTF-8).
            import tempfile
            fd, tmp_script = tempfile.mkstemp(prefix="csflow_", suffix=".py")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(target[1])
            target = [tmp_script]
        cmd = [exe, FLAG] + target + args
    elif target[0] == "-c":
        cmd = [sys.executable, "-c", target[1], *args]
    else:
        cmd = [sys.executable, *target, *args]

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, cwd=cwd, **extra)
        out, err = _decode(proc.stdout), _decode(proc.stderr)
        return {"output": (out + err).strip(), "stdout": out.strip(), "stderr": err.strip(),
                "returncode": proc.returncode, "status": "ok" if proc.returncode == 0 else "error"}
    except subprocess.TimeoutExpired:
        return {"output": f"Script timed out after {timeout}s", "returncode": -1, "status": "error"}
    except Exception as exc:
        return {"output": "%s (commande : %s)" % (exc, cmd[0]), "returncode": -1, "status": "error"}
    finally:
        if tmp_script:
            try:
                os.remove(tmp_script)
            except OSError:
                pass