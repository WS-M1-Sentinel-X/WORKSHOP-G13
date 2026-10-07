"""Reconnaissance des personnes enregistrées auprès du poste de sécurité.

- détection des visages : YuNet (OpenCV, ~0,2 Mo) ;
- signature d'un visage : SFace (OpenCV, ~37 Mo), un vecteur de 128 nombres ;
- deux signatures se ressemblent si leur similarité cosinus dépasse SEUIL_SIMILARITE ;
- la bibliothèque est un dossier : une signature .npy (+ une photo .jpg) par personne.

On ne stocke jamais de vidéo, seulement des signatures et une photo d'identification
par personne enregistrée (voir la note RGPD du README).
"""

import logging
import re
import time
import unicodedata

import cv2
import numpy as np

from commun import DONNEES, DOSSIER

log = logging.getLogger("sentinel-ia")

MODELES = DOSSIER / "modeles"
FICHIER_DETECTEUR = MODELES / "face_detection_yunet_2023mar.onnx"
FICHIER_RECONNAISSANCE = MODELES / "face_recognition_sface_2021dec.onnx"
BIBLIOTHEQUE = DONNEES / "personnes"
# Seuil recommandé par les auteurs de SFace pour la similarité cosinus.
SEUIL_SIMILARITE = 0.363
TAILLE_MIN_VISAGE = 40  # px : en dessous, le visage est trop petit pour être reconnu


def nom_fichier(nom):
    """« Louis Gardet » -> « louis-gardet » (sans accent ni espace)."""
    simple = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", simple.lower()).strip("-")


class Visages:
    """Détecte les visages d'une image et les compare à la bibliothèque."""

    def __init__(self, seuil_detection=0.8):
        if not FICHIER_DETECTEUR.exists() or not FICHIER_RECONNAISSANCE.exists():
            raise SystemExit(f"Modèles de visage absents de {MODELES} : lance outils/telecharger_modeles.py")
        self.detecteur = cv2.FaceDetectorYN.create(str(FICHIER_DETECTEUR), "", (640, 480), seuil_detection)
        self.reconnaissance = cv2.FaceRecognizerSF.create(str(FICHIER_RECONNAISSANCE), "")
        self.personnes = {}  # nom -> matrice (n signatures x 128)
        self.version_bibliotheque = None
        self.prochaine_verification = 0.0
        self.recharger_si_modifiee()

    # --- bibliothèque -----------------------------------------------------------
    def recharger_si_modifiee(self):
        """Relit la bibliothèque si un fichier a changé (enregistrer.py tourne à côté)."""
        if time.monotonic() < self.prochaine_verification:
            return
        self.prochaine_verification = time.monotonic() + 5
        BIBLIOTHEQUE.mkdir(parents=True, exist_ok=True)
        fichiers = sorted(BIBLIOTHEQUE.glob("*.npy"))
        version = [(f.name, f.stat().st_mtime) for f in fichiers]
        if version == self.version_bibliotheque:
            return
        self.version_bibliotheque = version
        self.personnes = {}
        for fichier in fichiers:
            donnees = np.load(fichier, allow_pickle=False)
            nom = (BIBLIOTHEQUE / f"{fichier.stem}.nom").read_text(encoding="utf-8").strip() \
                if (BIBLIOTHEQUE / f"{fichier.stem}.nom").exists() else fichier.stem
            self.personnes[nom] = donnees
        log.info("Bibliothèque : %d personne(s) enregistrée(s) %s", len(self.personnes), sorted(self.personnes))

    # --- analyse ----------------------------------------------------------------
    def detecter(self, image):
        """Liste des visages : tableaux YuNet (boîte x, y, l, h + 5 points + score)."""
        self.detecteur.setInputSize((image.shape[1], image.shape[0]))
        _, visages = self.detecteur.detect(image)
        if visages is None:
            return []
        return [v for v in visages if min(v[2], v[3]) >= TAILLE_MIN_VISAGE]

    def detecter_haut_du_corps(self, image, boite):
        """Plus grand visage dans le haut de la boîte d'une personne, ou None.

        Chercher dans un petit morceau d'image plutôt que dans toute l'image : bien moins de calcul.
        """
        x1, y1, x2, y2 = (int(v) for v in boite)
        hauteur = y2 - y1
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.shape[1], x2), min(image.shape[0], y1 + max(int(hauteur * 0.6), TAILLE_MIN_VISAGE))
        if x2 - x1 < TAILLE_MIN_VISAGE or y2 - y1 < TAILLE_MIN_VISAGE:
            return None
        trouves = self.detecter(image[y1:y2, x1:x2])
        if not trouves:
            return None
        visage = max(trouves, key=lambda v: v[2] * v[3]).copy()
        # Coordonnées du morceau -> coordonnées de l'image entière : coin de la boîte + 5 points
        # du visage (yeux, nez, coins de la bouche). Les indices 2 et 3 sont la largeur et la hauteur.
        visage[[0, 4, 6, 8, 10, 12]] += x1
        visage[[1, 5, 7, 9, 11, 13]] += y1
        return visage

    def signature(self, image, visage):
        """Vecteur de 128 nombres, normalisé, qui résume le visage."""
        vecteur = self.reconnaissance.feature(self.reconnaissance.alignCrop(image, visage)).flatten()
        return vecteur / np.linalg.norm(vecteur)

    def identifier(self, signature):
        """(nom, similarité) de la personne la plus ressemblante, nom=None si personne n'est assez proche."""
        meilleur_nom, meilleure = None, -1.0
        for nom, signatures in self.personnes.items():
            similarite = float(np.max(signatures @ signature))
            if similarite > meilleure:
                meilleur_nom, meilleure = nom, similarite
        if meilleure < SEUIL_SIMILARITE:
            return None, meilleure
        return meilleur_nom, meilleure


def iou(a, b):
    """Recouvrement de deux boîtes (x1, y1, x2, y2), de 0 (disjointes) à 1 (identiques)."""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


class Suivi:
    """Suivi simple des personnes d'une image à l'autre (par recouvrement des boîtes).

    Il retient l'identité d'une personne une fois reconnue : quelqu'un d'enregistré qui
    tourne la tête ne redevient pas « inconnu » pour autant.
    """

    def __init__(self, oubli=3.0):
        self.pistes = []
        self.oubli = oubli
        self.prochain_numero = 1

    def mettre_a_jour(self, boites):
        maintenant = time.monotonic()
        libres = list(self.pistes)
        resultat = []
        for boite in boites:
            meilleure = max(libres, key=lambda p: iou(p["boite"], boite), default=None)
            if meilleure is not None and iou(meilleure["boite"], boite) > 0.3:
                libres.remove(meilleure)
                piste = meilleure
            else:
                piste = {"numero": self.prochain_numero, "nom": None, "debut": maintenant,
                         "visage_inconnu": 0, "alerte": False}
                self.prochain_numero += 1
                self.pistes.append(piste)
            piste["boite"], piste["vu"] = boite, maintenant
            resultat.append(piste)
        self.pistes = [p for p in self.pistes if maintenant - p["vu"] < self.oubli]
        return resultat
