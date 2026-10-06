"""Briques communes aux deux IA de Sentinel-X.

- lecture de la configuration (.env)
- connexion MQTT (TLS si un certificat de CA est fourni)
- MQTT Discovery : déclaration automatique des entités dans Home Assistant
- envoi des alertes sur l'API de l'équipe (POST /api/v1/alerts)
"""

import json
import logging
import os
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

DOSSIER = Path(__file__).resolve().parent
# Modèle appris et captures : à part du code, pour être monté en volume dans Docker.
DONNEES = Path(os.getenv("DOSSIER_DONNEES", DOSSIER))
log = logging.getLogger("sentinel-ia")

# Toutes les entités IA sont regroupées sous un seul "appareil" dans Home Assistant.
APPAREIL_HA = {
    "identifiers": ["sentinel_x_ia"],
    "name": "Sentinel-X IA",
    "manufacturer": "AetherCorp (EPSI Workshop 2026)",
    "model": "IA vision YOLOv8n + Isolation Forest",
}


def charger_config():
    """Lit le fichier .env (clé=valeur) puis laisse les variables d'environnement gagner."""
    fichier = DOSSIER / ".env"
    if fichier.exists():
        for ligne in fichier.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                os.environ.setdefault(cle.strip(), valeur.strip())

    return {
        "mqtt_hote": os.getenv("MQTT_HOTE", "127.0.0.1"),
        "mqtt_port": int(os.getenv("MQTT_PORT", "1883")),
        "mqtt_utilisateur": os.getenv("MQTT_UTILISATEUR", ""),
        "mqtt_mot_de_passe": os.getenv("MQTT_MOT_DE_PASSE", ""),
        "mqtt_ca": os.getenv("MQTT_CA", ""),
        "topic_capteurs": os.getenv("TOPIC_CAPTEURS", "station/station1/capteurs"),
        "prefixe_discovery": os.getenv("PREFIXE_DISCOVERY", "homeassistant"),
        "api_alertes": os.getenv("API_ALERTES", ""),
        "api_ca": os.getenv("API_CA", ""),
        "api_jeton": os.getenv("API_JETON", ""),
        "source_video": os.getenv("SOURCE_VIDEO", ""),
    }


def creer_client_mqtt(cfg, nom):
    """Client MQTT connecté, avec un "testament" qui passe l'IA hors ligne si le script meurt."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sentinel-ia-{nom}")
    topic_dispo = f"sentinel/ia/{nom}/disponible"
    client.will_set(topic_dispo, "offline", retain=True)

    if cfg["mqtt_utilisateur"]:
        client.username_pw_set(cfg["mqtt_utilisateur"], cfg["mqtt_mot_de_passe"])
    if cfg["mqtt_ca"]:
        client.tls_set(ca_certs=cfg["mqtt_ca"], tls_version=ssl.PROTOCOL_TLS_CLIENT)
    elif cfg["mqtt_port"] == 8883:
        log.warning("Port 8883 sans MQTT_CA : renseigne le certificat de la CA dans .env")

    def a_la_connexion(client, userdata, flags, code, properties):
        if code.is_failure:
            log.error("Connexion MQTT refusée : %s", code)
            return
        log.info("Connecté à MQTT %s:%s", cfg["mqtt_hote"], cfg["mqtt_port"])
        client.publish(topic_dispo, "online", retain=True)
        if userdata and userdata.get("a_la_connexion"):
            userdata["a_la_connexion"](client)

    client.user_data_set({})
    client.on_connect = a_la_connexion
    client.connect(cfg["mqtt_hote"], cfg["mqtt_port"], keepalive=30)
    return client


def declarer_entite(client, cfg, nom_ia, composant, objet_id, config):
    """MQTT Discovery : un message "retained" qui crée l'entité dans Home Assistant.

    composant : "binary_sensor", "sensor", "camera"...
    """
    config = {
        "unique_id": f"sentinel_ia_{objet_id}",
        "default_entity_id": f"{composant}.sentinel_ia_{objet_id}",
        "device": APPAREIL_HA,
        "availability_topic": f"sentinel/ia/{nom_ia}/disponible",
        **config,
    }
    topic = f"{cfg['prefixe_discovery']}/{composant}/sentinel_ia/{objet_id}/config"
    client.publish(topic, json.dumps(config), retain=True)


def maintenant_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def envoyer_alerte(cfg, source, type_alerte, gravite, message, details=None):
    """POST /api/v1/alerts : c'est l'exigence du sujet, Home Assistant ne la remplace pas."""
    alerte = {
        "source": source,
        "type": type_alerte,
        "gravite": gravite,
        "message": message,
        "details": details or {},
        "horodatage": maintenant_iso(),
    }
    if not cfg["api_alertes"]:
        log.info("ALERTE (API non configurée) %s", alerte)
        return alerte

    requete = urllib.request.Request(
        cfg["api_alertes"],
        data=json.dumps(alerte).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if cfg["api_jeton"]:
        requete.add_header("Authorization", f"Bearer {cfg['api_jeton']}")
    contexte = ssl.create_default_context(cafile=cfg["api_ca"] or None)
    try:
        with urllib.request.urlopen(requete, timeout=2, context=contexte) as reponse:
            log.info("Alerte envoyée à l'API (%s) : %s", reponse.status, message)
    except Exception as erreur:  # l'IA ne doit jamais s'arrêter parce que l'API est down
        log.warning("Échec de l'envoi de l'alerte à l'API : %s", erreur)
    return alerte
