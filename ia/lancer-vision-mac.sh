#!/usr/bin/env bash
# Lance l'IA vision sur le Mac de démo (webcam USB branchée sur le Mac, broker local).
#   ./lancer-vision-mac.sh                 # avec fenêtre et annonce vocale
#   ./lancer-vision-mac.sh --sans-fenetre  # options de vision.py
set -Eeuo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "Crée d'abord ia/.env à partir de .env.example (MQTT_HOTE=127.0.0.1, utilisateur ia, SOURCE_VIDEO vide)." >&2
    exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi
.venv/bin/python outils/telecharger_modeles.py
exec .venv/bin/python vision.py "$@"
