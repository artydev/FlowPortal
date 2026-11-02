"""Tests de l'arrêt d'un lancement : processus, nœuds, moteur et historique.

Lancement (depuis la racine) :
    py -m unittest discover -s tests -p "test_*.py" -v
"""

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import WorkflowEngine  # noqa: E402
from nodes import proc_util  # noqa: E402
from nodes.python_node import python  # noqa: E402
from nodes.shell_node import shell  # noqa: E402
import portal  # noqa: E402

SLEEP = "import time; time.sleep(30)"


def later(event, delay=0.4):
    t = threading.Timer(delay, event.set)
    t.daemon = True
    t.start()
    return t


class ProcUtilTests(unittest.TestCase):

    def test_normal_run(self):
        p = proc_util.run([sys.executable, "-c", "print('ok')"], timeout=10)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), b"ok")

    def test_returncode_and_stderr(self):
        p = proc_util.run([sys.executable, "-c", "import sys; sys.stderr.write('e'); sys.exit(3)"], timeout=10)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(p.stderr, b"e")

    def test_timeout_still_raises_timeout_expired(self):
        t0 = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            proc_util.run([sys.executable, "-c", SLEEP], timeout=1)
        self.assertLess(time.time() - t0, 10)

    def test_cancel_kills_the_process(self):
        ev = threading.Event()
        later(ev)
        t0 = time.time()
        with self.assertRaises(proc_util.Cancelled):
            proc_util.run([sys.executable, "-c", SLEEP], timeout=60, cancel=ev)
        self.assertLess(time.time() - t0, 10)

    def test_unset_cancel_does_not_interfere(self):
        p = proc_util.run([sys.executable, "-c", "print(1)"], timeout=10, cancel=threading.Event())
        self.assertEqual(p.returncode, 0)

    @unittest.skipIf(sys.platform == "win32", "groupe de processus POSIX")
    def test_cancel_kills_children_too(self):
        ev = threading.Event()
        later(ev, 0.6)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "fils.txt"
            code = ("import subprocess,sys,time;"
                    "subprocess.Popen([sys.executable,'-c',\"import time,pathlib;time.sleep(2);"
                    "pathlib.Path(%r).write_text('vivant')\"]);time.sleep(30)" % str(marker))
            with self.assertRaises(proc_util.Cancelled):
                proc_util.run([sys.executable, "-c", code], timeout=60, cancel=ev)
            time.sleep(3)
            self.assertFalse(marker.exists(), "le processus fils aurait dû être arrêté")


class NodeCancelTests(unittest.TestCase):

    def test_python_node_cancelled(self):
        ev = threading.Event()
        later(ev)
        t0 = time.time()
        r = python({"code": SLEEP, "timeout": 60, "_cancel": ev})
        self.assertEqual(r["status"], "error")
        self.assertIn("Arrêté", r["output"])
        self.assertLess(time.time() - t0, 10)

    @unittest.skipIf(sys.platform == "win32", "commande POSIX")
    def test_shell_node_cancelled(self):
        ev = threading.Event()
        later(ev)
        t0 = time.time()
        r = shell({"cmd": "sleep 30", "timeout": 60, "_cancel": ev})
        self.assertEqual(r["status"], "error")
        self.assertIn("Arrêté", r["output"])
        self.assertLess(time.time() - t0, 10)

    def test_nodes_unchanged_without_cancel(self):
        self.assertEqual(python({"code": "print('a')"})["output"], "a")
        self.assertEqual(python({"code": SLEEP, "timeout": 1})["status"], "error")


WF_SLEEP = """name: dort
steps:
  - id: a
    type: log
    message: debut
  - id: dort
    type: python
    code: "import time; time.sleep(30)"
    timeout: 60
  - id: b
    type: log
    message: apres
"""


class EngineCancelTests(unittest.TestCase):

    def run_engine(self, text, cancel, after=None):
        events = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "w.yaml"
        path.write_text(text, encoding="utf-8")
        eng = WorkflowEngine(str(path), event_callback=events.append, cancel=cancel)
        if after:
            threading.Timer(after, cancel.set).start()
        t0 = time.time()
        eng.run()
        return events, time.time() - t0

    def test_cancel_before_start(self):
        ev = threading.Event()
        ev.set()
        events, _ = self.run_engine(WF_SLEEP, ev)
        types = [e["type"] for e in events]
        self.assertIn("workflow_cancelled", types)
        self.assertNotIn("step_start", types)
        self.assertNotIn("workflow_done", types)

    def test_cancel_during_python_step(self):
        ev = threading.Event()
        events, dur = self.run_engine(WF_SLEEP, ev, after=1.0)
        types = [e["type"] for e in events]
        self.assertLess(dur, 15)
        cancelled = [e for e in events if e["type"] == "workflow_cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["step_id"], "dort")
        self.assertNotIn("workflow_done", types)
        self.assertNotIn("workflow_error", types)
        started = [e["step_id"] for e in events if e["type"] == "step_start"]
        self.assertEqual(started, ["a", "dort"])

    def test_no_cancel_runs_to_the_end(self):
        events, _ = self.run_engine("name: x\nsteps:\n  - id: a\n    type: log\n    message: ok\n", threading.Event())
        self.assertEqual(events[-1]["type"], "workflow_done")

    def test_engine_without_cancel_param(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.yaml"
            path.write_text("name: x\nsteps:\n  - id: a\n    type: log\n    message: ok\n", encoding="utf-8")
            events = []
            WorkflowEngine(str(path), event_callback=events.append).run()
            self.assertEqual(events[-1]["type"], "workflow_done")


class RecorderCancelTests(unittest.TestCase):

    def test_cancelled_status_and_running_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = Path(tmp) / "workflows"
            wd.mkdir()
            (wd / "w.yaml").write_text(WF_SLEEP, encoding="utf-8")
            p = portal.Portal(wd, Path(tmp) / "runs.jsonl", base_dir=Path(tmp))
            rec = p.recorder("w.yaml")
            for ev in ({"type": "workflow_start", "name": "dort", "steps": [
                            {"id": "a", "type": "log", "description": "a"},
                            {"id": "dort", "type": "python", "description": "d"},
                            {"id": "b", "type": "log", "description": "b"}]},
                       {"type": "step_start", "step_id": "a"}, {"type": "step_done", "step_id": "a", "output": "debut"},
                       {"type": "step_start", "step_id": "dort"},
                       {"type": "workflow_cancelled", "step_id": "dort", "message": "Arrêté par l'utilisateur"}):
                rec.on_event(ev)
            r = rec.finish()
            self.assertEqual(r["status"], "cancelled")
            self.assertEqual([s["status"] for s in r["steps"]], ["ok", "cancelled", "notrun"])


if __name__ == "__main__":
    unittest.main()
