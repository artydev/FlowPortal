"""Tests unitaires des nœuds : contrat de sortie normalisé.

Le contrat que ces tests verrouillent :
  - output / stdout / stderr sont STRIPPÉS (pas de '\n' final) — c'est ce qui rend
    les comparaisons de skip_if ("'{{x.output}}' != 'ROUGE'") fiables ;
  - returncode est un int ;
  - le contenu interne multiligne est conservé (\r\n normalisé pour les tests).
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nodes.python_node import python  # noqa: E402
from nodes.shell_node import shell    # noqa: E402
from nodes.log_node import log        # noqa: E402
from nodes.user_input_node import user_input  # noqa: E402
import os, tempfile
from engine import eval_expr           # noqa: E402


def norm(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


class PythonNodeTests(unittest.TestCase):

    def test_output_stripped_no_trailing_newline(self):
        r = python({"code": "print('bonjour')"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["output"], "bonjour")

    def test_multiline_output_kept_stripped_at_ends(self):
        r = python({"code": "print('l1')\nprint('l2')"})
        self.assertEqual(norm(r["output"]), "l1\nl2")
        self.assertFalse(r["output"].endswith("\n"))

    def test_returncode_zero_and_status_ok(self):
        r = python({"code": "x = 1"})
        self.assertEqual(r["returncode"], 0)
        self.assertEqual(r["status"], "ok")

    def test_exit_code_propagated(self):
        r = python({"code": "import sys\nsys.exit(3)"})
        self.assertEqual(r["returncode"], 3)
        self.assertEqual(r["status"], "error")

    def test_stdout_stderr_combined_in_output(self):
        r = python({"code": "import sys\nprint('out')\nprint('err', file=sys.stderr)"})
        self.assertEqual(norm(r["stdout"]), "out")
        self.assertEqual(norm(r["stderr"]), "err")
        self.assertIn("out", r["output"])
        self.assertIn("err", r["output"])

    def test_missing_code_and_script_is_error(self):
        r = python({})
        self.assertEqual(r["returncode"], -1)
        self.assertEqual(r["status"], "error")

    def test_stripped_output_makes_skip_if_safe(self):
        # le contrat reliant nœud + moteur : print('ROUGE') → skip_if False quand ROUGE
        r = python({"code": "print('ROUGE')"})
        self.assertFalse(eval_expr("'{{s.output}}' != 'ROUGE'", {"s": r}))

    def test_script_file_execution(self, _tmp=None):
        # exécuter un fichier .py via le paramètre script
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "hello.py"
            p.write_text("print('since-script')", encoding="utf-8")
            r = python({"script": str(p)})
        self.assertEqual(r["output"], "since-script")
        self.assertEqual(r["returncode"], 0)


class ShellNodeTests(unittest.TestCase):

    def test_output_stripped(self):
        r = shell({"cmd": "echo bonjour"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["output"], "bonjour")

    def test_returncode_exit(self):
        r = shell({"cmd": "exit 3"})
        self.assertEqual(r["returncode"], 3)
        self.assertEqual(r["status"], "error")

    def test_chain_stops_on_failure(self):
        # dev_fix_tests.yaml : « && echo prêt » ne doit PAS produire « prêt » si le
        # maillon précédent échoue (portable : cmd.exe comme bash)
        r = shell({"cmd": "exit 1 && echo prêt"})
        self.assertEqual(r["returncode"], 1)
        self.assertNotIn("prêt", r["output"])

    def test_chain_reaches_echo_on_success(self):
        r = shell({"cmd": "echo prêt"})
        self.assertIn("prêt", r["output"])

    def test_invalid_forced_shell_rejected(self):
        r = shell({"cmd": "echo x", "shell": "zsh"})
        self.assertEqual(r["returncode"], -1)
        self.assertEqual(r["status"], "error")


class LogNodeTests(unittest.TestCase):

    def test_returns_message_and_status(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = log({"message": "hello", "level": "info"})
        self.assertEqual(r, {"output": "hello", "status": "ok"})

    def test_default_level_info(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = log({"message": "m"})
        self.assertEqual(r["output"], "m")


class UserInputNodeTests(unittest.TestCase):
    """user_input : le texte est renvoyé ET écrit dans un fichier ; annulation et texte vide = erreur."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def run_node(self, answer, **extra):
        seen = {}
        events = []

        def wait(request):
            seen.update(request)
            return answer

        p = {"prompt": "Que voyez-vous ?", "_wait_input": wait, "_callback": events.append, "_step_id": "notes"}
        p.update(extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return user_input(p), seen, events

    def test_text_returned_stripped_and_written(self):
        r, seen, events = self.run_node({"text": "  bonjour\nmonde  "})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["output"], "bonjour\nmonde")
        self.assertEqual(r["path"], "tmp/saisie/notes.txt")
        with open("tmp/saisie/notes.txt", encoding="utf-8") as f:
            self.assertEqual(f.read(), "bonjour\nmonde\n")
        self.assertEqual(events[0]["type"], "user_input_required")
        self.assertEqual(seen["prompt"], "Que voyez-vous ?")

    def test_save_as_is_used(self):
        r, _, _ = self.run_node({"text": "x"}, save_as="tmp/a/b.txt")
        self.assertEqual(r["path"], "tmp/a/b.txt")
        self.assertTrue(os.path.exists("tmp/a/b.txt"))

    def test_cancel_is_an_error(self):
        r, _, _ = self.run_node({"cancelled": True, "text": ""})
        self.assertEqual(r["status"], "error")

    def test_empty_refused_when_required(self):
        r, _, _ = self.run_node({"text": "   "})
        self.assertEqual(r["status"], "error")

    def test_empty_allowed_when_not_required(self):
        r, _, _ = self.run_node({"text": ""}, required="false")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["output"], "")

    def test_terminal_fallback_reads_stdin(self):
        sys_stdin = sys.stdin
        sys.stdin = io.StringIO("réponse au clavier\n")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                r = user_input({"prompt": "?", "_step_id": "t"})
        finally:
            sys.stdin = sys_stdin
        self.assertEqual(r["output"], "réponse au clavier")

    def test_template_in_answer_is_not_expanded_by_engine(self):
        from engine import resolve
        ctx = {"s": {"output": "{{secret.output}}"}, "secret": {"output": "NON"}}
        self.assertEqual(resolve("note : {{s.output}}", ctx), "note : {{secret.output}}")


if __name__ == "__main__":
    unittest.main()
