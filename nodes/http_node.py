"""nodes/http_node.py — HTTP GET and POST (no third-party dependencies)."""

import json as _json
from urllib.request import urlopen, Request
from urllib.error   import URLError, HTTPError
from . import register


def _request(method: str, params: dict) -> dict:
    url     = params.get("url", "")
    headers = params.get("headers", {})
    body    = params.get("body")
    timeout = int(params.get("timeout", 30))

    data = body.encode() if isinstance(body, str) else (
           _json.dumps(body).encode() if isinstance(body, dict) else None)

    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw  = resp.read().decode("utf-8")
            code = resp.status
            return {
                "output":      raw,
                "body":        raw,
                "status_code": code,
                "status":      "ok" if 200 <= code < 300 else "error",
            }
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        return {
            "output":      body_text,
            "status_code": exc.code,
            "status":      "error",
        }
    except URLError as exc:
        return {"output": str(exc), "status_code": 0, "status": "error"}


@register("http_get", summary="Requête HTTP GET ; sorties : body, status_code.", params={
    "url":     ("adresse à appeler", True),
    "headers": ("en-têtes HTTP (dictionnaire)", False),
    "timeout": ("secondes avant abandon (défaut 30)", False),
})
def http_get(params: dict) -> dict:
    """
    HTTP GET request.

    Params:
        url     (str):  target URL
        headers (dict): optional request headers
        timeout (int):  seconds (default 30)

    Output fields:
        output / body: response body as text
        status_code:   HTTP status code (int)
    """
    return _request("GET", params)


@register("http_post", summary="Requête HTTP POST ; sorties : body, status_code.", params={
    "url":     ("adresse à appeler", True),
    "body":    ("corps de la requête", False),
    "headers": ("en-têtes HTTP (dictionnaire)", False),
    "timeout": ("secondes avant abandon (défaut 30)", False),
})
def http_post(params: dict) -> dict:
    """
    HTTP POST request.

    Params:
        url     (str):        target URL
        body    (str|dict):   request body (dict → JSON-encoded automatically)
        headers (dict):       optional request headers
        timeout (int):        seconds (default 30)

    Output fields:
        output / body: response body as text
        status_code:   HTTP status code (int)
    """
    return _request("POST", params)
