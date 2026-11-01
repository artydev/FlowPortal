#!/usr/bin/env bash
# ------------------------------------------------------------
# Compilation de CSFlow avec Nuitka (un seul executable Linux)
# A lancer depuis le dossier workflow/ (celui qui contient app.py)
# Prerequis : python3, gcc, patchelf   (sudo apt install gcc patchelf)
#             pip install nuitka -r requirements.txt
# Les scripts des noeuds `python` sont executes par l'executable lui-meme :
# seuls les modules de la bibliotheque standard listes ici (--include-module)
# sont disponibles pour eux. Ajoutez les votres au besoin.
# ------------------------------------------------------------
set -e
cd "$(dirname "$0")"

python3 -m nuitka \
    --onefile \
    --remove-output \
    --output-dir=build \
    --output-filename=CSFlow \
    --include-data-dir=ui=ui \
    --include-data-dir=workflows=workflows \
    --include-data-files=csflow.schema.json=csflow.schema.json \
    --include-data-files=CSFLOW_LLM_GUIDE.md=CSFLOW_LLM_GUIDE.md \
    --include-data-files=REFERENCE.md=REFERENCE.md \
    --include-package=eel \
    --include-package-data=eel \
    --include-package=yaml \
    --include-package=nodes \
    --include-module=mcp_server \
    --include-module=ui_launcher \
    --include-module=audio_client \
    --include-module=mcp_client \
    --include-module=runpy \
    --include-package=unittest \
    --include-module=csv \
    --include-module=json \
    --include-module=datetime \
    --include-module=re \
    --include-module=statistics \
    --include-module=collections \
    --include-module=decimal \
    --include-module=fractions \
    --include-module=random \
    --include-module=textwrap \
    --include-module=argparse \
    --include-module=pathlib \
    --include-module=sqlite3 \
    --include-module=hashlib \
    --include-module=zipfile \
    --include-module=string \
    app.py

cp -f start_agent.sh build/start_agent.sh
cp -f start_csagent.sh build/start_csagent.sh
cp -f mcp.json.example build/mcp.json.example
cp -rf skills build/
chmod +x build/CSFlow build/start_agent.sh build/start_csagent.sh

echo "Compilation reussie : build/CSFlow"
echo "Livrez build/CSFlow ET build/start_agent.sh dans le meme dossier : lancez ./start_agent.sh (CSAgent puis CSFlow)."
echo "Mode MCP : lancez ./start_csagent.sh (CSAgent lance CSFlow --mcp) ; le dossier build/skills est lu par CSAgent."
echo "Au premier lancement, CSFlow cree a cote de l'executable les dossiers workflows et tmp, le schema, CSFLOW_LLM_GUIDE.md et REFERENCE.md."