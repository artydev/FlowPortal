#!/usr/bin/env python3
"""Add the Yaru theme to FlowPortal. Run from the FlowPortal folder:  python add_yaru.py
Edits ui/portal.css, ui/chat.html, ui/main.js, ui/chat.js. Safe to run twice.
Works on bytes and keeps each file's own line endings and encoding (no patch/CRLF issues)."""
import re, sys
from pathlib import Path

PORTAL_CSS = """/* Theme Yaru (Ubuntu, https://github.com/ubuntu/yaru): values from gtk/src/default/gtk-3.0
   (_palette.scss, _colors.scss, dark variant). Deviations keep WCAG AA contrast (>= 4.5:1):
   Ubuntu orange #E95420 is lightened 10% as accent, button text is #181818 (white gives 3.7:1),
   red and green are lightened. */
:root[data-theme="yaru"] {
  color-scheme: dark;
  --bg: #2c2c2c;
  --surface: #272727;
  --ink: #f7f7f7;
  --mute: #aea79f;
  --line: #3d3d3d;
  --accent: #ee784e;
  --accent-ink: #181818;
  --human: #f99b11;
  --mcp: #19b6ee;
  --bad: #ef6e7d;
  --ok: #15c931;
  --shadow: 0 1px 2px #0006, 0 8px 24px -12px #000a;
}
"""
CHAT_CSS = """/* Theme Yaru (Ubuntu): same values as portal.css; lightened accent and #181818 button text for AA contrast. */
:root[data-theme="yaru"] {
  color-scheme:dark;
  --bg:#2c2c2c; --surface:#272727; --ink:#f7f7f7; --mute:#aea79f;
  --line:#3d3d3d; --accent:#ee784e; --accent-h:#e95420; --accent-ink:#181818;
  --human:#f99b11; --mcp:#19b6ee; --bad:#ef6e7d; --ok:#15c931;
  --shadow:0 1px 2px #0006,0 8px 24px -12px #000a;
}

"""

def edit(path, fn):
    p = Path(path)
    if not p.exists():
        sys.exit(f"{path}: not found - run this from the FlowPortal folder")
    raw = p.read_bytes()
    if b'"yaru"' in raw:
        print(f"{path}: already has Yaru, skipped"); return
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    new = fn(raw, nl)
    if new == raw:
        sys.exit(f"{path}: anchor not found, nothing changed (send me this file)")
    p.write_bytes(new)
    print(f"{path}: Yaru added")

def css_block(text, nl):
    return text.encode("ascii").replace(b"\r\n", b"\n").replace(b"\n", nl)

def portal_css(raw, nl):
    m = re.search(rb"^\*\s*\{\s*box-sizing", raw, re.M)          # first "* { box-sizing" rule
    return raw if not m else raw[:m.start()] + css_block(PORTAL_CSS, nl) + raw[m.start():]

def chat_html(raw, nl):
    m = re.search(rb"^/\*[^\n]*Reset & base", raw, re.M)          # "Reset & base" section comment
    return raw if not m else raw[:m.start()] + css_block(CHAT_CSS, nl) + raw[m.start():]

def js_list(raw, nl):                                              # add the entry after Dracula
    return re.sub(rb'(\[\s*"dracula"\s*,\s*"Dracula"\s*\])', rb'\1, ["yaru", "Yaru"]', raw, count=1)

edit("ui/portal.css", portal_css)
edit("ui/chat.html", chat_html)
edit("ui/main.js", js_list)
edit("ui/chat.js", js_list)
