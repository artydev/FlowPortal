"""ui_launcher.py — ouvre l'interface de CSFlow dans le meilleur navigateur disponible sous Linux / WSL.

Sous Windows, app.py garde le choix d'Eel (Edge, puis Chrome). Ici (Linux, macOS, WSL) :
  1. un navigateur Chromium (chromium, google-chrome, edge, brave…) en fenêtre « application » (--app) ;
  2. sous WSL : le navigateur de Windows, via wslview ou explorer.exe (localhost est relayé vers WSL2) ;
  3. Firefox, sinon le navigateur par défaut, sauf Epiphany/GNOME Web (la page y est « non réactive ») ;
  4. sinon : un message qui donne l'adresse et la marche à suivre.
Ne dépend pas d'Eel (testable seule)."""

import os
import shutil
import subprocess
import sys
import webbrowser

CHROMIUM_NAMES = (
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser", "brave", "vivaldi",
)
AVOID = ("epiphany", "gnome-web", "midori")      # navigateurs qui rendent mal la page (Page Unresponsive)


def is_wsl() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def find_chromium():
    """Chemin d'un navigateur Chromium installé, ou None."""
    for name in CHROMIUM_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _spawn(args) -> bool:
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def _default_is_avoided() -> bool:
    try:
        b = webbrowser.get()
        name = (getattr(b, "name", "") or getattr(b, "basename", "") or "").lower()
        return any(a in name for a in AVOID)
    except webbrowser.Error:
        return False


def open_ui(url: str, size=(1440, 820)) -> str:
    """Ouvre `url`. Retourne ce qui a été utilisé : 'chromium:<chemin>', 'windows', 'firefox', 'default' ou 'none'."""
    chromium = find_chromium()
    if chromium:
        args = [chromium, f"--app={url}", f"--window-size={size[0]},{size[1]}"]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")                       # Chromium refuse de tourner en root sans cela
        if _spawn(args):
            return f"chromium:{chromium}"

    if is_wsl():
        for opener in (["wslview", url], ["explorer.exe", url]):
            if shutil.which(opener[0]) and _spawn(opener):
                return "windows"

    firefox = shutil.which("firefox")
    if firefox and _spawn([firefox, url]):
        return "firefox"

    if not _default_is_avoided():
        try:
            if webbrowser.open(url):
                return "default"
        except webbrowser.Error:
            pass
    return "none"


HINT = (
    "Aucun navigateur adapté n'a été ouvert. Ouvrez l'adresse ci-dessus dans votre navigateur\n"
    "(sous WSL : dans Edge ou Chrome de Windows), ou installez Chromium :  sudo apt install chromium\n"
    "Pour ne jamais ouvrir de navigateur automatiquement :  CSFLOW_NO_BROWSER=1"
)


def main(argv=None) -> int:                                   # python3 ui_launcher.py <url> : essai à la main
    argv = argv if argv is not None else sys.argv[1:]
    url = argv[0] if argv else "http://localhost:8000/index.html"
    how = open_ui(url)
    print(how)
    if how == "none":
        print(HINT)
    return 0 if how != "none" else 1


if __name__ == "__main__":
    sys.exit(main())
