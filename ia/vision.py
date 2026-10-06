"""IA vision : détection de présence humaine sur la webcam USB avec YOLOv8n.

- lit la webcam branchée sur le PC serveur, image ramenée à 640 px de large
  en gardant ses proportions (640x480 en 4:3, 640x360 pour le flux 854x480 de go2rtc) ;
- YOLOv8n pré-entraîné (COCO), on ne garde que la classe 0 = "person" ;
- publie l'état sur MQTT (Home Assistant le voit via MQTT Discovery) ;
- sert le flux vidéo annoté en MJPEG (http://<pc>:8090/flux) pour HA et le dashboard ;
- à chaque nouvelle intrusion : POST /api/v1/alerts + capture JPEG horodatée (preuve).

    python vision.py                 # webcam 0
    python vision.py --camera 1      # autre webcam
    python vision.py --source video.mp4 --sans-fenetre   # tester sur une vidéo
    python vision.py --source rtsp://127.0.0.1:8554/usb  # flux relayé par go2rtc (stack Docker)

Sur la stack WORKSHOP-G13, FFmpeg capture déjà la webcam pour MediaMTX : sous Windows
une webcam ne s'ouvre qu'une fois, donc on lit le flux RTSP de go2rtc (SOURCE_VIDEO dans .env).
"""

import argparse
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
from ultralytics import YOLO

from commun import DOSSIER, charger_config, creer_client_mqtt, declarer_entite, envoyer_alerte

NOM = "vision"
TOPIC_ETAT = "sentinel/ia/vision/etat"
TOPIC_IMAGE = "sentinel/ia/vision/capture"
LARGEUR, HAUTEUR = 640, 480
log = logging.getLogger("sentinel-ia")


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
    declarer_entite(client, cfg, NOM, "sensor", "personnes", {
        **commun, "name": "Personnes détectées", "icon": "mdi:account-group",
        "state_class": "measurement", "value_template": "{{ value_json.personnes }}",
    })
    declarer_entite(client, cfg, NOM, "sensor", "inference_ms", {
        **commun, "name": "Temps d'inférence YOLO", "unit_of_measurement": "ms",
        "state_class": "measurement", "icon": "mdi:timer-outline",
        "value_template": "{{ value_json.inference_ms }}",
    })
    declarer_entite(client, cfg, NOM, "camera", "derniere_intrusion", {
        "name": "Dernière intrusion", "topic": TOPIC_IMAGE,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--camera", type=int, default=0, help="index de la webcam")
    parser.add_argument("--source", default=None,
                        help="fichier vidéo ou flux rtsp:// à la place de la webcam (défaut : SOURCE_VIDEO du .env)")
    parser.add_argument("--modele", default="yolov8n.pt")
    parser.add_argument("--confiance", type=float, default=0.5)
    parser.add_argument("--port-flux", type=int, default=8090)
    parser.add_argument("--sans-fenetre", action="store_true", help="ne pas ouvrir de fenêtre OpenCV")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = charger_config()
    (DOSSIER / "captures").mkdir(exist_ok=True)
    source = args.source if args.source is not None else cfg["source_video"]
    # Un flux réseau (rtsp://, http://) est "vivant" : on attend s'il coupe ; un fichier se termine.
    fichier_video = bool(source) and "://" not in source

    modele = YOLO(args.modele)  # téléchargé automatiquement la première fois (~6 Mo)
    camera = cv2.VideoCapture(source or args.camera)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, LARGEUR)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, HAUTEUR)
    if not camera.isOpened():
        raise SystemExit(f"Source vidéo introuvable : {source}" if source else "Webcam introuvable : essaie --camera 1")

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

    flux = FluxMJPEG()
    flux.demarrer(args.port_flux)
    presence = Presence()
    dernier_envoi = 0.0
    etat_precedent = None
    echecs = 0

    try:
        while True:
            ok, image = camera.read()
            if not ok:
                if fichier_video:
                    break
                time.sleep(0.1)
                echecs += 1
                if source and echecs >= 50:  # flux RTSP muet depuis ~5 s : on se reconnecte
                    log.warning("Flux vidéo coupé, reconnexion à %s", source)
                    camera.release()
                    camera.open(source)
                    echecs = 0
                continue
            echecs = 0
            if image.shape[1] != LARGEUR:
                # On garde les proportions : écraser un flux 16:9 en 4:3 déformerait les silhouettes.
                hauteur = round(image.shape[0] * LARGEUR / image.shape[1])
                image = cv2.resize(image, (LARGEUR, hauteur))

            debut = time.perf_counter()
            resultat = modele(image, imgsz=LARGEUR, classes=[0], conf=args.confiance, verbose=False)[0]
            inference_ms = round((time.perf_counter() - debut) * 1000, 1)

            personnes = len(resultat.boxes)
            confiance = float(resultat.boxes.conf.max()) if personnes else 0.0
            nouvelle_intrusion = presence.mettre_a_jour(personnes > 0)
            annotee = resultat.plot()
            cv2.putText(annotee, f"{inference_ms} ms", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            flux.mettre_a_jour(annotee)

            etat = {
                "presence": "ON" if presence.active else "OFF",
                "personnes": personnes,
                "confiance": round(confiance, 2),
                "inference_ms": inference_ms,
            }
            # Publication à chaque changement, et au moins toutes les 2 s pour les courbes.
            if etat["presence"] != etat_precedent or time.monotonic() - dernier_envoi > 2:
                client.publish(TOPIC_ETAT, json.dumps(etat), retain=True)
                dernier_envoi, etat_precedent = time.monotonic(), etat["presence"]

            if nouvelle_intrusion:
                confirmee = time.monotonic() - dernier_pir["t"] < 10
                horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
                chemin = DOSSIER / "captures" / f"intrusion-{horodatage}.jpg"
                cv2.imwrite(str(chemin), annotee)
                client.publish(TOPIC_IMAGE, cv2.imencode(".jpg", annotee)[1].tobytes(), retain=True)
                envoyer_alerte(
                    cfg, "ia_vision", "intrusion", "critique" if confirmee else "haute",
                    ("Intrusion confirmée (PIR + caméra)" if confirmee else "Présence humaine détectée")
                    + f" : {personnes} personne(s), confiance {confiance:.0%}",
                    {"personnes": personnes, "confiance": round(confiance, 2),
                     "pir_concordant": confirmee, "capture": chemin.name},
                )

            if not args.sans_fenetre:
                cv2.imshow("Sentinel-X vision", annotee)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        camera.release()
        cv2.destroyAllWindows()
        client.publish(f"sentinel/ia/{NOM}/disponible", "offline", retain=True)
        client.loop_stop()


if __name__ == "__main__":
    main()
