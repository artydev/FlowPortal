"""Tests du client audio de CSAgent (audio_client) contre un faux serveur local.

Lancement (depuis la racine) :
    py -m unittest discover -s tests -p "test_*.py" -v
"""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audio_client  # noqa: E402

KEY = "cle-de-test"


class FakeAgent(BaseHTTPRequestHandler):
    store = {}                      # id -> octets reçus
    seen = {}                       # ce que le serveur a observé (type, lang, ordre)
    mode = "ok"                     # ok | no-speech | stream-cut | no-audio-routes

    def log_message(self, *a):
        pass

    def _deny(self, code, text=""):
        self.send_response(code); self.end_headers(); self.wfile.write(text.encode())

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + KEY:
            return self._deny(401, "Unauthorized")
        if FakeAgent.mode == "no-audio-routes":
            return self._deny(404)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        path, _, query = self.path.partition("?")
        if path == "/api/audio/start":
            FakeAgent.seen["type"] = query
            if "text%2Fplain" in query:
                return self._deny(415, "Unsupported audio type")
            FakeAgent.store["abc"] = b""
            data = json.dumps({"id": "abc", "path": "recordings/rec_test.webm"}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(data)
        elif path == "/api/audio/abc":
            FakeAgent.store["abc"] += body
            self.send_response(200); self.end_headers()
        elif path == "/api/audio/abc/transcribe":
            FakeAgent.seen["lang"] = query
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            events = [{"type": "progress", "part": 1, "total": 1}]
            if FakeAgent.mode == "no-speech":
                events.append({"type": "error", "message": "No speech detected."})
            elif FakeAgent.mode != "stream-cut":
                events.append({"type": "done", "path": "transcripts/rec_test.txt",
                               "text": "texte de %d octets" % len(FakeAgent.store["abc"])})
            for ev in events:
                self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
        else:
            self._deny(404)


class AudioClientTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeAgent)
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FakeAgent.mode = "ok"
        FakeAgent.seen = {}

    def test_round_trip_with_progress_and_chunks(self):
        old = audio_client.CHUNK
        audio_client.CHUNK = 10                      # force plusieurs morceaux
        try:
            steps = []
            text, audio_path, saved = audio_client.transcribe(
                self.url, KEY, b"x" * 35, "audio/webm;codecs=opus", "fr", progress=lambda p, t: steps.append((p, t)))
        finally:
            audio_client.CHUNK = old
        self.assertEqual(text, "texte de 35 octets")           # 4 morceaux reçus et assemblés
        self.assertEqual(audio_path, "recordings/rec_test.webm")
        self.assertEqual(saved, "transcripts/rec_test.txt")
        self.assertEqual(steps, [(1, 1)])
        self.assertIn("audio%2Fwebm%3Bcodecs%3Dopus", FakeAgent.seen["type"])
        self.assertEqual(FakeAgent.seen["lang"], "lang=fr")

    def test_auto_language_sends_no_lang(self):
        audio_client.transcribe(self.url, KEY, b"abc", "audio/webm", "auto")
        self.assertEqual(FakeAgent.seen["lang"], "")

    def test_wrong_key_is_explained_without_leaking_it(self):
        with self.assertRaises(audio_client.AudioError) as cm:
            audio_client.transcribe(self.url, "mauvaise-cle", b"abc", "audio/webm")
        self.assertIn("clé d'API", str(cm.exception))
        self.assertNotIn("mauvaise-cle", str(cm.exception))

    def test_old_csagent_without_audio_routes(self):
        FakeAgent.mode = "no-audio-routes"
        with self.assertRaises(audio_client.AudioError) as cm:
            audio_client.transcribe(self.url, KEY, b"abc", "audio/webm")
        self.assertIn("mettez CSAgent à jour", str(cm.exception))

    def test_unsupported_type(self):
        with self.assertRaises(audio_client.AudioError) as cm:
            audio_client.transcribe(self.url, KEY, b"abc", "text/plain")
        self.assertIn("Format audio", str(cm.exception))

    def test_no_speech_error_is_passed_on(self):
        FakeAgent.mode = "no-speech"
        with self.assertRaises(audio_client.AudioError) as cm:
            audio_client.transcribe(self.url, KEY, b"abc", "audio/webm")
        self.assertIn("No speech", str(cm.exception))

    def test_stream_cut_without_result(self):
        FakeAgent.mode = "stream-cut"
        with self.assertRaises(audio_client.AudioError):
            audio_client.transcribe(self.url, KEY, b"abc", "audio/webm")

    def test_csagent_down(self):
        with self.assertRaises(audio_client.AudioError) as cm:
            audio_client.transcribe("http://127.0.0.1:9", KEY, b"abc", "audio/webm")
        self.assertIn("injoignable", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
