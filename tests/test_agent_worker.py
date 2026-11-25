"""FlowPortal démarre et arrête lui-même le worker CSAgent (csagent_worker.py), avec un faux CSAgent."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import csagent_worker
import nodes.agent_node as agent_node
from nodes import REGISTRY

FAKE = str(ROOT / "tests" / "fake_csagent.py")
COMMAND = [sys.executable, FAKE]


def alive(pid: int) -> bool:
    return csagent_worker._pid_alive(pid)


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        env = {k: v for k, v in os.environ.items() if k not in ("CSAGENT_URL", "CSAGENT_KEY", "CSFLOW_NO_AGENT", "CSAGENT_BIN", "CSAGENT_MODEL", "CSFLOW_AGENT_START_TIMEOUT", "FAKE_MODE")}
        env["FAKE_LOG"] = str(self.dir / "fake.json")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        before = (agent_node.current_url(), agent_node.current_key(), agent_node.READY_WAIT)
        self.addCleanup(lambda: (agent_node.configure(before[0], before[1]), setattr(agent_node, "READY_WAIT", before[2])))
        self.workers = []
        self.addCleanup(lambda: [w.stop() for w in self.workers])

    def worker(self, command=COMMAND):
        w = csagent_worker.AgentWorker(self.dir, command=command)
        self.workers.append(w)
        return w

    def wait_for(self, w, states, timeout=15):
        end = time.time() + timeout
        while time.time() < end and w.state not in states:
            time.sleep(0.05)
        self.assertIn(w.state, states, w.message)

    def fake_args(self):
        return json.loads((self.dir / "fake.json").read_text(encoding="utf-8"))


class StartStopTests(WorkerCase):
    def test_demarre_sur_un_port_libre_avec_une_cle_generee(self):
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        seen = self.fake_args()["argv"]
        port = int(seen[seen.index("--port") + 1])
        key = seen[seen.index("--api-key") + 1]
        self.assertEqual(w.url, f"http://localhost:{port}")
        self.assertNotEqual(port, 8080)
        self.assertNotEqual(key, "demo-secret")
        self.assertGreaterEqual(len(key), 24)
        self.assertRegex(key, r"^[0-9a-f]+$")             # jamais de « - » en tête : ce serait pris pour une option
        for flag in ("--api", "--yes", "--no-route", "--no-distill"):
            self.assertIn(flag, seen)
        self.assertRegex(seen[seen.index("--mem") + 1], r"^csflow_worker_\d{8}-\d{6}_[0-9a-f]{4}$")   # mémoire neuve datée
        self.assertEqual((agent_node.current_url(), agent_node.current_key()), (w.url, key))

    def test_le_worker_tourne_dans_le_dossier_de_flowportal(self):
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        self.assertEqual(Path(self.fake_args()["cwd"]).resolve(), self.dir.resolve())

    def test_deux_demarrages_ne_partagent_ni_port_ni_cle(self):
        keys = []
        for _ in range(2):
            w = self.worker()
            w.start_async()
            self.wait_for(w, ("ready",))
            keys.append((w.url, w.key))
            w.stop()
        self.assertNotEqual(keys[0][1], keys[1][1])

    def test_une_etape_agent_parle_au_worker_lance(self):
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        result = REGISTRY["agent"]({"prompt": "bonjour"})
        self.assertEqual((result["status"], result["output"]), ("ok", "réponse du faux CSAgent"))

    def test_l_arret_tue_le_worker_et_efface_le_fichier_pid(self):
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        pid = self.fake_args()["pid"]
        self.assertTrue((self.dir / "csagent.pid").is_file())
        w.stop()
        self.assertFalse(alive(pid))
        self.assertFalse((self.dir / "csagent.pid").exists())
        self.assertEqual(w.state, "stopped")
        w.stop()                                              # sans effet la deuxième fois

    def test_le_modele_est_transmis(self):
        os.environ["CSAGENT_MODEL"] = "mon-modele"
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        seen = self.fake_args()["argv"]
        self.assertEqual(seen[seen.index("--model") + 1], "mon-modele")

    def test_relance_apres_arret(self):
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        first = self.fake_args()["pid"]
        w.restart()
        self.wait_for(w, ("ready",))
        self.assertFalse(alive(first))
        self.assertTrue(w.status()["reachable"])

    def test_un_worker_mort_apres_coup_est_signale(self):
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        w._proc.kill()
        w._proc.wait()
        status = w.status()
        self.assertEqual(status["state"], "failed")
        self.assertFalse(status["reachable"])


class ModeTests(WorkerCase):
    def test_csagent_url_definie_donc_mode_externe(self):
        os.environ["CSAGENT_URL"] = "http://localhost:9999"
        w = self.worker()
        self.assertEqual(w.start_async(), "external")
        self.assertFalse(w.managed)
        self.assertFalse((self.dir / "fake.json").exists())

    def test_csflow_no_agent(self):
        os.environ["CSFLOW_NO_AGENT"] = "1"
        w = self.worker()
        self.assertEqual(w.start_async(), "external")

    def test_exe_introuvable(self):
        os.environ["CSAGENT_BIN"] = str(self.dir / "nexiste-pas")
        w = csagent_worker.AgentWorker(self.dir)
        self.assertEqual(w.start_async(), "missing")
        self.assertIn("introuvable", w.message)
        self.assertEqual(w.status()["state"], "missing")

    def test_recherche_de_l_executable(self):
        name = "csagent.exe" if sys.platform.startswith("win") else "csagent"
        (self.dir / name).write_text("x")
        os.environ["CSAGENT_BIN"] = ""
        self.assertEqual(csagent_worker.find_binary(self.dir), [str(self.dir / name)])
        (self.dir / name).unlink()
        sub = self.dir / "csagent"
        sub.mkdir()
        (sub / name).write_text("x")
        self.assertEqual(csagent_worker.find_binary(self.dir), [str(sub / name)])


class FailureTests(WorkerCase):
    def test_plantage_au_demarrage_montre_le_journal(self):
        os.environ["FAKE_MODE"] = "crash"
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("failed",))
        self.assertIn("code 3", w.message)
        self.assertIn("ALBERT_API_KEY manquante", w.message)

    def test_pas_de_reponse_dans_le_delai(self):
        os.environ["FAKE_MODE"] = "hang"
        os.environ["CSFLOW_AGENT_START_TIMEOUT"] = "1.5"
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("failed",), timeout=10)
        self.assertIn("ne répond pas", w.message)
        self.assertFalse(alive(self.fake_args()["pid"]))

    def test_binaire_non_executable(self):
        w = self.worker(command=[str(self.dir / "pas-un-programme")])
        w.start_async()
        self.assertEqual(w.state, "failed")
        self.assertIn("pas pu être lancé", w.message)


class SlowStartTests(WorkerCase):
    def test_une_etape_agent_attend_la_fin_du_demarrage(self):
        os.environ["FAKE_MODE"] = "slow"
        w = self.worker()
        w.start_async()
        self.assertEqual(w.state, "starting")
        started = time.time()
        result = REGISTRY["agent"]({"prompt": "bonjour"})
        self.assertEqual(result["status"], "ok")
        self.assertGreater(time.time() - started, 0.5)

    def test_arret_pendant_l_attente_du_demarrage(self):
        os.environ["FAKE_MODE"] = "slow"
        w = self.worker()
        w.start_async()
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.time()
        result = REGISTRY["agent"]({"prompt": "x", "_cancel": cancel})
        self.assertEqual(result, {"output": "Arrêté par l'utilisateur", "status": "error"})
        self.assertLess(time.time() - started, 3)

    def test_etape_agent_avec_worker_en_echec_donne_la_raison(self):
        os.environ["FAKE_MODE"] = "crash"
        w = self.worker()
        w.start_async()
        result = REGISTRY["agent"]({"prompt": "x"})
        self.assertEqual(result["status"], "error")
        self.assertIn("s'est arrêté au démarrage", result["output"])

    def test_adresse_explicite_dans_l_etape_ignore_l_attente(self):
        """Une étape qui vise une adresse précise n'attend pas le worker de FlowPortal (sans mesurer de durée :
        sous Windows, un refus de connexion vers localhost prend plusieurs secondes)."""
        os.environ["FAKE_MODE"] = "slow"
        w = self.worker()
        w.start_async()
        waits = []
        agent_node.READY_WAIT = lambda cancel=None: waits.append(1) or (True, "")
        result = REGISTRY["agent"]({"prompt": "x", "csagent_url": "http://localhost:1", "timeout": 1})
        self.assertEqual(result["status"], "error")
        self.assertEqual(waits, [])
        REGISTRY["agent"]({"prompt": "x", "timeout": 1})              # sans adresse : il attend bien
        self.assertEqual(waits, [1])


class StaleWorkerTests(WorkerCase):
    def _stale(self, script_name):
        script = self.dir / script_name
        script.write_text("import time\ntime.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, str(script)])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        (self.dir / "csagent.pid").write_text(json.dumps({"pid": proc.pid, "port": 1}))
        return proc

    @unittest.skipIf(sys.platform.startswith("win"), "l'identification du processus se fait par le nom de l'exécutable sous Windows")
    def test_un_ancien_worker_est_arrete_au_demarrage(self):
        proc = self._stale("csagent_ancien.py")
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        self.assertIsNotNone(proc.wait(5))

    def test_un_processus_etranger_n_est_jamais_tue(self):
        proc = self._stale("autre_programme.py")
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))
        self.assertIsNone(proc.poll())
        proc.kill()

    def test_fichier_pid_illisible(self):
        (self.dir / "csagent.pid").write_text("pas du json")
        w = self.worker()
        w.start_async()
        self.wait_for(w, ("ready",))


if __name__ == "__main__":
    unittest.main()

class SessionMemoryTests(WorkerCase):
    """Issue #8 : chaque lancement démarre un worker à la mémoire vierge ; les anciennes sont nettoyées."""

    def mem_arg(self):
        argv = self.fake_args()["argv"]
        return argv[argv.index("--mem") + 1]

    def test_deux_sessions_n_ont_pas_la_meme_memoire(self):
        w1 = self.worker(); w1.start_async(); self.wait_for(w1, ("ready",)); first = self.mem_arg(); w1.stop()
        w2 = self.worker(); w2.start_async(); self.wait_for(w2, ("ready",)); second = self.mem_arg(); w2.stop()
        self.assertNotEqual(first, second)
        self.assertEqual(w2.memory, second)

    def test_les_plus_anciennes_memoires_sont_supprimees_et_les_autres_dossiers_epargnes(self):
        for name in ("csflow_worker", "csflow_worker_20250101-000001_aaaa", "csflow_worker_20250102-000001_bbbb",
                     "csflow_worker_20250103-000001_cccc", "csflow_worker_20250104-000001_dddd",
                     "csflow_worker_20250105-000001_eeee", "csflow_worker_20250106-000001_ffff",
                     "output", "csflow_worker_backup", "csflow_workerX"):
            (self.dir / name).mkdir()
        (self.dir / "csflow_worker_20240101-000001_aaaa.txt").write_text("fichier, pas un dossier", encoding="utf-8")
        removed = csagent_worker.prune_old_memories(self.dir, keep=5, current="csflow_worker_20250107-000001_gggg")
        self.assertEqual(sorted(removed), ["csflow_worker", "csflow_worker_20250101-000001_aaaa",
                                            "csflow_worker_20250102-000001_bbbb"])
        remaining = sorted(p.name for p in self.dir.iterdir())
        for kept in ("csflow_worker_20250103-000001_cccc", "csflow_worker_20250106-000001_ffff",
                     "output", "csflow_worker_backup", "csflow_workerX", "csflow_worker_20240101-000001_aaaa.txt"):
            self.assertIn(kept, remaining)
        self.assertNotIn("csflow_worker", remaining)

    def test_le_demarrage_garde_au_plus_cinq_memoires(self):
        for day in range(1, 9):
            (self.dir / f"csflow_worker_2025010{day}-000001_abcd").mkdir()
        w = self.worker(); w.start_async(); self.wait_for(w, ("ready",))
        self.assertLessEqual(len([p for p in self.dir.iterdir() if p.name.startswith("csflow_worker")]), 5)
