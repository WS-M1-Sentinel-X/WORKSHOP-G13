"""API Sentinel-X : reçoit et historise les alertes (exigence du sujet : POST /api/v1/alerts).

- POST /api/v1/alerts      enregistre une alerte JSON (IA vision, IA anomalies, ESP...)
- GET  /api/v1/alerts      dernières alertes (?limite=50, ?source=ia_vision)
- GET  /api/v1/sante       état de l'API et nombre d'alertes stockées

Base SQLite dans DOSSIER_DONNEES (volume Docker). Si API_JETON est défini, chaque requête
doit porter l'en-tête « Authorization: Bearer <jeton> ». Bibliothèque standard uniquement.
"""

import hmac
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.getenv("API_PORT", "8000"))
JETON = os.getenv("API_JETON", "")
BASE = Path(os.getenv("DOSSIER_DONNEES", Path(__file__).resolve().parent)) / "sentinel.db"
TAILLE_MAX = 16 * 1024  # octets : une alerte n'a pas besoin de plus
CHAMPS_OBLIGATOIRES = ("source", "type", "gravite", "message")
GRAVITES = ("info", "basse", "moyenne", "haute", "critique")
log = logging.getLogger("sentinel-api")
verrou = threading.Lock()


def ouvrir_base():
    BASE.parent.mkdir(parents=True, exist_ok=True)
    base = sqlite3.connect(BASE, check_same_thread=False)
    base.execute("""CREATE TABLE IF NOT EXISTS alertes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recue_le TEXT NOT NULL,
        horodatage TEXT,
        source TEXT NOT NULL,
        type TEXT NOT NULL,
        gravite TEXT NOT NULL,
        message TEXT NOT NULL,
        details TEXT NOT NULL)""")
    base.commit()
    return base


def valider(alerte):
    """Renvoie un message d'erreur, ou None si l'alerte est acceptable."""
    if not isinstance(alerte, dict):
        return "le corps doit être un objet JSON"
    for champ in CHAMPS_OBLIGATOIRES:
        if not isinstance(alerte.get(champ), str) or not alerte[champ].strip():
            return f"champ « {champ} » manquant ou vide"
        if len(alerte[champ]) > (500 if champ == "message" else 50):
            return f"champ « {champ} » trop long"
    if alerte["gravite"] not in GRAVITES:
        return f"gravité inconnue (attendu : {', '.join(GRAVITES)})"
    if not isinstance(alerte.get("details", {}), dict):
        return "« details » doit être un objet JSON"
    return None


def creer_gestionnaire(base):
    class Gestionnaire(BaseHTTPRequestHandler):
        server_version = "SentinelAPI"
        sys_version = ""

        def repondre(self, code, corps):
            donnees = json.dumps(corps, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(donnees)))
            self.end_headers()
            self.wfile.write(donnees)

        def autorise(self):
            if not JETON:
                return True
            recu = self.headers.get("Authorization", "")
            if hmac.compare_digest(recu, f"Bearer {JETON}"):
                return True
            self.repondre(401, {"erreur": "jeton manquant ou invalide"})
            return False

        def do_POST(self):
            if urlparse(self.path).path != "/api/v1/alerts":
                self.repondre(404, {"erreur": "route inconnue"})
                return
            if not self.autorise():
                return
            taille = int(self.headers.get("Content-Length") or 0)
            if taille <= 0 or taille > TAILLE_MAX:
                self.repondre(413 if taille > TAILLE_MAX else 400, {"erreur": "corps vide ou trop gros"})
                return
            try:
                alerte = json.loads(self.rfile.read(taille))
            except ValueError:
                self.repondre(400, {"erreur": "JSON invalide"})
                return
            erreur = valider(alerte)
            if erreur:
                self.repondre(422, {"erreur": erreur})
                return
            recue_le = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with verrou:
                curseur = base.execute(
                    "INSERT INTO alertes (recue_le, horodatage, source, type, gravite, message, details) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",  # requête paramétrée : pas d'injection SQL
                    (recue_le, str(alerte.get("horodatage", ""))[:40], alerte["source"], alerte["type"],
                     alerte["gravite"], alerte["message"], json.dumps(alerte.get("details", {}), ensure_ascii=False)),
                )
                base.commit()
            log.info("Alerte %d (%s, %s) : %s", curseur.lastrowid, alerte["source"], alerte["gravite"], alerte["message"])
            self.repondre(201, {"id": curseur.lastrowid, "recue_le": recue_le})

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/api/v1/sante":
                with verrou:
                    total = base.execute("SELECT COUNT(*) FROM alertes").fetchone()[0]
                self.repondre(200, {"statut": "ok", "alertes": total})
                return
            if url.path != "/api/v1/alerts":
                self.repondre(404, {"erreur": "route inconnue"})
                return
            if not self.autorise():
                return
            parametres = parse_qs(url.query)
            try:
                limite = max(1, min(500, int(parametres.get("limite", ["50"])[0])))
            except ValueError:
                limite = 50
            source = parametres.get("source", [None])[0]
            requete = "SELECT id, recue_le, horodatage, source, type, gravite, message, details FROM alertes"
            valeurs = []
            if source:
                requete += " WHERE source = ?"
                valeurs.append(source)
            requete += " ORDER BY id DESC LIMIT ?"
            valeurs.append(limite)
            with verrou:
                lignes = base.execute(requete, valeurs).fetchall()
            colonnes = ("id", "recue_le", "horodatage", "source", "type", "gravite", "message", "details")
            alertes = [dict(zip(colonnes, ligne)) for ligne in lignes]
            for alerte in alertes:
                alerte["details"] = json.loads(alerte["details"])
            self.repondre(200, alertes)

        def log_message(self, format, *args):
            log.debug("%s %s", self.address_string(), format % args)

    return Gestionnaire


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    base = ouvrir_base()
    if not JETON:
        log.warning("API_JETON vide : l'API accepte les requêtes sans authentification")
    serveur = ThreadingHTTPServer(("0.0.0.0", PORT), creer_gestionnaire(base))
    log.info("API Sentinel-X sur le port %d, base %s", PORT, BASE)
    serveur.serve_forever()


if __name__ == "__main__":
    main()
