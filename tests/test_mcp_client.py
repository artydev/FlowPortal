"""Tests du client MCP (mcp_client.py) et des nœuds mcp_tools / mcp_call.

Aucun vrai serveur MCP : un faux serveur stdio (script Python) et un faux serveur HTTP local, en
JSON puis en flux SSE, jouent le protocole. Les tests verrouillent : l'initialisation, la liste
paginée, l'appel d'un outil, le rendu du résultat, les erreurs lisibles (serveur absent, variable
manquante, 401, délai) et le fait qu'aucune clé n'apparaît dans un message.
"""

import json
import os
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp_client                         # noqa: E402
from nodes.mcp_node import mcp_call, mcp_tools   # noqa: E402

TOOLS = [
    {"name": "echo", "description": "Renvoie le texte", "inputSchema": {
        "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "boom", "description": "Échoue", "inputSchema": {"type": "object", "properties": {}}},
]
PAGE2 = [{"name": "troisieme", "description": "", "inputSchema": {"type": "object", "properties": {}}}]


def handle(msg, seen=None):
    """Logique commune des faux serveurs : rend la réponse JSON-RPC, ou None pour une notification."""
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "faux", "version": "0"}}}
    if method == "tools/list":
        if msg["params"].get("cursor") == "p2":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": PAGE2}}
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS, "nextCursor": "p2"}}
    if method == "tools/call":
        name, args = msg["params"]["name"], msg["params"].get("arguments") or {}
        if name == "echo":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "écho : " + args.get("text", "")},
                            {"type": "image", "mimeType": "image/png", "data": "AAAA"}],
                "structuredContent": {"n": len(args.get("text", ""))}}}
        if name == "boom":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True, "content": [{"type": "text", "text": "ça a échoué"}]}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "outil inconnu"}}
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "méthode inconnue"}}


STDIO_SERVER = textwrap.dedent('''
    import json, sys
    sys.path.insert(0, %(tests)r)
    from test_mcp_client import handle
    sys.stderr.write("faux serveur prêt\\n"); sys.stderr.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "tools/call" and msg["params"]["name"] == "lent":
            continue                       # ne répond jamais : pour tester le délai
        if msg.get("method") == "tools/call" and msg["params"]["name"] == "ping_d_abord":
            print(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}), flush=True)
        out = handle(msg)
        if out:
            print(json.dumps(out), flush=True)
''')


class FakeHttp(BaseHTTPRequestHandler):
    sse = False
    required_key = "k-secret"
    seen = []
    agents = []

    def log_message(self, *a):
        pass

    def do_DELETE(self):
        FakeHttp.seen.append(("DELETE", self.headers.get("Mcp-Session-Id")))
        self.send_response(204); self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHttp.seen.append((body.get("method"), self.headers.get("Mcp-Session-Id"),
                              self.headers.get("MCP-Protocol-Version")))
        FakeHttp.agents.append(self.headers.get("User-Agent"))
        if (self.headers.get("User-Agent") or "").startswith("Python-urllib"):
            blocked = b"error code: 1010"
            self.send_response(403); self.send_header("Content-Length", str(len(blocked))); self.end_headers()
            self.wfile.write(blocked); return
        if self.headers.get("Authorization") != "Bearer " + self.required_key:
            body = ("refusé pour " + self.headers.get("Authorization", "")).encode()
            self.send_response(401); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        out = handle(body)
        if out is None:
            self.send_response(202); self.end_headers(); return
        data = json.dumps(out)
        self.send_response(200)
        if body.get("method") == "initialize":
            self.send_header("Mcp-Session-Id", "sess-42")
        if self.sse:
            payload = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress",
                       "params": {}}) + "\n\nevent: message\ndata: " + data + "\n\n").encode()
            self.send_header("Content-Type", "text/event-stream")
        else:
            payload = data.encode()
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class SseHttp(FakeHttp):
    sse = True


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(mcp_client.close_all)
        self.server_py = Path(self.tmp.name) / "faux_serveur.py"
        self.server_py.write_text(STDIO_SERVER % {"tests": str(ROOT / "tests")}, encoding="utf-8")
        self.cfg = str(Path(self.tmp.name) / "mcp.json")

    def write_cfg(self, servers):
        Path(self.cfg).write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def stdio_cfg(self):
        self.write_cfg({"faux": {"command": sys.executable, "args": [str(self.server_py)]}})


class StdioTests(Base):

    def test_tools_are_listed_across_pages(self):
        self.stdio_cfg()
        r = mcp_tools({"server": "faux", "config": self.cfg})
        self.assertEqual(r["status"], "ok", r["output"])
        self.assertEqual(r["tools"], "echo,boom,troisieme")
        self.assertEqual(r["count"], 3)
        self.assertIn("echo(text) — Renvoie le texte", r["output"])

    def test_call_renders_text_structured_and_image_placeholder(self):
        self.stdio_cfg()
        r = mcp_call({"server": "faux", "tool": "echo", "config": self.cfg, "arguments": {"text": "salut"}})
        self.assertEqual(r["status"], "ok", r["output"])
        self.assertIn("écho : salut", r["output"])
        self.assertIn("[image image/png", r["output"])
        self.assertEqual(json.loads(r["structured"]), {"n": 5})

    def test_arguments_may_be_json_text(self):
        self.stdio_cfg()
        r = mcp_call({"server": "faux", "tool": "echo", "config": self.cfg, "arguments": '{"text": "x"}'})
        self.assertIn("écho : x", r["output"])

    def test_tool_error_flag_gives_error_status(self):
        self.stdio_cfg()
        r = mcp_call({"server": "faux", "tool": "boom", "config": self.cfg})
        self.assertEqual(r["status"], "error")
        self.assertIn("ça a échoué", r["output"])

    def test_protocol_error_is_readable(self):
        self.stdio_cfg()
        r = mcp_call({"server": "faux", "tool": "inconnu", "config": self.cfg})
        self.assertEqual(r["status"], "error")
        self.assertIn("-32602", r["output"])
        self.assertIn("outil inconnu", r["output"])

    def test_session_is_reused_between_steps(self):
        self.stdio_cfg()
        a = mcp_client.session("faux", self.cfg)
        b = mcp_client.session("faux", self.cfg)
        self.assertIs(a, b)
        mcp_client.close_all()
        self.assertIsNot(mcp_client.session("faux", self.cfg), a)

    def test_timeout_is_reported(self):
        self.stdio_cfg()
        r = mcp_call({"server": "faux", "tool": "lent", "config": self.cfg, "timeout": 1})
        self.assertEqual(r["status"], "error")
        self.assertIn("pas de réponse", r["output"])

    def test_server_request_ping_does_not_break_the_call(self):
        self.stdio_cfg()
        r = mcp_call({"server": "faux", "tool": "ping_d_abord", "config": self.cfg})
        # l'outil n'existe pas : la réponse est une erreur MCP, mais la ligne « ping » a été absorbée
        self.assertIn("-32602", r["output"])

    def test_save_as_writes_the_full_text_and_max_chars_truncates(self):
        self.stdio_cfg()
        target = str(Path(self.tmp.name) / "sortie" / "r.txt")
        r = mcp_call({"server": "faux", "tool": "echo", "config": self.cfg, "save_as": target,
                      "arguments": {"text": "a" * 300}, "max_chars": 100})
        self.assertIn("tronqué", r["output"])
        self.assertEqual(r["path"], target)
        self.assertIn("a" * 300, Path(target).read_text(encoding="utf-8"))

    def test_close_stops_the_process(self):
        self.stdio_cfg()
        sess = mcp_client.session("faux", self.cfg)
        proc = sess._proc
        mcp_client.close_all()
        self.assertIsNotNone(proc.poll())


class ConfigTests(Base):

    def test_unknown_server_lists_available_ones(self):
        self.stdio_cfg()
        r = mcp_tools({"server": "autre", "config": self.cfg})
        self.assertEqual(r["status"], "error")
        self.assertIn("faux", r["output"])

    def test_missing_config_file(self):
        r = mcp_tools({"server": "x", "config": str(Path(self.tmp.name) / "absent.json")})
        self.assertEqual(r["status"], "error")
        self.assertIn("introuvable", r["output"])

    def test_comment_lines_in_config_are_ignored(self):
        self.stdio_cfg()
        text = Path(self.cfg).read_text(encoding="utf-8")
        Path(self.cfg).write_text("// exemple copié depuis mcp.*.example\n" + text, encoding="utf-8")
        self.assertEqual(mcp_tools({"server": "faux", "config": self.cfg})["count"], 3)

    def test_example_files_are_loadable(self):
        servers = mcp_client.load_servers(str(ROOT / "mcp.client.example"))
        self.assertEqual(sorted(servers), ["api", "fichiers"])
        self.assertIn("csflow", mcp_client.load_servers(str(ROOT / "mcp.windows.source.example")))

    def test_missing_command_is_clear(self):
        self.write_cfg({"x": {"command": "cette-commande-n-existe-pas-12345"}})
        r = mcp_tools({"server": "x", "config": self.cfg})
        self.assertIn("commande introuvable", r["output"])

    def test_missing_env_variable_is_an_error_not_empty(self):
        os.environ.pop("CSFLOW_TEST_ABSENTE", None)
        self.write_cfg({"x": {"url": "http://127.0.0.1:1/mcp", "headers": {"Authorization": "Bearer ${CSFLOW_TEST_ABSENTE}"}}})
        r = mcp_tools({"server": "x", "config": self.cfg})
        self.assertEqual(r["status"], "error")
        self.assertIn("CSFLOW_TEST_ABSENTE", r["output"])

    def test_missing_parameters(self):
        self.assertEqual(mcp_call({"server": "x"})["status"], "error")
        self.assertEqual(mcp_tools({})["status"], "error")

    def test_arguments_must_be_an_object(self):
        self.stdio_cfg()
        self.assertEqual(mcp_call({"server": "faux", "tool": "echo", "config": self.cfg, "arguments": "[1]"})["status"], "error")
        self.assertEqual(mcp_call({"server": "faux", "tool": "echo", "config": self.cfg, "arguments": "{pas json"})["status"], "error")

    def test_env_is_expanded_for_the_server_process(self):
        os.environ["CSFLOW_TEST_VAL"] = "bonjour"
        code = ("import json,os,sys\n"
                "for l in sys.stdin:\n"
                " m=json.loads(l)\n"
                " if 'id' not in m: continue\n"
                " if m['method']=='initialize': r={'protocolVersion':'2025-06-18','serverInfo':{}}\n"
                " elif m['method']=='tools/call': r={'content':[{'type':'text','text':os.environ.get('X_ENV','?')}]}\n"
                " else: r={'tools':[]}\n"
                " print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)\n")
        f = Path(self.tmp.name) / "env_serveur.py"
        f.write_text(code, encoding="utf-8")
        self.write_cfg({"e": {"command": sys.executable, "args": [str(f)], "env": {"X_ENV": "${CSFLOW_TEST_VAL}!"}}})
        r = mcp_call({"server": "e", "tool": "t", "config": self.cfg})
        self.assertEqual(r["output"], "bonjour!")


class HttpTests(Base):

    def _run(self, handler):
        FakeHttp.seen = []
        FakeHttp.agents = []
        srv = serve(handler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        os.environ["CSFLOW_TEST_KEY"] = "k-secret"
        self.write_cfg({"web": {"url": f"http://127.0.0.1:{srv.server_port}/mcp",
                                "headers": {"Authorization": "Bearer ${CSFLOW_TEST_KEY}"}}})

    def _check(self):
        r = mcp_tools({"server": "web", "config": self.cfg})
        self.assertEqual(r["tools"], "echo,boom,troisieme", r["output"])
        c = mcp_call({"server": "web", "tool": "echo", "config": self.cfg, "arguments": {"text": "hé"}})
        self.assertEqual(c["status"], "ok", c["output"])
        self.assertIn("écho : hé", c["output"])

    def test_json_responses_and_session_header(self):
        self._run(FakeHttp)
        self._check()
        after_init = [s for s in FakeHttp.seen if s[0] in ("tools/list", "tools/call")]
        self.assertTrue(all(s[1] == "sess-42" and s[2] == "2025-06-18" for s in after_init), FakeHttp.seen)

    def test_sse_responses(self):
        self._run(SseHttp)
        self._check()

    def test_session_is_deleted_on_close(self):
        self._run(FakeHttp)
        self._check()
        mcp_client.close_all()
        self.assertIn(("DELETE", "sess-42"), FakeHttp.seen)

    def test_wrong_key_is_401_without_leaking_it(self):
        self._run(FakeHttp)
        os.environ["CSFLOW_TEST_KEY"] = "mauvaise-cle-zzz"
        r = mcp_tools({"server": "web", "config": self.cfg})
        self.assertEqual(r["status"], "error")
        self.assertIn("401", r["output"])
        self.assertNotIn("mauvaise-cle-zzz", r["output"])

    def test_explicit_user_agent_is_sent_not_python_urllib(self):
        self._run(FakeHttp)
        self._check()
        self.assertTrue(FakeHttp.agents and all(a.startswith("csflow/") for a in FakeHttp.agents), FakeHttp.agents)

    def test_user_agent_from_config_wins(self):
        self._run(FakeHttp)
        cfg = json.loads(Path(self.cfg).read_text(encoding="utf-8"))
        cfg["mcpServers"]["web"]["headers"]["user-agent"] = "mon-agent/9"
        Path(self.cfg).write_text(json.dumps(cfg), encoding="utf-8")
        mcp_tools({"server": "web", "config": self.cfg})
        self.assertEqual(set(FakeHttp.agents), {"mon-agent/9"})

    def test_error_reply_is_shown_but_never_the_key(self):
        self._run(FakeHttp)
        os.environ["CSFLOW_TEST_KEY"] = "mauvaise-cle-zzz"
        r = mcp_tools({"server": "web", "config": self.cfg})
        self.assertIn("Réponse du serveur", r["output"])
        self.assertNotIn("mauvaise-cle-zzz", r["output"])

    def test_unreachable_server(self):
        self.write_cfg({"web": {"url": "http://127.0.0.1:1/mcp"}})
        r = mcp_tools({"server": "web", "config": self.cfg})
        self.assertEqual(r["status"], "error")
        self.assertIn("injoignable", r["output"])

    def test_bad_url_scheme(self):
        self.write_cfg({"web": {"url": "ftp://x/mcp"}})
        self.assertIn("http://", mcp_tools({"server": "web", "config": self.cfg})["output"])


class EngineIntegration(Base):

    def test_workflow_chains_tools_and_call_with_placeholders_then_closes(self):
        import engine
        self.stdio_cfg()
        wf = Path(self.tmp.name) / "w.yaml"
        wf.write_text(textwrap.dedent(f"""
            name: test mcp
            steps:
              - id: liste
                type: mcp_tools
                server: faux
                config: {json.dumps(self.cfg)}
              - id: appel
                type: mcp_call
                server: faux
                config: {json.dumps(self.cfg)}
                tool: echo
                arguments:
                  text: "vu: {{{{liste.count}}}} outils"
              - id: fin
                type: end
                message: "{{{{appel.output}}}}"
        """), encoding="utf-8")
        events = []
        eng = engine.WorkflowEngine(str(wf), event_callback=events.append)
        eng.run()
        self.assertEqual(events[-1]["type"], "workflow_done", events[-3:])
        self.assertIn("écho : vu: 3 outils", eng.ctx["appel"]["output"])
        self.assertEqual(mcp_client._POOL, {})          # les serveurs sont fermés en fin de workflow


if __name__ == "__main__":
    unittest.main()