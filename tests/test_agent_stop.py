"""Étape `agent` : l'arrêt demandé depuis le portail coupe la connexion tout de suite, même si CSAgent est silencieux."""
import contextlib
import io
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nodes.agent_node  # noqa: F401,E402  (enregistre l'étape)
from nodes import REGISTRY  # noqa: E402


def sse(kind, data):
    return b"data: " + json.dumps({"type": kind, "data": data}).encode() + b"\n\n"


class FakeCSAgent:
    """Faux CSAgent : envoie `events`, puis (si `then_silence`) ne dit plus rien jusqu'à la fin du test."""

    def __init__(self, events, then_silence=False):
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for kind, data in events:
                    self.wfile.write(sse(kind, data))
                    self.wfile.flush()
                if then_silence:
                    outer.release.wait(60)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


def run_agent(url, cancel=None, timeout=30):
    params = {"prompt": "x", "csagent_url": url, "timeout": timeout}
    if cancel is not None:
        params["_cancel"] = cancel
    started = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        result = REGISTRY["agent"](params)
    return result, time.time() - started


class AgentStopTests(unittest.TestCase):
    def test_arret_pendant_un_silence_de_csagent(self):
        fake = FakeCSAgent([("thought", "je réfléchis")], then_silence=True)
        self.addCleanup(fake.close)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        result, took = run_agent(fake.url, cancel, timeout=30)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["output"], "Arrêté par l'utilisateur")
        self.assertLess(took, 5, "l'arrêt doit être immédiat, pas attendre le délai de 30 s")

    def test_arret_meme_si_la_coupure_du_socket_est_sans_effet(self):
        """Windows : fermer un socket depuis un autre thread ne débloque pas toujours la lecture."""
        from unittest import mock
        fake = FakeCSAgent([("thought", "je réfléchis")], then_silence=True)
        self.addCleanup(fake.close)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        with mock.patch.object(nodes.agent_node, "_drop_connection", lambda resp: None):
            result, took = run_agent(fake.url, cancel, timeout=30)
        self.assertEqual(result["output"], "Arrêté par l'utilisateur")
        self.assertLess(took, 5)

    def test_arret_deja_demande_avant_la_connexion(self):
        fake = FakeCSAgent([("thought", "a")], then_silence=True)
        self.addCleanup(fake.close)
        cancel = threading.Event()
        cancel.set()
        result, took = run_agent(fake.url, cancel)
        self.assertEqual(result["output"], "Arrêté par l'utilisateur")
        self.assertLess(took, 5)

    def test_flux_normal_inchange(self):
        fake = FakeCSAgent([("thought", "bonjour"), ("done", "")])
        self.addCleanup(fake.close)
        result, _ = run_agent(fake.url, threading.Event())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"], "bonjour")

    def test_erreur_de_csagent_inchangee(self):
        fake = FakeCSAgent([("error", "modèle indisponible")])
        self.addCleanup(fake.close)
        result, _ = run_agent(fake.url, threading.Event())
        self.assertEqual(result, {"output": "modèle indisponible", "status": "error"})

    def test_csagent_injoignable_n_est_pas_pris_pour_un_arret(self):
        fake = FakeCSAgent([])
        url = fake.url
        fake.close()                                      # plus personne n'écoute sur ce port
        result, _ = run_agent(url, threading.Event())
        self.assertEqual(result["status"], "error")
        self.assertNotEqual(result["output"], "Arrêté par l'utilisateur")

    def test_silence_sans_arret_finit_par_le_delai(self):
        fake = FakeCSAgent([("thought", "a")], then_silence=True)
        self.addCleanup(fake.close)
        result, took = run_agent(fake.url, threading.Event(), timeout=1)
        self.assertEqual(result["status"], "error")
        self.assertIn("Aucune donnée de CSAgent", result["output"])
        self.assertLess(took, 10)


if __name__ == "__main__":
    unittest.main()
