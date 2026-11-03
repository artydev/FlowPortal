"""FlowPortal : logique du portail de workflows (sans interface, sans eel).

Ce module ne dépend que de PyYAML, de `schema` et, en option, de `mcp_client`.
Il fournit au portail :

  * la liste des « cartes » (un workflow = une carte) avec étapes, entrées, étiquettes et prérequis ;
  * la lecture et l'écriture sécurisées d'un workflow (validation par `schema.check`, copie .bak,
    écriture atomique, nom de fichier contrôlé) ;
  * l'historique des lancements (`runs.jsonl`, borné) construit à partir des événements du moteur.

Utilisation type (voir app.py) :

    portal = Portal(WORKFLOWS_DIR, APP_DIR / "runs.jsonl")
    cards  = portal.list_cards()
    rec    = portal.recorder("weather.yaml", inputs)
    engine = WorkflowEngine(path, event_callback=lambda ev: (rec.on_event(ev), ui(ev)))
    ...
    rec.finish()
"""

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import yaml

import schema

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD", re.I)
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*\.ya?ml$")
_MCP_TYPES = ("mcp_tools", "mcp_call")

MAX_OUTPUT = 2000          # caractères conservés par étape dans l'historique
MAX_RUNS = 200             # lancements conservés dans runs.jsonl


TEMPLATES = [
    {"id": "vide", "label": "Vide", "description": "Un nom, une description et une étape de fin.",
     "yaml": "name: {NOM}\ndescription: À compléter.\nsteps:\n  - id: fin\n    type: end\n    message: Terminé\n"},
    {"id": "une-etape", "label": "Une étape", "description": "Une entrée, un fichier écrit, un message final.",
     "yaml": "name: {NOM}\ndescription: Écrit un fichier à partir d'une entrée.\ninputs:\n  sujet: Rapport\nsteps:\n"
             "  - id: ecrire\n    type: file_write\n    description: Écrit le fichier\n    path: tmp/{NOM}.txt\n"
             "    content: \"Sujet : {{sujet}}\"\n  - id: fin\n    type: end\n    message: Fichier écrit dans tmp/{NOM}.txt\n"},
    {"id": "validation", "label": "Avec validation humaine", "description": "Prépare un fichier, demande l'accord, puis conclut.",
     "yaml": "name: {NOM}\ndescription: Prépare un fichier puis attend votre validation.\nsteps:\n"
             "  - id: preparer\n    type: file_write\n    description: Prépare le fichier\n    path: tmp/{NOM}.txt\n    content: Brouillon\n"
             "  - id: valider\n    type: human_approval\n    description: Validation\n    message: Valider le fichier tmp/{NOM}.txt ?\n"
             "    on_approve: accepte\n    on_reject: refuse\n"
             "  - id: accepte\n    type: end\n    message: Validé\n  - id: refuse\n    type: end\n    message: Refusé\n"},
]


def _output_warnings(text: str) -> list:
    """Avertit quand une étape file_write écrit hors du dossier tmp/ (les fichiers produits y sont rangés)."""
    try:
        root = schema.yaml.compose(text)
    except Exception:                                       # noqa: BLE001 : le YAML invalide est déjà signalé par schema.check
        return []
    if root is None or getattr(root, "id", "") != "mapping":
        return []
    out = []
    for k, v in root.value:
        if getattr(k, "value", None) != "steps" or getattr(v, "id", "") != "sequence":
            continue
        for st in v.value:
            if getattr(st, "id", "") != "mapping":
                continue
            kv = {getattr(a, "value", None): b for a, b in st.value}
            t, pth, sid = kv.get("type"), kv.get("path"), kv.get("id")
            if getattr(t, "value", None) != "file_write" or getattr(pth, "id", "") != "scalar":
                continue
            val = str(pth.value).strip().replace("\\", "/")
            while val.startswith("./"):
                val = val[2:]
            if not val or val.startswith("{{") or val.startswith("tmp/"):
                continue
            out.append({"level": "warn", "line": pth.start_mark.line + 1,
                        "message": f"L'étape « {getattr(sid, 'value', '?')} » écrit « {pth.value} » hors du dossier tmp/ : "
                                   "rangez les fichiers produits dans tmp/ (par exemple tmp/rapport.html)."})
    return out


class PortalError(Exception):
    """Erreur d'usage (nom de fichier invalide, workflow introuvable…), message lisible."""


# ── utilitaires ───────────────────────────────────────────────────────────────
def safe_name(filename) -> str:
    """Nom de fichier de workflow : pas de chemin, pas de « .. », extension .yaml ou .yml."""
    name = str(filename or "")
    if not _FILENAME.match(name) or ".." in name or "/" in name or "\\" in name:
        raise PortalError(f"Nom de workflow invalide : « {name} » (lettres, chiffres, - _ . et extension .yaml).")
    return name


def title_of(name: str) -> str:
    return re.sub(r"[-_]+", " ", str(name)).strip()


def summary_of(description: str, limit: int = 220) -> str:
    """Première phrase de la description, sur une ligne, bornée."""
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    m = re.search(r"(?<=[.!?])\s", text)
    if m and m.start() <= limit:
        text = text[:m.start()]
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def mask(text: str) -> str:
    """Remplace par *** la valeur de toute variable d'environnement « secrète » (KEY, TOKEN, SECRET…)."""
    text = str(text)
    for key, value in os.environ.items():
        if len(value) >= 8 and _SECRET_NAME.search(key) and value in text:
            text = text.replace(value, "***")
    return text


def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    text = mask(text)
    return text if len(text) <= limit else text[:limit] + f"… [+{len(text) - limit} car.]"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _issues(raw: list) -> list:
    return [{"level": lvl, "line": line, "message": msg} for lvl, line, msg in raw]


# ── historique ────────────────────────────────────────────────────────────────
_NAME_LINE = re.compile(r"^name:[ \t]*(.*?)[ \t]*(#.*)?$", re.M)
_YAML_WORDS = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}


def _yaml_scalar(value: str) -> str:
    """Valeur YAML sûre pour un nom : telle quelle si elle est simple, sinon entre guillemets."""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value) and value.lower() not in _YAML_WORDS:
        return value
    return json.dumps(value, ensure_ascii=False)


def _top_name(text: str):
    """(valeur, correspondance) de la première ligne `name:` de premier niveau, ou (None, None)."""
    m = _NAME_LINE.search(text)
    if not m:
        return None, None
    v = m.group(1).strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v, m


def _set_top_name(text: str, new_name: str) -> str:
    _, m = _top_name(text)
    if not m:
        return text
    comment = ("  " + m.group(2)) if m.group(2) else ""
    return text[:m.start()] + "name: " + _yaml_scalar(new_name) + comment + text[m.end():]


class History:
    """Fichier JSONL, un lancement par ligne. Les lignes illisibles sont ignorées ; le fichier est borné."""

    def __init__(self, path, max_runs: int = MAX_RUNS):
        self.path = Path(path)
        self.max_runs = max(1, int(max_runs))
        self._lock = threading.Lock()

    def load(self) -> list:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
        return out

    def rename_file(self, old: str, new: str) -> None:
        """Rattache les lancements d'un workflow renommé à son nouveau nom de fichier."""
        with self._lock:
            rows = self.load()
            if not any(r.get("file") == old for r in rows):
                return
            for r in rows:
                if r.get("file") == old:
                    r["file"] = new
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            os.replace(tmp, self.path)

    def append(self, record: dict) -> None:
        with self._lock:
            rows = self.load()
            rows.append(record)
            rows = rows[-self.max_runs:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            os.replace(tmp, self.path)


class RunRecorder:
    """Transforme le flux d'événements du moteur en un enregistrement de lancement."""

    def __init__(self, portal: "Portal", filename: str, inputs: dict | None = None, clock=time.time):
        self._portal, self._clock = portal, clock
        self.filename = safe_name(filename)
        self._t0 = clock()
        self._steps: list = []
        self._by_id: dict = {}
        self._start: dict = {}
        self._name = Path(filename).stem
        self._done = False
        self._error = ""
        self._rejected = False
        self._cancelled = False
        self._warnings: list = []
        self._record = None
        self._lock = threading.Lock()
        self._inputs = {k: ("***" if _SECRET_NAME.search(str(k)) else clip(v, 500))
                        for k, v in (inputs or {}).items()}

    def on_event(self, ev: dict) -> None:
        if not isinstance(ev, dict):
            return
        kind, sid = ev.get("type"), ev.get("step_id")
        with self._lock:
            if kind == "workflow_start":
                self._name = ev.get("name") or self._name
                self._steps = [{"id": s.get("id"), "type": s.get("type"),
                                "label": s.get("description") or s.get("id"),
                                "status": "pending", "duration": 0.0, "output": ""}
                               for s in ev.get("steps", [])]
                self._by_id = {s["id"]: s for s in self._steps}
            elif kind == "workflow_done":
                self._done = True
            elif kind == "workflow_cancelled":
                self._cancelled = True
                for st in self._steps:
                    if st["status"] == "running":
                        st["status"], st["output"] = "cancelled", "Arrêté par l'utilisateur."
            elif kind == "workflow_error":
                self._error = clip(ev.get("message", "erreur"), 500)
            elif kind == "workflow_warning":
                self._warnings.append(clip(ev.get("message", ""), 300))
            elif kind in ("step_start", "step_done", "step_error", "step_skip") and sid in self._by_id:
                st = self._by_id[sid]
                if kind == "step_start":
                    st["status"] = "running"
                    self._start[sid] = self._clock()
                elif kind == "step_skip":
                    st["status"], st["output"] = "skipped", clip(ev.get("reason", ""), 300)
                else:
                    ok = kind == "step_done"
                    out = str(ev.get("output", ""))
                    st["status"] = "ok" if ok else "error"
                    st["output"] = clip(out)
                    dur = ev.get("duration")
                    st["duration"] = float(dur) if isinstance(dur, (int, float)) \
                        else round(self._clock() - self._start.get(sid, self._clock()), 1)
                    if ok and st["type"] == "human_approval" and out.strip().lower() == "rejected":
                        st["status"], self._rejected = "rejected", True

    def finish(self, status: str | None = None, error: str = "") -> dict:
        """Termine et enregistre le lancement (une seule fois). `status` force le résultat."""
        with self._lock:
            if self._record is not None:
                return self._record
            had_error = any(s["status"] == "error" for s in self._steps)
            if status is None:
                if self._cancelled:
                    status = "cancelled"
                elif self._error or (had_error and not self._done):
                    status = "error"
                elif self._done:
                    status = "rejected" if self._rejected else "ok"
                else:
                    status = "interrupted"
            for s in self._steps:
                if s["status"] in ("pending", "running"):
                    s["status"] = "notrun"
            ts = self._clock()
            self._record = {
                "id": uuid.uuid4().hex[:12], "file": self.filename, "name": self._name,
                "started": _iso(self._t0), "start_ts": self._t0,
                "duration": round(ts - self._t0, 1), "status": status,
                "error": clip(error or self._error, 500), "inputs": self._inputs,
                "steps": self._steps, "warnings": self._warnings[:20],
                "files": self._portal.files_written(self.filename, self._steps),
            }
            self._portal.history.append(self._record)
            return self._record


# ── portail ───────────────────────────────────────────────────────────────────
class Portal:

    def __init__(self, workflows_dir, history_path, mcp_config: str = "mcp.json",
                 base_dir=None, max_runs: int = MAX_RUNS):
        self.dir = Path(workflows_dir)
        self.base = Path(base_dir) if base_dir else self.dir.parent
        self.mcp_config = mcp_config
        self.history = History(history_path, max_runs)

    # -- fichiers ----------------------------------------------------------
    def workflow_path(self, filename) -> Path:
        path = self.dir / safe_name(filename)
        if not path.is_file():
            raise PortalError(f"Workflow introuvable : {path.name}")
        return path

    def read_yaml(self, filename) -> dict:
        try:
            path = self.workflow_path(filename)
            return {"ok": True, "filename": path.name, "yaml": path.read_text(encoding="utf-8")}
        except (PortalError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def check_yaml(self, text: str) -> dict:
        issues = _issues(schema.check(str(text))) + _output_warnings(str(text))
        issues.sort(key=lambda i: (i.get("line") or 0))
        return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues}

    def save_yaml(self, filename, text: str, create: bool = False) -> dict:
        """Valide puis écrit. Refuse s'il y a des erreurs ; garde l'ancienne version en `<nom>.bak`."""
        try:
            name = safe_name(filename)
            path = self.dir / name
            if create and path.exists():
                raise PortalError(f"Le workflow « {name} » existe déjà.")
            if not create and not path.is_file():
                raise PortalError(f"Workflow introuvable : {name}")
            res = self.check_yaml(text)
            if not res["ok"]:
                return {"ok": False, "error": "Le workflow contient des erreurs.", "issues": res["issues"]}
            body = str(text)
            if not body.endswith("\n"):
                body += "\n"
            self.dir.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                (self.dir / (name + ".bak")).write_bytes(path.read_bytes())
            tmp = self.dir / ("." + name + ".tmp")
            tmp.write_bytes(body.encode("utf-8"))
            os.replace(tmp, path)
            return {"ok": True, "filename": name, "issues": res["issues"]}
        except (PortalError, OSError) as exc:
            return {"ok": False, "error": str(exc), "issues": []}

    def _clash(self, new_name: str, ignore: str | None = None):
        """Un workflow porte-t-il déjà ce nom (sans tenir compte de la casse, comme Windows) ?"""
        low = new_name.lower()
        for p in self.dir.glob("*.y*ml"):
            if p.name.lower() == low and p.name != ignore:
                return p.name
        return None

    def rename_workflow(self, old, new) -> dict:
        """Renomme le fichier ; l'historique suit. Si `name:` valait l'ancien nom, il prend le nouveau."""
        try:
            o, n = safe_name(old), safe_name(new)
            src = self.dir / o
            if not src.is_file():
                raise PortalError(f"Workflow introuvable : {o}")
            if o == n:
                raise PortalError("Le nouveau nom est identique à l'ancien.")
            clash = self._clash(n, ignore=o if o.lower() == n.lower() else None)
            if clash:
                raise PortalError(f"Un workflow porte déjà ce nom : {clash}.")
            text = src.read_text(encoding="utf-8")
            cur, _ = _top_name(text)
            new_text = _set_top_name(text, Path(n).stem) if cur is not None and cur == Path(o).stem else text
            dst = self.dir / n
            if new_text != text:
                src.write_text(new_text, encoding="utf-8")
            os.replace(src, dst)
            bak = self.dir / (o + ".bak")
            if bak.is_file():
                os.replace(bak, self.dir / (n + ".bak"))
            self.history.rename_file(o, n)
            return {"ok": True, "filename": n}
        except (PortalError, OSError, UnicodeError) as exc:
            return {"ok": False, "error": str(exc)}

    def duplicate_workflow(self, source, new) -> dict:
        """Copie un workflow sous un nouveau nom ; `name:` prend le nouveau nom pour distinguer les deux cartes."""
        try:
            o, n = safe_name(source), safe_name(new)
            src = self.dir / o
            if not src.is_file():
                raise PortalError(f"Workflow introuvable : {o}")
            clash = self._clash(n)
            if clash:
                raise PortalError(f"Un workflow porte déjà ce nom : {clash}.")
            text = _set_top_name(src.read_text(encoding="utf-8"), Path(n).stem)
            if not text.endswith("\n"):
                text += "\n"
            tmp = self.dir / ("." + n + ".tmp")
            tmp.write_bytes(text.encode("utf-8"))
            os.replace(tmp, self.dir / n)
            return {"ok": True, "filename": n}
        except (PortalError, OSError, UnicodeError) as exc:
            return {"ok": False, "error": str(exc)}

    TRASH = ".corbeille"

    def delete_workflow(self, filename) -> dict:
        """Supprime un workflow en le déplaçant dans `<workflows>/.corbeille/` (récupérable à la main).
        Sa copie `.bak` suit. L'historique des lancements est conservé."""
        try:
            name = safe_name(filename)
            path = self.dir / name
            if not path.is_file():
                raise PortalError(f"Workflow introuvable : {name}")
            trash = self.dir / self.TRASH
            trash.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = trash / f"{path.stem}.{stamp}{path.suffix}"
            n = 1
            while dest.exists():
                n += 1
                dest = trash / f"{path.stem}.{stamp}-{n}{path.suffix}"
            os.replace(path, dest)
            bak = self.dir / (name + ".bak")
            if bak.is_file():
                os.replace(bak, trash / (dest.name + ".bak"))
            return {"ok": True, "filename": name, "trash": f"{self.dir.name}/{self.TRASH}/{dest.name}"}
        except (PortalError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    # -- modèles ------------------------------------------------------------
    @staticmethod
    def templates() -> list:
        return [{k: t[k] for k in ("id", "label", "description")} for t in TEMPLATES]

    @staticmethod
    def template_text(template_id: str, filename: str) -> str:
        """Texte d'un modèle, avec le nom du workflow tiré du nom de fichier."""
        name = re.sub(r"\.ya?ml$", "", safe_name(filename), flags=re.I)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "nouveau-workflow"
        for t in TEMPLATES:
            if t["id"] == template_id:
                return t["yaml"].replace("{NOM}", slug)
        raise PortalError(f"Modèle inconnu : « {template_id} ».")

    # -- cartes -------------------------------------------------------------
    def list_cards(self) -> list:
        last = {}
        counts = {}
        for rec in self.history.load():
            last[rec.get("file")] = rec
            counts[rec.get("file")] = counts.get(rec.get("file"), 0) + 1
        cards = []
        for path in sorted(self.dir.glob("*.y*ml")):
            if path.suffix.lower() in (".yaml", ".yml"):
                cards.append(self._card(path, last.get(path.name), counts.get(path.name, 0)))
        return cards

    def card(self, filename) -> dict:
        path = self.workflow_path(filename)
        runs = [r for r in self.history.load() if r.get("file") == path.name]
        return self._card(path, runs[-1] if runs else None, len(runs))

    def _card(self, path: Path, last: dict | None, nruns: int) -> dict:
        base = {"filename": path.name, "path": str(path), "name": path.stem, "title": title_of(path.stem),
                "summary": "", "description": "", "inputs": {}, "steps": [],
                "tags": {"agent": False, "human": False, "mcp": False},
                "requirements": {"servers": [], "env": []}, "missing": [], "ready": False,
                "last_run": self._brief(last), "runs": nruns, "error": ""}
        try:
            text = path.read_text(encoding="utf-8")
            wf = yaml.safe_load(text)
            if not isinstance(wf, dict):
                raise ValueError("le fichier n'est pas un workflow (mapping YAML attendu)")
        except (OSError, ValueError, yaml.YAMLError) as exc:
            base["error"] = str(exc).splitlines()[0] if str(exc) else "fichier illisible"
            base["missing"] = ["Workflow illisible : " + base["error"]]
            return base
        steps = [s for s in (wf.get("steps") or []) if isinstance(s, dict)]
        types = [str(s.get("type", "")) for s in steps]
        inputs = wf.get("inputs") if isinstance(wf.get("inputs"), dict) else {}
        reqs, missing = self.requirements(steps)
        base.update({
            "name": str(wf.get("name") or path.stem), "title": title_of(wf.get("name") or path.stem),
            "summary": summary_of(wf.get("description")), "description": str(wf.get("description") or "").strip(),
            "inputs": {str(k): "" if v is None else str(v) for k, v in inputs.items()},
            "steps": [{"id": str(s.get("id", f"step_{i + 1}")), "type": str(s.get("type", "unknown")),
                       "label": str(s.get("description") or s.get("id") or f"step_{i + 1}")}
                      for i, s in enumerate(steps)],
            "tags": {"agent": "agent" in types, "human": "human_approval" in types or "user_input" in types,
                     "mcp": any(t in _MCP_TYPES for t in types)},
            "requirements": reqs, "missing": missing, "ready": not missing,
        })
        return base

    @staticmethod
    def _brief(rec: dict | None) -> dict | None:
        if not rec:
            return None
        return {k: rec.get(k) for k in ("id", "started", "duration", "status")}

    def requirements(self, steps: list) -> tuple:
        """(prérequis, manquants) : serveurs MCP nommés par les étapes et variables ${VAR} de leur définition."""
        wanted = {}
        for s in steps:
            if s.get("type") in _MCP_TYPES and str(s.get("server", "")).strip() and "{{" not in str(s.get("server")):
                wanted.setdefault(str(s.get("config") or self.mcp_config), set()).add(str(s["server"]).strip())
        reqs, missing, seen = {"servers": [], "env": []}, [], set()
        for config, names in sorted(wanted.items()):
            try:
                import mcp_client
                declared = mcp_client.load_servers(str(self.base / config) if not os.path.isabs(config) else config)
                err = ""
            except Exception as exc:                      # absent, illisible, ou module indisponible
                declared, err = {}, str(exc)
            if err and not declared:
                missing.append(f"Configuration MCP indisponible ({config}) : {err}")
            for name in sorted(names):
                ok = name in declared
                reqs["servers"].append({"name": name, "declared": ok})
                if not ok and not err:
                    missing.append(f"Serveur MCP « {name} » non déclaré dans {config}.")
                for var in sorted(set(_VAR.findall(json.dumps(declared.get(name, {}))))):
                    if var not in seen:
                        seen.add(var)
                        reqs["env"].append({"name": var, "set": var in os.environ})
                        if var not in os.environ:
                            missing.append(f"Variable d'environnement {var} absente.")
        return reqs, missing

    # -- lancement ----------------------------------------------------------
    def normalize_inputs(self, filename, values: dict | None) -> dict:
        """Entrées déclarées par le workflow, surchargées par `values` (clés inconnues ignorées), en texte."""
        card = self.card(filename)
        out = dict(card["inputs"])
        for key, value in (values or {}).items():
            if key in out:
                out[key] = "" if value is None else str(value)
        return out

    def recorder(self, filename, inputs: dict | None = None, clock=time.time) -> RunRecorder:
        return RunRecorder(self, filename, inputs, clock)

    def files_written(self, filename: str, steps: list) -> list:
        """Fichiers `path:` des étapes file_write réussies (chemins sans gabarit {{ }}), avec leur taille."""
        try:
            wf = yaml.safe_load((self.dir / filename).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return []
        ok = {s["id"] for s in steps if s.get("status") == "ok"}
        out, seen = [], set()
        for s in wf.get("steps") or []:
            if not isinstance(s, dict) or s.get("type") != "file_write" or s.get("id") not in ok:
                continue
            p = str(s.get("path") or "")
            if not p or "{{" in p or p in seen:
                continue
            seen.add(p)
            full = Path(p) if os.path.isabs(p) else self.base / p
            size = full.stat().st_size if full.is_file() else None
            out.append({"path": p, "size": size})
        return out

    # -- historique ---------------------------------------------------------
    def runs(self, filename: str | None = None, limit: int = 20) -> list:
        rows = [r for r in self.history.load() if filename is None or r.get("file") == filename]
        return [{**r, "steps": [{k: s.get(k) for k in ("id", "type", "label", "status", "duration")}
                                for s in r.get("steps", [])]} for r in rows[-max(1, int(limit)):][::-1]]

    def get_run(self, run_id: str) -> dict | None:
        for rec in self.history.load():
            if rec.get("id") == run_id:
                return rec
        return None