"""Faux ESP8266 : publie des mesures capteurs sur MQTT, au même format que le firmware.

Sert à entraîner et tester l'IA d'anomalies avant que le vrai boîtier soit prêt.

    python simulateur.py                                   # normal en continu, 1 mesure/s
    python simulateur.py --scenario surchauffe --normal 300 # 300 mesures normales puis surchauffe lente
    python simulateur.py --scenario fuite --periode 0.05    # version accélérée pour tester
"""

import argparse
import json
import logging
import math
import random
import time

from commun import charger_config, creer_client_mqtt, maintenant_iso


def mesure_normale(t):
    """Ambiance de salle stable : petite oscillation + bruit de capteur."""
    return {
        "temperature": 23.0 + 0.4 * math.sin(t / 20) + random.gauss(0, 0.15),
        "humidite": 45.0 + 1.5 * math.sin(t / 30) + random.gauss(0, 0.6),
        "gaz": 180 + random.gauss(0, 6),  # MQ-2 lu sur A0 (0-1023)
        "mouvement": 1 if random.random() < 0.02 else 0,
    }


def appliquer_scenario(mesure, scenario, k):
    """k = nombre de mesures depuis le début de l'anomalie.

    Les dérives sont lentes exprès : le but est de détecter AVANT un seuil critique.
    """
    if scenario == "surchauffe":
        mesure["temperature"] += 0.04 * k   # +0,04 °C par mesure
        mesure["gaz"] += 0.15 * k            # micro-hausse de gaz corrélée
        mesure["humidite"] -= 0.02 * k
    elif scenario == "fuite":
        mesure["gaz"] += 1.2 * k
    elif scenario == "pic":
        if k % 40 == 0:
            mesure["temperature"] += 8
    return mesure


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=["normal", "surchauffe", "fuite", "pic"], default="normal")
    parser.add_argument("--normal", type=int, default=300, help="mesures normales avant l'anomalie")
    parser.add_argument("--periode", type=float, default=1.0, help="secondes entre deux mesures")
    parser.add_argument("--total", type=int, default=0, help="arrêter après N mesures (0 = jamais)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = charger_config()
    client = creer_client_mqtt(cfg, "simulateur")
    client.loop_start()

    i = 0
    try:
        while not args.total or i < args.total:
            mesure = mesure_normale(i)
            if args.scenario != "normal" and i >= args.normal:
                mesure = appliquer_scenario(mesure, args.scenario, i - args.normal)
            message = {
                "id": "sentinel-x-01",
                "temperature": round(mesure["temperature"], 2),
                "humidite": round(mesure["humidite"], 1),
                "gaz": int(mesure["gaz"]),
                "mouvement": mesure["mouvement"],
                "ts": maintenant_iso(),
            }
            client.publish(cfg["topic_capteurs"], json.dumps(message))
            if i % 50 == 0:
                logging.info("mesure %d %s", i, message)
            i += 1
            time.sleep(args.periode)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
