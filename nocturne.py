#!/usr/bin/env python3
"""Add the Nocturne theme (original FlowPortal theme) to FlowPortal. Run from the FlowPortal folder:
    python add_nocturne.py
Edits ui/portal.css, ui/chat.html, ui/main.js, ui/chat.js. Safe to run twice.
Works on bytes and keeps each file's own line endings and encoding."""
import re, sys
from pathlib import Path

PORTAL_CSS = """/* Theme Nocturne (original): near-black blue surfaces, vivid accents. All text colors
   reach WCAG AA (>= 4.5:1) on both --bg and --surface (lowest: accent 6.9:1, mute 7.1:1). */
:root[data-theme="nocturne"] {
  color-scheme: dark;
  --bg: #090a10;
  --surface: #11131c;
  --ink: #eceef7;
  --mute: #9a9fb8;
  --line: #2b3148;
  --accent: #6c9cff;
  --accent-ink: #070a14;
  --human: #ffb454;
  --mcp: #c39bff;
  --bad: #ff7088;
  --ok: #4fe08a;
  --shadow: 0 1px 2px #0008, 0 8px 24px -12px #000c;
}
"""
CHAT_CSS = """/* Theme Nocturne (original): same values as portal.css. */
:root[data-theme="nocturne"] {
  color-scheme:dark;
  --bg:#090a10; --surface:#11131c; --ink:#eceef7; --mute:#9a9fb8;
  --line:#2b3148; --accent:#6c9cff; --accent-h:#5887f0; --accent-ink:#070a14;
  --human:#ffb454; --mcp:#c39bff; --bad:#ff7088; --ok:#4fe08a;
  --shadow:0 1px 2px #0008,0 8px 24px -12px #000c;
}

"""

def edit(path, fn):
    p = Path(path)
    if not p.exists():
        sys.exit(f"{path}: not found - run this from the FlowPortal folder")
    raw = p.read_bytes()
    if b'"nocturne"' in raw:
        print(f"{path}: already has Nocturne, skipped"); return
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    new = fn(raw, nl)
    if new == raw:
        sys.exit(f"{path}: anchor not found, nothing changed (send me this file)")
    p.write_bytes(new)
    print(f"{path}: Nocturne added")

def css_block(text, nl):
    return text.encode("ascii").replace(b"\r\n", b"\n").replace(b"\n", nl)

def portal_css(raw, nl):
    m = re.search(rb"^\*\s*\{\s*box-sizing", raw, re.M)
    return raw if not m else raw[:m.start()] + css_block(PORTAL_CSS, nl) + raw[m.start():]

def chat_html(raw, nl):
    m = re.search(rb"^/\*[^\n]*Reset & base", raw, re.M)
    return raw if not m else raw[:m.start()] + css_block(CHAT_CSS, nl) + raw[m.start():]

def js_list(raw, nl):    # after the Yaru entry, or after Dracula if Yaru is not installed
    for name in (rb"yaru", rb"dracula"):
        pat = rb'(\[\s*"' + name + rb'"\s*,\s*"[A-Za-z]+"\s*\])'
        new = re.sub(pat, rb'\1, ["nocturne", "Nocturne"]', raw, count=1)
        if new != raw:
            return new
    return raw

edit("ui/portal.css", portal_css)
edit("ui/chat.html", chat_html)
edit("ui/main.js", js_list)
edit("ui/chat.js", js_list)