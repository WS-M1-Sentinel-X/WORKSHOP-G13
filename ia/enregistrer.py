"""Poste de sécurité : enregistrer, lister ou retirer les personnes autorisées.

    python enregistrer.py --nom "Louis Gardet" --camera 1      # 5 prises de vue sur la webcam USB
    python enregistrer.py --nom "Louis Gardet" --photo moi.jpg # depuis une photo
    python enregistrer.py --liste
    python enregistrer.py --supprimer "Louis Gardet"

La personne doit être seule face à la caméra, visage dégagé. vision.py recharge la
bibliothèque tout seul (pas besoin de le redémarrer).
"""

import argparse
import logging
import time

import cv2

from commun import charger_config
from visages import BIBLIOTHEQUE, Visages, nom_fichier, sauvegarder_personne

log = logging.getLogger("sentinel-ia")


def un_seul_visage(visages, image):
    trouves = visages.detecter(image)
    if len(trouves) != 1:
        return None, f"{len(trouves)} visage(s) visible(s), il en faut exactement 1"
    return trouves[0], None


def depuis_photo(visages, chemin):
    image = cv2.imread(chemin)
    if image is None:
        raise SystemExit(f"Photo illisible : {chemin}")
    visage, erreur = un_seul_visage(visages, image)
    if erreur:
        raise SystemExit(erreur)
    return [visages.signature(image, visage)], image


def depuis_camera(visages, source, prises):
    camera = cv2.VideoCapture(source)
    if not camera.isOpened():
        raise SystemExit(f"Source vidéo introuvable : {source}")
    signatures, photo, dernier_message = [], None, ""
    limite = time.monotonic() + 60
    try:
        while len(signatures) < prises and time.monotonic() < limite:
            # On vide le tampon du flux pour analyser une image actuelle.
            for _ in range(5):
                camera.grab()
            ok, image = camera.read()
            if not ok:
                continue
            visage, erreur = un_seul_visage(visages, image)
            if erreur:
                if erreur != dernier_message:
                    log.info("%s", erreur)
                    dernier_message = erreur
                continue
            signatures.append(visages.signature(image, visage))
            photo = image
            log.info("Prise %d/%d : bouge un peu la tête entre deux prises", len(signatures), prises)
            time.sleep(0.6)
    finally:
        camera.release()
    if len(signatures) < prises:
        raise SystemExit("Pas assez de prises de vue en 60 s : rapproche-toi de la caméra, seul et visage dégagé")
    return signatures, photo


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--nom", help="nom de la personne à enregistrer")
    action.add_argument("--liste", action="store_true", help="afficher les personnes enregistrées")
    action.add_argument("--supprimer", metavar="NOM", help="retirer une personne (droit à l'effacement)")
    parser.add_argument("--photo", help="enregistrer depuis une photo plutôt que la caméra")
    parser.add_argument("--source", default=None, help="flux vidéo (défaut : SOURCE_VIDEO du .env)")
    parser.add_argument("--camera", type=int, default=0, help="index de la webcam si pas de flux")
    parser.add_argument("--prises", type=int, default=5)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = charger_config()
    BIBLIOTHEQUE.mkdir(parents=True, exist_ok=True)

    if args.liste:
        noms = sorted(f.read_text(encoding="utf-8").strip() for f in BIBLIOTHEQUE.glob("*.nom"))
        log.info("%d personne(s) enregistrée(s) : %s", len(noms), ", ".join(noms) or "aucune")
        return

    if args.supprimer:
        base = nom_fichier(args.supprimer)
        fichiers = [f for f in BIBLIOTHEQUE.glob(f"{base}.*")]
        for fichier in fichiers:
            fichier.unlink()
        log.info("%s : %s", args.supprimer, "supprimé(e)" if fichiers else "introuvable")
        return

    visages = Visages()
    if args.photo:
        signatures, photo = depuis_photo(visages, args.photo)
    else:
        source = args.source if args.source is not None else (cfg["source_video"] or args.camera)
        signatures, photo = depuis_camera(visages, source, args.prises)

    sauvegarder_personne(args.nom, signatures, photo)


if __name__ == "__main__":
    main()
