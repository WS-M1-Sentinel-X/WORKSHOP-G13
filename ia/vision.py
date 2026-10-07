"""IA vision : détection humaine (YOLOv8n) et contrôle d'accès par reconnaissance faciale.

Scénario : un campus d'entreprise où chaque personne doit être enregistrée au poste de sécurité.
- YOLOv8n pré-entraîné (COCO) détecte les personnes (classe 0 = "person"), image à 640 px de large ;
- chaque personne est suivie d'une image à l'autre, et son visage est comparé à la bibliothèque
  des personnes enregistrées (enregistrer.py) : connue -> cadre vert avec son nom, rien d'autre ;
- personne inconnue -> cadre rouge, annonce vocale « Inconnu, identifiez-vous », alerte
  POST /api/v1/alerts, capture JPEG horodatée (preuve) et événement MQTT ;
- publie l'état sur MQTT (client MQTT propre à l'IA, compte "ia") et sert le flux annoté en
  MJPEG (http://<pc>:8090/flux).

    python vision.py                                          # SOURCE_VIDEO du .env, ou webcam 0
    python vision.py --source rtsp://192.168.0.100:8554/camera_usb
    python vision.py --source video.mp4 --sans-fenetre        # tester sur une vidéo

Temps réel : un fil de lecture ne garde que la DERNIÈRE image du flux. Sans lui, quand l'analyse
est plus lente que la caméra, les images s'empilent et l'IA travaille avec des minutes de retard.
"""

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
from ultralytics import YOLO

from commun import DONNEES, charger_config, creer_client_mqtt, declarer_entite, envoyer_alerte
from visages import Suivi, Visages

NOM = "vision"
TOPIC_ETAT = "sentinel/ia/vision/etat"
TOPIC_IMAGE = "sentinel/ia/vision/capture"
TOPIC_ANNONCE = "sentinel/ia/vision/annonce"
LARGEUR, HAUTEUR = 640, 480
ANNONCE = "Inconnu, identifiez-vous."
DELAI_SANS_VISAGE = 8.0  # s : une personne qui ne montre jamais son visage devient « inconnue »
VISAGES_INCONNUS_AVANT_ALERTE = 3  # visage vu mais pas reconnu sur 3 analyses d'affilée
INTERVALLE_ANNONCES = 15.0  # s entre deux annonces vocales
VERT, ROUGE, ORANGE = (0, 200, 0), (0, 0, 255), (0, 165, 255)
log = logging.getLogger("sentinel-ia")


# --- Lecture temps réel -------------------------------------------------------
def ouvrir(source):
    """Ouvre la source. Une webcam est lue en MJPEG 640x480 : elle compresse elle-même ses images,
    le PC n'a qu'à les décompresser (bien moins coûteux qu'un réencodage H.264 par go2rtc)."""
    webcam = isinstance(source, int) or str(source).startswith("/dev/video")
    if webcam and platform.system() == "Linux":
        camera = cv2.VideoCapture(source, cv2.CAP_V4L2)
    else:
        camera = cv2.VideoCapture(source)
    if webcam:
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEUR)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HAUTEUR)
        camera.set(cv2.CAP_PROP_FPS, 15)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return camera


class LecteurDirect:
    """Lit le flux en continu dans un fil à part et ne garde que l'image la plus récente.

    Le flux doit être lu en entier (sinon il prend du retard), mais seule l'image demandée par
    l'IA est convertie en image exploitable (retrieve) : à 5 analyses/s sur un flux à 30 images/s,
    on évite 25 conversions de couleur inutiles par seconde.
    """

    def __init__(self, source):
        self.source = source
        self.camera = ouvrir(source)
        if not self.camera.isOpened():
            raise SystemExit(f"Source vidéo introuvable : {source}")
        self.image = None
        self.numero = 0
        self.demande = threading.Event()
        self.prete = threading.Event()
        threading.Thread(target=self._lire, daemon=True).start()

    def _lire(self):
        echecs = 0
        while True:
            if not self.camera.grab():
                echecs += 1
                time.sleep(0.1)
                if echecs >= 50:  # flux muet depuis ~5 s : on se reconnecte
                    log.warning("Flux vidéo coupé, reconnexion à %s", self.source)
                    self.camera.release()
                    self.camera = ouvrir(self.source)
                    echecs = 0
                continue
            echecs = 0
            if self.demande.is_set():
                ok, image = self.camera.retrieve()
                if ok:
                    self.image, self.numero = image, self.numero + 1
                    self.demande.clear()
                    self.prete.set()

    def lire(self, dernier_numero):
        """Demande l'image suivante du flux et l'attend ; renvoie (numéro, image)."""
        self.prete.clear()
        self.demande.set()
        self.prete.wait()
        return self.numero, self.image


class LecteurFichier:
    """Pour un fichier vidéo de test : toutes les images, dans l'ordre."""

    def __init__(self, source):
        self.camera = cv2.VideoCapture(source)
        if not self.camera.isOpened():
            raise SystemExit(f"Fichier vidéo introuvable : {source}")

    def lire(self, dernier_numero):
        ok, image = self.camera.read()
        return (dernier_numero + 1, image) if ok else (dernier_numero, None)


# --- Annonce vocale -----------------------------------------------------------
def commande_voix():
    """Synthèse vocale du système : say (macOS) ou espeak-ng (Linux)."""
    if platform.system() == "Darwin" and shutil.which("say"):
        return ["say", "-v", "Thomas"]
    for programme in ("espeak-ng", "espeak"):
        if shutil.which(programme):
            return [programme, "-v", "fr"]
    return None


def annoncer(voix, texte):
    if voix:
        subprocess.Popen(voix + [texte], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        log.info("Annonce (pas de synthèse vocale sur cette machine) : %s", texte)


# --- Dessin -------------------------------------------------------------------
def dessiner(image, pistes, inference_ms):
    for piste in pistes:
        x1, y1, x2, y2 = (int(v) for v in piste["boite"])
        if piste["nom"]:
            couleur, texte = VERT, piste["nom"]
        elif piste["alerte"]:
            couleur, texte = ROUGE, "INCONNU"
        else:
            couleur, texte = ORANGE, "identification..."
        cv2.rectangle(image, (x1, y1), (x2, y2), couleur, 3 if couleur == ROUGE else 2)
        (l, h), _ = cv2.getTextSize(texte, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(image, (x1, max(0, y1 - h - 10)), (x1 + l + 8, y1), couleur, -1)
        cv2.putText(image, texte, (x1 + 4, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.putText(image, f"{inference_ms} ms", (10, image.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    return image


# --- Flux MJPEG ---------------------------------------------------------------
class FluxMJPEG:
    """Garde la dernière image annotée et la sert en MJPEG (simple à afficher partout)."""

    def __init__(self):
        self.jpeg = None
        self.verrou = threading.Condition()

    def mettre_a_jour(self, image):
        ok, tampon = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self.verrou:
                self.jpeg = tampon.tobytes()
                self.verrou.notify_all()

    def demarrer(self, port):
        flux = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/flux":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=image")
                self.end_headers()
                try:
                    while True:
                        with flux.verrou:
                            flux.verrou.wait(timeout=2)
                            jpeg = flux.jpeg
                        if jpeg:
                            self.wfile.write(b"--image\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        serveur = ThreadingHTTPServer(("0.0.0.0", port), Gestionnaire)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        log.info("Flux vidéo annoté : http://<ip-du-pc>:%d/flux", port)


# --- Anti-rebond --------------------------------------------------------------
class Presence:
    """Une présence n'est "vraie" que si un humain est vu sur plusieurs images récentes,
    et ne retombe qu'après quelques secondes sans personne (évite le clignotement)."""

    def __init__(self, images=10, minimum=4, delai_fin=3.0):
        self.historique = deque(maxlen=images)
        self.minimum = minimum
        self.delai_fin = delai_fin
        self.derniere_vue = 0.0
        self.active = False

    def mettre_a_jour(self, humain_vu):
        self.historique.append(humain_vu)
        maintenant = time.monotonic()
        if humain_vu:
            self.derniere_vue = maintenant
        debut = not self.active and sum(self.historique) >= self.minimum
        if debut:
            self.active = True
        elif self.active and maintenant - self.derniere_vue > self.delai_fin:
            self.active = False
        return debut




def declarer_entites_ha(client, cfg):
    commun = {"state_topic": TOPIC_ETAT}
    declarer_entite(client, cfg, NOM, "binary_sensor", "presence_humaine", {
        **commun, "name": "Présence humaine (IA)", "device_class": "occupancy",
        "value_template": "{{ value_json.presence }}",
    })
    declarer_entite(client, cfg, NOM, "binary_sensor", "inconnu", {
        **commun, "name": "Personne inconnue", "device_class": "safety",
        "value_template": "{{ 'ON' if value_json.inconnus > 0 else 'OFF' }}",
    })
    declarer_entite(client, cfg, NOM, "sensor", "personnes", {
        **commun, "name": "Personnes détectées", "icon": "mdi:account-group",
        "state_class": "measurement", "value_template": "{{ value_json.personnes }}",
    })
    declarer_entite(client, cfg, NOM, "sensor", "personnes_connues", {
        **commun, "name": "Personnes identifiées", "icon": "mdi:badge-account",
        "value_template": "{{ value_json.connus | join(', ') if value_json.connus else 'aucune' }}",
    })
    declarer_entite(client, cfg, NOM, "sensor", "inference_ms", {
        **commun, "name": "Temps d'inférence YOLO", "unit_of_measurement": "ms",
        "state_class": "measurement", "icon": "mdi:timer-outline",
        "value_template": "{{ value_json.inference_ms }}",
    })
    declarer_entite(client, cfg, NOM, "camera", "derniere_intrusion", {
        "name": "Dernier inconnu", "topic": TOPIC_IMAGE,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=0, help="index de la webcam")
    parser.add_argument("--source", default=None,
                        help="fichier vidéo ou flux rtsp:// à la place de la webcam (défaut : SOURCE_VIDEO du .env)")
    parser.add_argument("--modele", default=None,
                        help="modèle YOLO (défaut : MODELE_YOLO du .env, sinon yolov8n.pt ; dossier *_openvino_model = OpenVINO)")
    parser.add_argument("--confiance", type=float, default=0.5)
    parser.add_argument("--taille", type=int, default=None,
                        help="taille d'analyse YOLO (défaut : TAILLE_YOLO du .env, sinon 640 ; 416 ou 320 = plus rapide)")
    parser.add_argument("--ips", type=float, default=None,
                        help="analyses par seconde au maximum (défaut : IPS_MAX du .env, sinon 5) : laisse du CPU au reste")
    parser.add_argument("--port-flux", type=int, default=8090)
    parser.add_argument("--sans-fenetre", action="store_true", help="ne pas ouvrir de fenêtre OpenCV")
    parser.add_argument("--sans-voix", action="store_true", help="pas d'annonce vocale")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = charger_config()
    (DONNEES / "captures").mkdir(parents=True, exist_ok=True)
    source = args.source if args.source is not None else cfg["source_video"]
    if source.isdigit():
        source = int(source)
    modele_yolo = args.modele or os.getenv("MODELE_YOLO") or "yolov8n.pt"
    taille = args.taille or int(os.getenv("TAILLE_YOLO") or LARGEUR)
    pause_min = 1 / (args.ips or float(os.getenv("IPS_MAX") or 5))
    # Un flux réseau, une webcam (/dev/video*, index) : temps réel. Un fichier : toutes les images.
    if isinstance(source, str) and source and "://" not in source and not source.startswith("/dev/"):
        lecteur = LecteurFichier(source)
    else:
        lecteur = LecteurDirect(source or args.camera)

    # Le port du flux sert aussi de verrou : une 2e instance s'arrête ici au lieu de doubler la charge CPU.
    flux = FluxMJPEG()
    try:
        flux.demarrer(args.port_flux)
    except OSError:
        raise SystemExit(f"Le port {args.port_flux} est déjà pris : vision.py tourne déjà sur cette machine ?")

    modele = YOLO(modele_yolo, task="detect")  # yolov8n.pt est téléchargé la première fois (~6 Mo)
    log.info("Modèle %s, analyse en %d px, %.0f analyses/s au maximum", modele_yolo, taille, 1 / pause_min)
    visages = Visages()
    suivi = Suivi()
    voix = None if args.sans_voix else commande_voix()

    # Fusion capteur + vision (bonus) : on mémorise le dernier mouvement vu par le PIR.
    dernier_pir = {"t": 0.0}

    def a_la_connexion(client):
        declarer_entites_ha(client, cfg)
        client.subscribe(cfg["topic_capteurs"])

    def a_la_reception(client, userdata, message):
        try:
            if json.loads(message.payload).get("mouvement"):
                dernier_pir["t"] = time.monotonic()
        except (ValueError, AttributeError):
            pass

    client = creer_client_mqtt(cfg, NOM)
    client.user_data_set({"a_la_connexion": a_la_connexion})
    client.on_message = a_la_reception
    client.loop_start()

    presence = Presence()
    dernier_envoi = 0.0
    etat_precedent = None
    derniere_annonce = 0.0
    numero = 0

    try:
        while True:
            debut_tour = time.monotonic()
            numero, image = lecteur.lire(numero)
            if image is None:
                break
            if image.shape[1] != LARGEUR:
                # On garde les proportions : écraser un flux 16:9 en 4:3 déformerait les silhouettes.
                image = cv2.resize(image, (LARGEUR, round(image.shape[0] * LARGEUR / image.shape[1])))

            debut = time.perf_counter()
            resultat = modele(image, imgsz=taille, classes=[0], conf=args.confiance, verbose=False)[0]
            inference_ms = round((time.perf_counter() - debut) * 1000, 1)

            pistes = suivi.mettre_a_jour([b.tolist() for b in resultat.boxes.xyxy])
            visages.recharger_si_modifiee()
            # On ne cherche le visage que des personnes pas encore identifiées : économie de CPU.
            a_identifier = [p for p in pistes if not p["nom"]]
            for piste in a_identifier:
                visage = visages.detecter_haut_du_corps(image, piste["boite"])
                if visage is not None:
                    nom, similarite = visages.identifier(visages.signature(image, visage))
                    if nom:
                        piste["nom"], piste["visage_inconnu"] = nom, 0
                        log.info("Personne identifiée : %s (similarité %.2f)", nom, similarite)
                    else:
                        piste["visage_inconnu"] += 1

            maintenant = time.monotonic()
            nouveaux_inconnus = []
            for piste in pistes:
                if piste["nom"] or piste["alerte"]:
                    continue
                if (piste["visage_inconnu"] >= VISAGES_INCONNUS_AVANT_ALERTE
                        or maintenant - piste["debut"] > DELAI_SANS_VISAGE):
                    piste["alerte"] = True
                    nouveaux_inconnus.append(piste)

            presence.mettre_a_jour(len(pistes) > 0)
            annotee = dessiner(image.copy(), pistes, inference_ms)
            flux.mettre_a_jour(annotee)

            connus = sorted({p["nom"] for p in pistes if p["nom"]})
            inconnus = sum(1 for p in pistes if p["alerte"])
            etat = {
                "presence": "ON" if presence.active else "OFF",
                "personnes": len(pistes),
                "connus": connus,
                "inconnus": inconnus,
                "inference_ms": inference_ms,
            }
            # Publication à chaque changement, et au moins toutes les 2 s pour les courbes.
            resume = (etat["presence"], tuple(connus), inconnus)
            if resume != etat_precedent or maintenant - dernier_envoi > 2:
                client.publish(TOPIC_ETAT, json.dumps(etat), retain=True)
                dernier_envoi, etat_precedent = maintenant, resume

            if nouveaux_inconnus:
                if maintenant - derniere_annonce > INTERVALLE_ANNONCES:
                    annoncer(voix, ANNONCE)
                    derniere_annonce = maintenant
                confirmee = maintenant - dernier_pir["t"] < 10
                horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
                chemin = DONNEES / "captures" / f"inconnu-{horodatage}.jpg"
                cv2.imwrite(str(chemin), annotee)
                client.publish(TOPIC_IMAGE, cv2.imencode(".jpg", annotee)[1].tobytes(), retain=True)
                client.publish(TOPIC_ANNONCE, json.dumps({"message": ANNONCE, "inconnus": inconnus,
                                                          "horodatage": horodatage}))
                envoyer_alerte(
                    cfg, "ia_vision", "personne_inconnue", "critique" if confirmee else "haute",
                    f"Personne non enregistrée détectée ({inconnus} inconnu(s)"
                    + (f", {len(connus)} personne(s) identifiée(s) : {', '.join(connus)}" if connus else "")
                    + ")" + (", mouvement confirmé par le PIR" if confirmee else ""),
                    {"inconnus": inconnus, "connus": connus, "pir_concordant": confirmee, "capture": chemin.name},
                )

            if not args.sans_fenetre:
                cv2.imshow("Sentinel-X vision", annotee)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            # Rythme plafonné : sur le Celeron, analyser plus vite affamerait Mosquitto et Home Assistant.
            reste = pause_min - (time.monotonic() - debut_tour)
            if reste > 0 and not isinstance(lecteur, LecteurFichier):
                time.sleep(reste)
    finally:
        cv2.destroyAllWindows()
        client.publish(f"sentinel/ia/{NOM}/disponible", "offline", retain=True)
        client.loop_stop()


if __name__ == "__main__":
    main()
