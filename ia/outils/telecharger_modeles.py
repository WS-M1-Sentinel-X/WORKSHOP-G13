"""Télécharge les deux petits modèles de visage d'OpenCV (YuNet + SFace) dans ia/modeles/.

    python outils/telecharger_modeles.py

Chaque fichier est vérifié par son empreinte SHA-256 : un fichier corrompu ou modifié est refusé.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from visages import FICHIER_DETECTEUR, FICHIER_RECONNAISSANCE, MODELES  # noqa: E402

DEPOT = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELES_A_TELECHARGER = {
    FICHIER_DETECTEUR: (f"{DEPOT}/face_detection_yunet/{FICHIER_DETECTEUR.name}",
                        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    FICHIER_RECONNAISSANCE: (f"{DEPOT}/face_recognition_sface/{FICHIER_RECONNAISSANCE.name}",
                             "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


def empreinte(chemin):
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def main():
    MODELES.mkdir(exist_ok=True)
    for chemin, (url, attendue) in MODELES_A_TELECHARGER.items():
        if chemin.exists() and empreinte(chemin) == attendue:
            continue
        urllib.request.urlretrieve(url, chemin)
        if empreinte(chemin) != attendue:
            chemin.unlink()
            raise SystemExit(f"Empreinte SHA-256 inattendue pour {chemin.name} : téléchargement refusé")
    sys.stdout.write(f"Modèles de visage prêts dans {MODELES}\n")


if __name__ == "__main__":
    main()
