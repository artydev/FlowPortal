"""
audio_client.py — client des points d'accès audio de CSAgent (/api/audio/...).

Envoie un enregistrement à CSAgent (démarré avec --api) et renvoie la transcription (Whisper) :
    POST /api/audio/start?type=<mime>            -> {"id", "path"}
    POST /api/audio/{id}                         -> octets bruts, dans l'ordre
    POST /api/audio/{id}/transcribe?lang=fr      -> flux SSE : progress, puis done ou error

Aucune dépendance : urllib seulement. La clé d'API n'est jamais écrite dans un message d'erreur.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CHUNK = 4 * 1024 * 1024          # octets envoyés par requête


class AudioError(Exception):
    """Erreur lisible par l'utilisateur."""


def _request(url, key, method="POST", data=None, content_type=None, timeout=60):
    headers = {"Authorization": "Bearer " + key} if key else {}
    if content_type:
        headers["Content-Type"] = content_type
    req = Request(url, data=data if data is not None else (b"" if method == "POST" else None),
                  headers=headers, method=method)
    try:
        return urlopen(req, timeout=timeout)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200].strip()
        if exc.code == 401:
            raise AudioError("CSAgent refuse la clé d'API (csagent_key).") from None
        if exc.code == 404 and "/api/audio/start" in url:
            raise AudioError("Les points d'accès audio sont absents de CSAgent : mettez CSAgent à jour "
                             "(--api avec /api/audio/…).") from None
        if exc.code == 415:
            raise AudioError("Format audio non accepté par CSAgent : " + body) from None
        raise AudioError("CSAgent a répondu %d : %s" % (exc.code, body or exc.reason)) from None
    except (URLError, OSError) as exc:
        raise AudioError("CSAgent est injoignable (%s) : lancez start_agent." % getattr(exc, "reason", exc)) from None


def transcribe(base_url, key, audio, mime, language="fr", progress=None, timeout=300):
    """Renvoie (texte, chemin_audio, chemin_transcription). Lève AudioError."""
    base = base_url.rstrip("/")
    with _request(base + "/api/audio/start?type=" + quote(mime or "audio/webm", safe=""), key) as r:
        started = json.loads(r.read().decode("utf-8"))
    rec_id, audio_path = started["id"], started.get("path", "")

    for i in range(0, len(audio), CHUNK):
        _request(base + "/api/audio/" + rec_id, key, data=audio[i:i + CHUNK],
                 content_type="application/octet-stream", timeout=120).close()

    lang = (language or "").strip().lower()
    url = base + "/api/audio/" + rec_id + "/transcribe"
    if lang and lang != "auto":
        url += "?lang=" + quote(lang, safe="")
    text, saved = None, ""
    with _request(url, key, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "progress" and progress:
                progress(ev.get("part", 0), ev.get("total", 0))
            elif kind == "error":
                raise AudioError(str(ev.get("message", "transcription impossible")))
            elif kind == "done":
                text, saved = str(ev.get("text", "")), str(ev.get("path", ""))
    if text is None:
        raise AudioError("La transcription s'est interrompue sans résultat.")
    return text, audio_path, saved
