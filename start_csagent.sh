#!/usr/bin/env bash
# start_csagent.sh - mode "CSAgent lance CSFlow" (MCP).
#  1. un CSAgent "worker" en arriere-plan (--api, port 8080) : il sert les etapes `agent` des workflows ;
#  2. le CSAgent interactif au premier plan, avec CSFlow declare comme serveur MCP (mcp.json).
# Le CSAgent interactif tourne SANS --yes : chaque appel MCP vous est demande.
# Fermer le CSAgent interactif arrete le worker.

cd "$(dirname "$0")"

CSAGENT_BIN="${CSAGENT_BIN:-csagent}"
PORT="${PORT:-8080}"
API_KEY="${API_KEY:-demo-secret}"
MODEL="${MODEL:-}"          # ex : MODEL=mon-modele-rapide (vide = routage automatique)

if [ -z "$ALBERT_API_KEY" ]; then
    echo " ERREUR : ALBERT_API_KEY n'est pas definie (export ALBERT_API_KEY=ta-cle)."; exit 1
fi
if ! command -v "$CSAGENT_BIN" >/dev/null 2>&1; then
    echo " ERREUR : $CSAGENT_BIN est introuvable dans le PATH (adapte CSAGENT_BIN)."; exit 1
fi
if [ ! -f mcp.json ]; then
    # exe a cote de ce fichier -> ./CSFlow ; sinon sources -> python3 app.py
    if   [ -x ./CSFlow ]; then SRC=mcp.json.example
    elif [ -f ./app.py ]; then SRC=mcp.linux.source.example
    else echo " ERREUR : ni ./CSFlow ni app.py a cote de ce fichier."; exit 1; fi
    if [ ! -f "$SRC" ]; then echo " ERREUR : $SRC introuvable."; exit 1; fi
    if [ "$SRC" = mcp.linux.source.example ]; then
        command -v python3 >/dev/null 2>&1 || { echo " ERREUR : python3 est introuvable."; exit 1; }
        python3 -c "import yaml" 2>/dev/null || { echo " ERREUR : PyYAML manque (sudo apt install python3-yaml)."; exit 1; }
    fi
    cp "$SRC" mcp.json; echo "  mcp.json cree depuis $SRC (verifiez la commande)."
fi

# Worker : memoire separee (--mem) pour ne pas melanger avec votre conversation
"$CSAGENT_BIN" --api --yes --port "$PORT" --api-key "$API_KEY" ${MODEL:+--model "$MODEL"} --mem csflow_worker --no-distill >csagent_worker.log 2>&1 &
WORKER_PID=$!
trap 'kill "$WORKER_PID" 2>/dev/null' EXIT INT TERM
sleep 3
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    echo " ERREUR : le worker CSAgent s'est arrete (voir csagent_worker.log)."; exit 1
fi

echo "  Worker CSAgent : http://localhost:$PORT   (log : csagent_worker.log)"
echo "  CSAgent interactif avec le serveur MCP CSFlow..."
"$CSAGENT_BIN" --mcp-config mcp.json
echo "  CSAgent ferme -- arret du worker."