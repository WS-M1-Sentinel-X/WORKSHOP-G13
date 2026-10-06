"""IA de maintenance prédictive : Isolation Forest sur les séries capteurs (aucun seuil statique).

Le script s'abonne au topic des capteurs (le même que l'ESP8266 publie), puis :
1. APPRENTISSAGE : il observe N mesures "normales" et entraîne l'Isolation Forest ;
2. SURVEILLANCE : chaque nouvelle mesure reçoit un score de risque 0-100.
Les résultats partent sur MQTT (Home Assistant les voit via MQTT Discovery)
et chaque nouvelle anomalie part aussi en POST /api/v1/alerts.

    python anomalies.py                      # recharge le modèle s'il existe, sinon apprend
    python anomalies.py --reset              # oublie le modèle et réapprend (ex. vraies données)
    python anomalies.py --apprentissage 120  # apprentissage plus court pour un test
"""

import argparse
import json
import logging
from collections import deque

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from commun import DONNEES, charger_config, creer_client_mqtt, declarer_entite, envoyer_alerte

NOM = "anomalies"
FICHIER_MODELE = DONNEES / "modele_anomalies.joblib"
GRANDEURS = ["temperature", "humidite", "gaz"]
COLONNES = GRANDEURS + [f"pente_{g}" for g in GRANDEURS]
TOPIC_ETAT = "sentinel/ia/anomalies/etat"
log = logging.getLogger("sentinel-ia")


def pente(valeurs):
    """Pente de la droite de régression sur la fenêtre (unité par mesure).

    C'est elle qui repère une dérive lente bien avant qu'une valeur ne devienne "haute".
    """
    y = np.asarray(valeurs, dtype=float)
    x = np.arange(len(y))
    return float(np.polyfit(x, y, 1)[0])


class DetecteurAnomalies:
    def __init__(self, taille_fenetre, nb_apprentissage, confirmations):
        self.fenetre = deque(maxlen=taille_fenetre)
        self.nb_apprentissage = nb_apprentissage
        self.confirmations = confirmations
        self.exemples = []
        self.modele = None
        self.suite_anormale = 0
        self.suite_normale = 0
        self.en_anomalie = False

    # --- caractéristiques ---------------------------------------------------
    def caracteristiques(self, mesure):
        self.fenetre.append([float(mesure[g]) for g in GRANDEURS])
        if len(self.fenetre) < self.fenetre.maxlen:
            return None  # pas encore assez d'historique pour calculer les pentes
        historique = np.array(self.fenetre)
        actuelles = historique[-1].tolist()
        pentes = [pente(historique[:, i]) for i in range(len(GRANDEURS))]
        return np.array(actuelles + pentes)

    # --- modèle -------------------------------------------------------------
    def entrainer(self):
        X = np.array(self.exemples)
        foret = IsolationForest(n_estimators=200, contamination=0.005, random_state=42)
        foret.fit(X)
        self.modele = {
            "foret": foret,
            "mediane": float(np.median(foret.score_samples(X))),
            "moyenne": X.mean(axis=0),
            "ecart_type": X.std(axis=0) + 1e-9,
            "colonnes": COLONNES,
        }
        joblib.dump(self.modele, FICHIER_MODELE)
        log.info("Modèle entraîné sur %d mesures et sauvegardé (%s)", len(X), FICHIER_MODELE.name)

    def charger(self):
        if FICHIER_MODELE.exists():
            self.modele = joblib.load(FICHIER_MODELE)
            log.info("Modèle rechargé depuis %s", FICHIER_MODELE.name)

    def evaluer(self, x):
        """Renvoie (score 0-100, est_anormal, cause lisible)."""
        foret = self.modele["foret"]
        brut = foret.score_samples([x])[0]
        # Score de risque : 0 = aussi normal que la mesure normale médiane,
        # 80 = frontière apprise par la forêt (offset_), au-delà = anomalie franche.
        mediane, frontiere = self.modele["mediane"], foret.offset_
        score = float(np.clip(80 * (mediane - brut) / (mediane - frontiere), 0, 100))
        anormal = foret.predict([x])[0] == -1

        # Explication : la caractéristique la plus éloignée de ce qui a été appris.
        z = (x - self.modele["moyenne"]) / self.modele["ecart_type"]
        i = int(np.argmax(np.abs(z)))
        sens = "en hausse" if z[i] > 0 else "en baisse"
        cause = f"{COLONNES[i].replace('_', ' ')} {sens} ({z[i]:+.1f} écarts-types)"
        return round(float(score), 1), anormal, cause

    # --- boucle -------------------------------------------------------------
    def traiter(self, mesure):
        """Renvoie l'état à publier, et l'éventuel événement 'nouvelle anomalie'."""
        x = self.caracteristiques(mesure)
        if x is None:
            return {"phase": "préchauffage", "progression": len(self.fenetre)}, None

        if self.modele is None:
            self.exemples.append(x)
            if len(self.exemples) >= self.nb_apprentissage:
                self.entrainer()
            progression = round(100 * len(self.exemples) / self.nb_apprentissage)
            return {"phase": "apprentissage", "progression": progression}, None

        score, anormal, cause = self.evaluer(x)
        # Anti-rebond : plusieurs mesures anormales d'affilée pour lever l'alerte.
        # Pour la retomber, il faut redevenir "typique" (score < 40) assez longtemps :
        # une Isolation Forest sature hors de ce qu'elle a appris (un gaz à 900 peut
        # ressembler au point le plus extrême de l'apprentissage), donc "plus anormal"
        # ne suffit pas à dire que tout est rentré dans l'ordre.
        self.suite_anormale = self.suite_anormale + 1 if anormal else 0
        self.suite_normale = self.suite_normale + 1 if score < 40 else 0
        nouvelle = False
        if self.suite_anormale >= self.confirmations and not self.en_anomalie:
            self.en_anomalie, nouvelle = True, True
        elif self.suite_normale >= 2 * self.confirmations and self.en_anomalie:
            self.en_anomalie = False

        etat = {
            "phase": "surveillance",
            "progression": 100,
            "score": score,
            "anomalie": "ON" if self.en_anomalie else "OFF",
            "cause": cause if anormal else "RAS",
        }
        return etat, (etat if nouvelle else None)


def declarer_entites_ha(client, cfg):
    commun = {"state_topic": TOPIC_ETAT}
    declarer_entite(client, cfg, NOM, "sensor", "score_risque", {
        **commun, "name": "Score de risque capteurs", "unit_of_measurement": "%",
        "state_class": "measurement", "icon": "mdi:chart-bell-curve",
        "value_template": "{{ value_json.score | default(0) }}",
    })
    declarer_entite(client, cfg, NOM, "binary_sensor", "anomalie", {
        **commun, "name": "Anomalie capteurs", "device_class": "problem",
        "value_template": "{{ value_json.anomalie | default('OFF') }}",
    })
    declarer_entite(client, cfg, NOM, "sensor", "cause", {
        **commun, "name": "Cause de l'anomalie", "icon": "mdi:help-circle-outline",
        "value_template": "{{ value_json.cause | default('RAS') }}",
    })
    declarer_entite(client, cfg, NOM, "sensor", "phase", {
        **commun, "name": "Phase IA prédictive", "icon": "mdi:school",
        "value_template": "{{ value_json.phase }} ({{ value_json.progression }} %)",
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="supprimer le modèle et réapprendre")
    parser.add_argument("--apprentissage", type=int, default=300, help="nb de mesures normales pour apprendre")
    parser.add_argument("--fenetre", type=int, default=30, help="taille de la fenêtre glissante")
    parser.add_argument("--confirmations", type=int, default=5, help="mesures anormales d'affilée avant alerte")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = charger_config()
    if args.reset and FICHIER_MODELE.exists():
        FICHIER_MODELE.unlink()

    detecteur = DetecteurAnomalies(args.fenetre, args.apprentissage, args.confirmations)
    detecteur.charger()

    def a_la_connexion(client):
        declarer_entites_ha(client, cfg)
        client.subscribe(cfg["topic_capteurs"])

    def a_la_reception(client, userdata, message):
        try:
            mesure = json.loads(message.payload)
            etat, nouvelle_anomalie = detecteur.traiter(mesure)
        except (ValueError, KeyError, TypeError) as erreur:
            log.warning("Message capteur ignoré (%s) : %r", erreur, message.payload[:200])
            return
        client.publish(TOPIC_ETAT, json.dumps(etat), retain=True)
        if nouvelle_anomalie:
            log.warning("ANOMALIE score=%s cause=%s", etat["score"], etat["cause"])
            envoyer_alerte(cfg, "ia_anomalies", "anomalie_capteurs", "haute",
                           f"Comportement anormal des capteurs : {etat['cause']}",
                           {"score": etat["score"], "mesure": mesure})

    client = creer_client_mqtt(cfg, NOM)
    client.user_data_set({"a_la_connexion": a_la_connexion})
    client.on_message = a_la_reception
    client.loop_forever()


if __name__ == "__main__":
    main()
