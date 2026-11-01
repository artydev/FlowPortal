"""Smoke test du serveur MCP de CSFlow (aucun CSAgent requis).
Usage :  python3 tests/mcp_smoke.py        (depuis la racine du projet)
Cree des workflows jetables dans un dossier temporaire, parle JSON-RPC au serveur via stdio."""
import json, subprocess, sys, tempfile, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = {
    "quick.yaml": "name: quick\nsteps:\n  - id: a\n    type: log\n    message: bonjour\n",
    "slow.yaml": "name: slow\nsteps:\n  - id: a\n    type: python\n    code: |\n      import time\n      time.sleep(3)\n",
    "human.yaml": "name: human\nsteps:\n  - id: h\n    type: human_approval\n    message: ok ?\n",
    "saisie.yaml": "name: saisie\nsteps:\n  - id: s\n    type: user_input\n    prompt: texte ?\n",
    "fails.yaml": "name: fails\nsteps:\n  - id: f\n    type: python\n    code: |\n      import sys\n      sys.exit(3)\n",
    "broken.yaml": "name: broken\nsteps:\n  - id: x\n    type: nonexistent\n",
}
fails = []
def check(cond, label):
    print(("OK   " if cond else "FAIL ") + label)
    if not cond: fails.append(label)

tmp = Path(tempfile.mkdtemp())
for k, v in WF.items(): (tmp / k).write_text(v, encoding="utf-8")
code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from mcp_server import serve; sys.exit(serve(__import__('pathlib').Path({str(tmp)!r})))"
p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT))
n = 0
def rpc(method, params=None, notif=False):
    global n
    m = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notif: n += 1; m["id"] = n
    p.stdin.write((json.dumps(m) + "\n").encode()); p.stdin.flush()
    if notif: return None
    line = p.stdout.readline().decode()
    assert line.strip().startswith("{"), f"stdout pollue : {line!r}"
    return json.loads(line)
def call(name, **a):
    r = rpc("tools/call", {"name": name, "arguments": a}); res = r.get("result") or {}
    txt = res["content"][0]["text"] if res.get("content") else json.dumps(r.get("error"))
    try: txt = json.loads(txt)
    except Exception: pass
    return res.get("isError", False), txt

r = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
check(r["result"]["serverInfo"]["name"], "initialize")
rpc("notifications/initialized", notif=True)
check({t["name"] for t in rpc("tools/list")["result"]["tools"]} == {"csflow_list", "csflow_check", "csflow_run", "csflow_status"}, "4 outils exposes")
err, d = call("csflow_list"); check(not err and len(d["workflows"]) == 6, "list")
err, d = call("csflow_check", workflow="broken"); check(not err and d["ok"] is False, "check detecte l'erreur")
for bad in ("../app.py", "/etc/passwd", "a/b.yaml", ".hidden", "nope"):
    err, d = call("csflow_run", workflow=bad); check(err, f"refus : {bad}")
err, d = call("csflow_run", workflow="human.yaml"); check(err, "human_approval refuse")
err, d = call("csflow_run", workflow="saisie.yaml"); check(err, "user_input refuse")
err, d = call("csflow_run", workflow="broken.yaml"); check(d.get("started") is False, "workflow invalide non lance")
err, d = call("csflow_run", workflow="quick.yaml", wait_seconds=20); check(d["status"] == "done", "quick -> done")
err, d = call("csflow_run", workflow="fails.yaml", wait_seconds=20); check(d["status"] == "error" and d.get("failed_step", {}).get("returncode") == 3, "fails -> error + failed_step")
err, d = call("csflow_run", workflow="slow.yaml", wait_seconds=1); rid = d["run_id"]; check(d["status"] == "running", "slow -> running apres 1 s")
err, d2 = call("csflow_run", workflow="quick.yaml", wait_seconds=1); check(d2.get("started") is False, "un seul run a la fois")
err, d = call("csflow_status", run_id=rid, wait_seconds=30); check(d["status"] == "done", "status long-poll -> done")
err, d = call("csflow_status", run_id="zzz"); check(err, "run_id inconnu")
check(rpc("foo/bar")["error"]["code"] == -32601, "methode inconnue -> -32601")
p.stdin.close()
try: check(p.wait(timeout=10) == 0, "sortie propre sur EOF")
except subprocess.TimeoutExpired: p.kill(); check(False, "sortie propre sur EOF (le serveur ne s'arrete pas)")
print("\n" + ("TOUT EST OK" if not fails else f"{len(fails)} ECHEC(S)")); sys.exit(1 if fails else 0)
