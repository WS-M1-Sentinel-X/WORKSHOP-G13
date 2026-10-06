"""Fausse API qui affiche les alertes reçues, en attendant celle des devs.

    python outils/fausse_api.py        # écoute sur http://127.0.0.1:8000/api/v1/alerts
puis dans .env : API_ALERTES=http://127.0.0.1:8000/api/v1/alerts
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger("fausse-api")


class Gestionnaire(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/v1/alerts":
            self.send_error(404)
            return
        corps = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        log.info("ALERTE REÇUE : %s", json.dumps(json.loads(corps), ensure_ascii=False))
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"statut": "ok"}')

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    HTTPServer(("127.0.0.1", 8000), Gestionnaire).serve_forever()
