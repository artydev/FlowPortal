"""Faux CSAgent pour les tests : `fake_csagent.py --api --yes --port N --api-key K …` (même ligne de commande que le vrai).

Variables : FAKE_LOG (fichier où écrire la ligne de commande reçue), FAKE_MODE = crash | hang | slow | silent | normal."""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--api", action="store_true")
ap.add_argument("--yes", action="store_true")
ap.add_argument("--no-route", action="store_true")
ap.add_argument("--no-distill", action="store_true")
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--api-key", required=True)
ap.add_argument("--model")
ap.add_argument("--mem")
args = ap.parse_args()

if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid()}, f)
mode = os.environ.get("FAKE_MODE", "normal")
if mode == "crash":
    print("ALBERT_API_KEY manquante", flush=True)
    sys.exit(3)
if mode == "hang":
    time.sleep(300)
if mode == "slow":
    time.sleep(float(os.environ.get("FAKE_DELAY", "1.5")))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != f"Bearer {args.api_key}":
            self.send_response(401); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for kind, data in (("thought", "réponse du faux CSAgent"), ("done", "")):
            self.wfile.write(b"data: " + json.dumps({"type": kind, "data": data}).encode() + b"\n\n"); self.wfile.flush()
        if mode == "silent":
            threading.Event().wait(300)


ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
