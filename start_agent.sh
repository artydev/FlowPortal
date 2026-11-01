#!/usr/bin/env bash
# start_agent.sh - lance CSAgent (mode API headless) PUIS CSFlow, depuis ce dossier.
# CSAgent n'ecrit que dans son dossier courant : il doit tourner ici pour que les
# chemins relatifs des workflows (tmp/...) pointent au meme endroit que CSFlow.
# Fermer CSFlow (ou Ctrl-C) arrete aussi CSAgent.

cd "$(dirname "$0")"

# ---- a adapter ------------------------------------------------------------
CSAGENT_BIN="${CSAGENT_BIN:-csagent}"
PORT="${PORT:-8080}"
API_KEY="${API_KEY:-demo-secret}"
MODEL="${MODEL:-}"          # ex : MODEL=mon-modele-rapide (vide = routage automatique)
# ---------------------------------------------------------------------------

if [ -z "$ALBERT_API_KEY" ]; then
    echo " ERREUR : ALBERT_API_KEY n'est pas definie."
    echo " Lance :  export ALBERT_API_KEY=ta-cle"
    exit 1
fi
if ! command -v "$CSAGENT_BIN" >/dev/null 2>&1; then
    echo " ERREUR : $CSAGENT_BIN est introuvable dans le PATH (adapte CSAGENT_BIN)."
    exit 1
fi

# Application CSFlow : executable a cote de ce fichier, sinon build/CSFlow, sinon app.py
if   [ -x ./CSFlow ];       then CSFLOW_CMD=(./CSFlow)
elif [ -x ./build/CSFlow ]; then CSFLOW_CMD=(./build/CSFlow)
elif [ -f ./app.py ];       then CSFLOW_CMD=(python3 ./app.py)
else
    echo " ERREUR : CSFlow ou app.py introuvable a cote de ce fichier."
    exit 1
fi

echo
echo "  CSAgent -- mode API headless"
echo "  Dossier : $PWD"
echo "  Ecoute sur http://localhost:$PORT"
echo

# 1. CSAgent en arriere-plan, meme dossier de travail
"$CSAGENT_BIN" --api --yes --port "$PORT" --api-key "$API_KEY" ${MODEL:+--model "$MODEL"} --mem csflow_worker --no-distill >csagent.log 2>&1 &
AGENT_PID=$!
trap 'kill "$AGENT_PID" 2>/dev/null' EXIT INT TERM

# 2. laisser a CSAgent le temps d'ouvrir son port
sleep 3
if ! kill -0 "$AGENT_PID" 2>/dev/null; then
    echo " ERREUR : CSAgent s'est arrete (voir csagent.log)."
    exit 1
fi

# 3. CSFlow au premier plan ; a sa fermeture, le trap arrete CSAgent
echo "  Lancement de CSFlow..."
"${CSFLOW_CMD[@]}"
echo "  CSFlow ferme -- arret de CSAgent."