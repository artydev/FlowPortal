"""
mcp_client.py — CSFlow comme client MCP (sans CSAgent, sans SDK)
────────────────────────────────────────────────────────────────
Appelle directement les outils d'un serveur MCP depuis un workflow (nœuds mcp_tools et mcp_call).
Bibliothèque standard seule (comme mcp_server.py) : rien à installer, rien de plus dans l'exe.

Deux transports :
    • stdio : CSFlow lance le serveur (command + args) et lui parle en JSON-RPC, une ligne par message ;
    • HTTP  : « Streamable HTTP » (POST JSON ; réponse JSON ou flux SSE ; en-tête Mcp-Session-Id).
Le transport SSE historique (GET /sse, version 2024-11-05) n'est pas géré.

Configuration : le même fichier JSON que CSAgent (--mcp-config), clé « mcpServers » :
    {"mcpServers": {
        "fichiers": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
        "api":      {"url": "https://exemple.org/mcp", "headers": {"Authorization": "Bearer ${MON_JETON}"}}
    }}
« ${NOM} » dans command, args, env, cwd, url et headers est remplacé par la variable d'environnement
(les secrets restent hors du fichier). Une variable absente est une erreur claire, jamais une valeur vide.

Sécurité : un workflow ne désigne qu'un serveur DÉCLARÉ dans ce fichier (par son nom) ; il ne peut ni
lancer une commande ni viser une adresse de son choix par ce chemin. Le résultat d'un outil est une
DONNÉE (il peut contenir du texte hostile) : à relire par un agent depuis un fichier, jamais collé
dans son instruction.
"""

import atexit
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque

CLIENT_NAME = "csflow"
CLIENT_VERSION = "1.0"
PROTOCOL = "2025-06-18"
DEFAULT_CONFIG = "mcp.json"
DEFAULT_TIMEOUT = 60
INIT_TIMEOUT = 60          # démarrage d'un serveur (npx peut télécharger au premier lancement)
MAX_PAGES = 20             # garde-fou sur tools/list paginé
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpError(Exception):
    """Erreur de connexion, de protocole ou d'outil, avec un message lisible (jamais de secret)."""


# ── configuration ─────────────────────────────────────────────────────────────
def _expand(value, where: str):
    """Remplace ${VAR} par la variable d'environnement ; une variable absente est une erreur."""
    if isinstance(value, str):
        def sub(m):
            name = m.group(1)
            if name not in os.environ:
                raise McpError(f"Variable d'environnement « {name} » absente (utilisée dans {where}).")
            return os.environ[name]
        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v, where) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, where) for k, v in value.items()}
    return value


def load_servers(path: str = DEFAULT_CONFIG) -> dict:
    """Lit le fichier de configuration et rend {nom: définition} (sans développer les ${VAR})."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            # les exemples de config commencent par des lignes « // » (CSAgent les accepte) : on les ignore
            text = "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("//"))
        data = json.loads(text)
    except FileNotFoundError:
        raise McpError(f"Fichier de configuration MCP introuvable : {path}")
    except (OSError, ValueError) as exc:
        raise McpError(f"Configuration MCP illisible ({path}) : {exc}")
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise McpError(f"{path} : clé « mcpServers » absente ou vide.")
    return servers


def _server_def(config: str, server: str) -> dict:
    servers = load_servers(config)
    if server not in servers:
        raise McpError(f"Serveur « {server} » absent de {config} (disponibles : {', '.join(sorted(servers))}).")
    spec = servers[server]
    if not isinstance(spec, dict):
        raise McpError(f"{config} : la définition de « {server} » doit être un objet.")
    return spec


# ── JSON-RPC commun ───────────────────────────────────────────────────────────
class _Session:
    """Base d'une session : initialisation, appels d'outils, pagination."""

    def __init__(self, name: str):
        self.name = name
        self.server_info = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self.closed = False

    # à fournir par les transports
    def _request(self, method: str, params: dict, timeout: float) -> dict:
        raise NotImplementedError

    def _notify(self, method: str, params: dict | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _new_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def initialize(self, timeout: float = INIT_TIMEOUT) -> None:
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
        }, timeout)
        self.server_info = result.get("serverInfo") or {}
        self.protocol = result.get("protocolVersion") or PROTOCOL
        self._notify("notifications/initialized")

    def list_tools(self, timeout: float = DEFAULT_TIMEOUT) -> list:
        tools, cursor = [], None
        for _ in range(MAX_PAGES):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {}, timeout)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, tool: str, arguments: dict | None, timeout: float = DEFAULT_TIMEOUT) -> dict:
        return self._request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)


def _rpc_error(err) -> McpError:
    if isinstance(err, dict):
        return McpError(f"Erreur MCP {err.get('code', '?')} : {err.get('message', err)}")
    return McpError(f"Erreur MCP : {err}")


def _answer_server_request(msg: dict):
    """Réponse minimale aux requêtes que le serveur peut nous adresser (ping seulement)."""
    if msg.get("method") == "ping":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
    return {"jsonrpc": "2.0", "id": msg["id"],
            "error": {"code": -32601, "message": "Méthode non prise en charge par CSFlow"}}


# ── transport stdio ───────────────────────────────────────────────────────────
class StdioSession(_Session):

    def __init__(self, name: str, spec: dict):
        super().__init__(name)
        command = _expand(spec.get("command"), f"{name}.command")
        if not command or not isinstance(command, str):
            raise McpError(f"Serveur « {name} » : « command » manquant.")
        args = [str(a) for a in _expand(spec.get("args") or [], f"{name}.args")]
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in _expand(spec.get("env") or {}, f"{name}.env").items()})
        cwd = _expand(spec.get("cwd"), f"{name}.cwd") or None
        exe = shutil.which(command, path=env.get("PATH")) or shutil.which(command)
        if not exe:
            raise McpError(f"Serveur « {name} » : commande introuvable : {command}")

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen([exe] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, env=env, cwd=cwd, bufsize=0,
                                          creationflags=flags)
        except OSError as exc:
            raise McpError(f"Serveur « {name} » : démarrage impossible : {exc}")

        self._write_lock = threading.Lock()
        self._pending = {}                     # id -> [Event, message]
        self._pending_lock = threading.Lock()
        self._stderr = deque(maxlen=40)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # lecture
    def _read_stdout(self):
        try:
            for raw in iter(self._proc.stdout.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue                    # une ligne qui n'est pas du JSON-RPC : ignorée
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:        # requête du serveur
                    self._send(_answer_server_request(msg))
                elif "id" in msg and ("result" in msg or "error" in msg):
                    with self._pending_lock:
                        waiter = self._pending.get(msg["id"])
                    if waiter:
                        waiter[1] = msg
                        waiter[0].set()
        finally:
            with self._pending_lock:
                for waiter in self._pending.values():
                    waiter[0].set()             # réveille les attentes : le serveur s'est arrêté

    def _read_stderr(self):
        for raw in iter(self._proc.stderr.readline, b""):
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def stderr_tail(self, lines: int = 6) -> str:
        return "\n".join(list(self._stderr)[-lines:])

    # écriture
    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except (OSError, ValueError):
                raise McpError(self._died("Le serveur ne répond plus (flux fermé)."))

    def _died(self, prefix: str) -> str:
        tail = self.stderr_tail()
        code = self._proc.poll()
        extra = f" (code {code})" if code is not None else ""
        return f"Serveur « {self.name} » : {prefix}{extra}" + (f"\n{tail}" if tail else "")

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        rid = self._new_id()
        waiter = [threading.Event(), None]
        with self._pending_lock:
            self._pending[rid] = waiter
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            if not waiter[0].wait(timeout):
                try:
                    self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                "params": {"requestId": rid, "reason": "délai dépassé"}})
                except McpError:
                    pass
                raise McpError(f"Serveur « {self.name} » : pas de réponse à « {method} » en {int(timeout)} s.")
            msg = waiter[1]
            if msg is None:
                raise McpError(self._died("le serveur s'est arrêté"))
            if "error" in msg:
                raise _rpc_error(msg["error"])
            return msg.get("result") or {}
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send(msg)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        for pipe in (self._proc.stdout, self._proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass


# ── transport HTTP (Streamable HTTP) ──────────────────────────────────────────
class HttpSession(_Session):

    def __init__(self, name: str, spec: dict):
        super().__init__(name)
        url = _expand(spec.get("url"), f"{name}.url")
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            raise McpError(f"Serveur « {name} » : « url » doit commencer par http:// ou https://.")
        self.url = url
        self._headers = {str(k): str(v) for k, v in _expand(spec.get("headers") or {}, f"{name}.headers").items()}
        self._session_id = None
        self.protocol = PROTOCOL

    def _post(self, msg: dict, timeout: float):
        # Un User-Agent explicite : le « Python-urllib » par défaut est refusé (HTTP 403) par beaucoup de
        # services protégés par un pare-feu web. Un en-tête du fichier de configuration garde la priorité.
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION} (MCP client)"}
        if any(k.lower() == "user-agent" for k in self._headers):
            headers.pop("User-Agent")
        headers.update(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if msg.get("method") != "initialize":
            headers["MCP-Protocol-Version"] = self.protocol
        req = urllib.request.Request(self.url, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise McpError(f"Serveur « {self.name} » : accès refusé (HTTP {exc.code}) : vérifiez la clé ou le jeton."
                               + self._reply_hint(exc))
            if exc.code == 404 and self._session_id:
                raise McpError(f"Serveur « {self.name} » : session expirée (HTTP 404).")
            if exc.code == 404:
                raise McpError(f"Serveur « {self.name} » : adresse introuvable (HTTP 404) : {self.url}")
            raise McpError(f"Serveur « {self.name} » : HTTP {exc.code}." + self._reply_hint(exc))
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise McpError(f"Serveur « {self.name} » injoignable ({self.url}) : {reason}")

    def _reply_hint(self, exc) -> str:
        """Début de la réponse d'erreur du serveur (utile pour distinguer une clé refusée d'un pare-feu),
        sans jamais y laisser une valeur d'en-tête que nous avons envoyée."""
        try:
            body = exc.read(300).decode("utf-8", "replace")
        except Exception:
            return ""
        for value in self._headers.values():
            if value and len(value) > 3:
                body = body.replace(value, "***")
                if value.lower().startswith("bearer "):
                    body = body.replace(value[7:], "***")
        body = " ".join(body.split())
        return f"\nRéponse du serveur : {body}" if body else ""

    @staticmethod
    def _sse_messages(resp, deadline: float):
        data = []
        for raw in resp:
            if time.monotonic() > deadline:
                raise McpError("délai dépassé en lisant le flux")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data:
                    try:
                        yield json.loads("\n".join(data))
                    except ValueError:
                        pass
                    data = []
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data:
            try:
                yield json.loads("\n".join(data))
            except ValueError:
                pass

    def _request(self, method: str, params: dict, timeout: float) -> dict:
        rid = self._new_id()
        deadline = time.monotonic() + timeout
        resp = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, timeout)
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in ctype:
                messages = self._sse_messages(resp, deadline)
            else:
                try:
                    body = json.loads(resp.read().decode("utf-8", "replace") or "null")
                except ValueError:
                    raise McpError(f"Serveur « {self.name} » : réponse illisible (ni JSON ni SSE).")
                messages = body if isinstance(body, list) else [body]
            try:
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("id") == rid and ("result" in msg or "error" in msg):
                        if "error" in msg:
                            raise _rpc_error(msg["error"])
                        return msg.get("result") or {}
            except (OSError, TimeoutError) as exc:
                raise McpError(f"Serveur « {self.name} » : pas de réponse à « {method} » ({exc}).")
        raise McpError(f"Serveur « {self.name} » : la réponse à « {method} » est absente.")

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        with self._post(msg, DEFAULT_TIMEOUT) as resp:
            resp.read()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._session_id:
            try:
                req = urllib.request.Request(self.url, method="DELETE",
                                             headers={**self._headers, "Mcp-Session-Id": self._session_id})
                urllib.request.urlopen(req, timeout=5).close()
            except Exception:
                pass                              # le serveur peut refuser la fermeture : sans importance


# ── sessions réutilisées pendant un workflow ──────────────────────────────────
_POOL = {}
_POOL_LOCK = threading.Lock()


def session(server: str, config: str = DEFAULT_CONFIG) -> _Session:
    """Session ouverte (et initialisée) pour ce serveur ; réutilisée par les étapes suivantes."""
    key = (os.path.abspath(config), server)
    with _POOL_LOCK:
        existing = _POOL.get(key)
        if existing and not existing.closed and _alive(existing):
            return existing
        spec = _server_def(config, server)
        if spec.get("url"):
            sess = HttpSession(server, spec)
        elif spec.get("command"):
            sess = StdioSession(server, spec)
        else:
            raise McpError(f"Serveur « {server} » : ni « command » ni « url » dans {config}.")
        try:
            sess.initialize()
        except Exception:
            sess.close()
            raise
        _POOL[key] = sess
        return sess


def _alive(sess) -> bool:
    proc = getattr(sess, "_proc", None)
    return proc is None or proc.poll() is None


def close_all() -> None:
    """Ferme tous les serveurs ouverts (fin de workflow, fin du programme)."""
    with _POOL_LOCK:
        sessions = list(_POOL.values())
        _POOL.clear()
    for sess in sessions:
        try:
            sess.close()
        except Exception:
            pass


atexit.register(close_all)


# ── mise en forme du résultat d'un outil ──────────────────────────────────────
def render_result(result: dict) -> tuple:
    """(texte, structuré ou None, en_erreur) à partir du résultat de tools/call."""
    parts = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind in ("image", "audio"):
            size = len(item.get("data", "")) * 3 // 4
            parts.append(f"[{kind} {item.get('mimeType', '?')}, ~{size} octets : non repris dans le texte]")
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res["text"]) if "text" in res else f"[ressource {res.get('uri', '?')}]")
        elif kind == "resource_link":
            parts.append(f"[lien {item.get('uri', '?')}]")
        else:
            parts.append(f"[contenu de type {kind}]")
    structured = result.get("structuredContent")
    text = "\n".join(p for p in parts if p != "").strip()
    if not text and structured is not None:
        text = json.dumps(structured, ensure_ascii=False, indent=2)
    return text, structured, bool(result.get("isError"))


def format_tools(tools: list) -> str:
    lines = []
    for t in tools:
        desc = " ".join(str(t.get("description") or "").split())
        props = ((t.get("inputSchema") or {}).get("properties") or {})
        required = set((t.get("inputSchema") or {}).get("required") or [])
        sig = ", ".join(f"{k}{'' if k in required else '?'}" for k in props)
        lines.append(f"{t.get('name')}({sig})" + (f" — {desc[:160]}" if desc else ""))
    return "\n".join(lines)