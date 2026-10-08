# Passation Sentinel-X : partie IA et serveur

*Rédigé le jeudi 8 octobre 2026 vers 15h50, au départ de Louis. Destiné à l'équipe et à son assistant (Claude ou autre).
L'historique détaillé, avec mesures et captures, est dans `docs/journal-ia.md`.*

---

## 0. À lire en premier

- **Tout tourne sur le PC de la Région** (`192.168.0.100`) : IA vision, IA anomalies, API des alertes, Mosquitto,
  go2rtc, Home Assistant. Il ne faut rien relancer pour la démo, sauf panne.
- **Tout est sur GitHub** : le commit « feat: API des alertes, revérification des visages et passation » (8 octobre,
  ~15h50) contient tout ce qui tourne sur le serveur, et le serveur a été aligné dessus. Les seules différences locales
  normales sur le serveur sont `mosquitto/config/acl` (droits seulement, voir §8) et les fichiers `*.bak-*` (sauvegardes).
- **Ne jamais commiter de secret** : `.env`, `mosquitto/config/passwd`, `mosquitto/config/certs/`, `ia/personnes/`
  (visages) et `docs/captures/` (photos de visages) sont exclus de Git ; gardez-les ainsi.

## 1. Règles de l'équipe à respecter

| Règle | Pourquoi |
|---|---|
| **Ne pas modifier la ligne de capture caméra de `go2rtc/go2rtc.yaml`** | réglée par Tassily après beaucoup d'essais ; l'IA lit simplement le flux de go2rtc |
| Toujours **sauvegarder avant de modifier un fichier sur le serveur** (`cp fichier fichier.bak-<raison>-<date>`) | permet de revenir en arrière en une commande |
| Messages de commit au format **`feat: ...`, `fix: ...`, `docs: ...` sans portée** (pas `feat(ia):`) | le hook `commit-msg` du poste de Louis refuse sinon |
| **Aucun `print()`** dans le code Python (utiliser `logging`) | le hook `pre-commit` refuse sinon |
| `docker-compose.yml` et `ia/anomalies.py` sont en **fins de ligne Windows (CRLF)** | les garder ainsi pour éviter des diffs géants |
| **Docker contourne le pare-feu UFW** pour les ports qu'il publie | fermer un port = le retirer (ou le lier à une IP) dans le compose, pas seulement dans UFW |
| Pousser au nom de la personne qui le demande, seulement à sa demande | règle de l'équipe (Louis l'appliquait à son propre compte) |

## 2. Machine et accès

- **PC Serveur Local** : HP ProBook x360 11 G5 EE prêté par la Région, Intel Celeron N4120 (4 cœurs à 1,1 GHz),
  4 Go de RAM, Ubuntu. Sur le Wi-Fi Sentinel : **`192.168.0.100`** (pensez à lui réserver cette IP dans le routeur).
- **SSH** : `ssh sentinel@192.168.0.100`, **par clé uniquement** (`ssh-copy-id` depuis votre poste ; ne jamais écrire
  de mot de passe dans un fichier ou une conversation).
- **Dépôt** : `~/WORKSHOP-G13`. **Secrets** : `~/WORKSHOP-G13/.env` (variables `MQTT_BROKER_PASSWORD_ESP8266`,
  `MQTT_BROKER_PASSWORD_IA`, `MQTT_BROKER_PASSWORD_HOMEASSISTANT`, `API_JETON`, `MODELE_YOLO`, `TAILLE_YOLO`, `IPS_MAX`,
  `IA_APPRENTISSAGE`, `WIREGUARD_SERVERURL`).
- Le serveur **n'a pas d'identifiants GitHub** : pour pousser un commit fait sur le serveur, le récupérer depuis un poste
  qui en a (`git fetch ssh://sentinel@192.168.0.100/home/sentinel/WORKSHOP-G13 main`, vérifier, puis `git push`).

## 3. Architecture

| Service (conteneur) | Exposé sur le Wi-Fi | Rôle |
|---|---|---|
| `mqtt` (Mosquitto 2) | **8883** (MQTTS, TLS) ; 1883 interne Docker | broker dédié, comptes `esp8266`, `ia`, `homeassistant` + ACL |
| `go2rtc` | 8554 (RTSP) | capture la webcam USB (`/dev/video0`, 640×360, ~30 images/s) → `rtsp://go2rtc:8554/camera_usb` |
| `homeassistant` | 8123 | tableau de bord **TDB-SENTINEL-X** |
| `wireguard` | 51820/udp | VPN |
| `ia-vision` | non (interne : `http://ia-vision:8090/flux`) | YOLOv8n + contrôle d'accès par visage, flux vidéo annoté |
| `ia-anomalies` | non | Isolation Forest sur les mesures de l'ESP |
| `api` | non (interne : `http://api:8000`) | `POST/GET /api/v1/alerts`, historique SQLite |

**Topics MQTT utilisés par l'IA**

| Topic | Sens | Contenu |
|---|---|---|
| `station/station1/capteurs` | ESP → IA | `{"temperature": 25.9, "humidite": 47.1, "gaz": 101}` toutes les ~2 s |
| `station/station1/mouvement` | ESP → IA | `ON` / `OFF` (PIR) |
| `station/station1/buzzer/set` | HA → ESP | commande du buzzer (le compte `ia` n'a **pas** le droit d'y écrire) |
| `sentinel/ia/vision/etat` | IA → HA | présence, personnes, connus, inconnus, `inference_ms` |
| `sentinel/ia/vision/capture` | IA → HA | photo JPEG du dernier inconnu (`image.sentinel_ia_dernier_inconnu`) |
| `sentinel/ia/vision/annonce` | IA → tous | « Inconnu, identifiez-vous. » à chaque nouvel inconnu |
| `sentinel/ia/vision/enregistrement/{nom, nom/set, lancer, etat}` | HA ↔ IA | poste de sécurité (champ + bouton + état) |
| `sentinel/ia/anomalies/etat` | IA → HA | phase, score de risque 0-100, cause |
| `homeassistant/…/sentinel_ia/…/config` | IA → HA | MQTT Discovery (entités créées automatiquement) |

## 4. IA vision (`ia/vision.py`, `ia/visages.py`)

**Fonctionnement**
1. Lit `rtsp://go2rtc:8554/camera_usb` dans un fil séparé (toujours l'image la plus récente : jamais de retard).
2. **YOLOv8n exporté en OpenVINO, quantifié INT8, 320 px** (`yolov8n_320_int8_openvino_model`, intégré à l'image
   Docker, choisi automatiquement) : **~56 ms par image** sur le Celeron, **5 analyses/s**.
3. Suivi des personnes d'une image à l'autre ; visage cherché dans le haut du corps (YuNet), signature (SFace),
   comparaison à la bibliothèque (`/donnees/personnes` dans le volume `ia_donnees`).
4. Flux annoté MJPEG produit par un fil séparé : **~18 images/s**.

**Ce que l'on voit**

| Cadre | Signification |
|---|---|
| **Vert** + nom | personne enregistrée, visage vérifié il y a moins de 10 s |
| **Orange** « Nom ? » | reconnue plus tôt, mais visage pas revu depuis 10 s (tête tournée, penchée) |
| **Orange** « identification... » | visage pas encore vu ou pas encore confirmé |
| **Rouge** « INCONNU » | visage vu 3 fois sans correspondance, ou aucun visage montré pendant 8 s → alerte |

**Règles de reconnaissance** : seuil de similarité **0,45** (`SEUIL_VISAGE`), même nom sur **2 analyses d'affilée**,
**un nom sur un seul cadre**, visage **revérifié toutes les 2 s** (nom retiré si 2 revérifications contradictoires).

**Alerte « personne inconnue »** : `POST /api/v1/alerts` (gravité `haute`, **`critique`** si le PIR a détecté un
mouvement dans les 10 s), photo dans `/donnees/captures`, image MQTT, message `annonce`.

**Personnes enregistrées** (au 8 octobre, 15h45) : Joris Martins, Louis Gardet, Rafael Da Silva, Tasilimy KABA.

**Réglages** (variables d'environnement, valeurs par défaut correctes) : `MODELE_YOLO`, `TAILLE_YOLO` (320),
`IPS_MAX` (5 analyses/s), `IPS_AFFICHAGE` (20), `SEUIL_VISAGE` (0,45), `TOPIC_MOUVEMENT`.

## 5. IA anomalies (`ia/anomalies.py`)

- Isolation Forest sur une fenêtre glissante de 30 mesures (valeurs + pentes de température, humidité, gaz), **aucun seuil statique**.
- **Entraînée sur 1 200 vraies mesures de l'ESP** (terminé le 8 octobre à 14h01), modèle conservé dans le volume
  `ia_donnees` : il survit aux redémarrages. État actuel : « surveillance », risque ~33/100, « RAS ».
- Mesures impossibles rejetées (bornes ajoutées par l'équipe pendant le pentest).
- Réapprendre (≈ 40 min de salle « normale ») : `docker compose run --rm ia-anomalies python anomalies.py --reset`.

## 6. API des alertes (`api/serveur.py`)

- `POST /api/v1/alerts` (JSON : `source`, `type`, `gravite` ∈ info/basse/moyenne/haute/critique, `message`, `details`),
  `GET /api/v1/alerts?limite=50&source=ia_vision`, `GET /api/v1/sante`.
- En-tête obligatoire : `Authorization: Bearer <API_JETON>` (jeton dans le `.env` du serveur).
- Python standard + SQLite (volume `api_donnees`), non-root, requêtes SQL paramétrées, non exposée sur le Wi-Fi.
- Testée : 401 sans jeton, 400 JSON invalide, 422 gravité inconnue, injection SQL stockée comme simple texte.

## 7. Home Assistant

- Tableau de bord **TDB-SENTINEL-X** : grande carte vidéo (flux brut de go2rtc, **sans cadres**), tuiles IA, photo
  du dernier inconnu, courbes de l'ESP.
- **À faire pour voir les cadres en direct** : *Paramètres > Appareils et services > Ajouter une intégration >
  « MJPEG IP Camera »*, URL `http://ia-vision:8090/flux`, nom « Flux IA » ; puis remplacer `camera.192_168_0_100` par
  `camera.flux_ia` sur la grande carte (vue « en direct »). HA joint bien cette adresse (vérifié).
- **Poste de sécurité** (appareil « Sentinel-X IA ») : champ « Nom à enregistrer » + bouton « Enregistrer la personne
  devant la caméra » + capteur « Enregistrement ». Réenregistrer quelqu'un **ajoute** des photos (30 au plus) : faites
  passer chaque personne 2 fois (de face, puis légèrement de côté).

## 8. Commandes utiles (sur le serveur, dans `~/WORKSHOP-G13`)

```bash
docker compose ps                                        # état des services
docker compose logs -f ia-vision ia-anomalies api        # journaux
# Après une modification de code IA : reconstruire et relancer la vision seule
docker compose build ia-vision && docker compose up -d --no-deps ia-vision
# Enregistrer / lister / retirer une personne en ligne de commande
docker compose exec ia-vision python enregistrer.py --nom "Prénom Nom" --source http://127.0.0.1:8090/flux
docker compose exec ia-vision python enregistrer.py --liste
docker compose exec ia-vision python enregistrer.py --supprimer "Prénom Nom"
# Dernières alertes reçues par l'API
docker exec sentinel-api python -c "import os,json,urllib.request as u; r=u.Request('http://127.0.0.1:8000/api/v1/alerts?limite=5', headers={'Authorization':'Bearer '+os.environ['API_JETON']}); [print(a['recue_le'],a['gravite'],a['message']) for a in json.load(u.urlopen(r))]"
# Ressources (le Celeron doit rester sous une charge de ~3 sur 4)
docker stats --no-stream ; cat /proc/loadavg
```

**Piège `mosquitto/config/acl`** : le conteneur Mosquitto le passe en `0700`, propriétaire `mosquitto`, au démarrage ;
Git ne peut alors plus le lire (`Permission denied`) et il apparaît toujours « modifié » (droits seulement). Pour un
`git pull` qui doit le toucher : `docker exec sentinel-mqtt chmod 0644 /mosquitto/config/acl`, puis `git pull`, puis
`docker exec sentinel-mqtt sh -c "chown mosquitto:mosquitto /mosquitto/config/acl && chmod 0700 /mosquitto/config/acl"`
et `docker kill -s HUP sentinel-mqtt` pour recharger les droits sans couper le broker.

## 9. Sauvegardes et retour arrière (sur le serveur)

| Sauvegarde | Contenu |
|---|---|
| `ia/*.bak-avant-<raison>-<date>` | chaque version précédente de `vision.py` / `visages.py` (restauration : la recopier, puis reconstruire `ia-vision`) |
| `~/sauvegarde-avant-api-20261008-1502/` | compose, `.env`, `.env.example` avant l'ajout de l'API |
| `~/sauvegarde-avant-bouton-20261008-1436/` | code IA + ACL avant le bouton HA ; branche `sauvegarde/avant-bouton-20261008-1436` |
| `~/sauvegarde-avant-push-20261008-1216.tgz` + branche `sauvegarde/avant-push-20261008-1216` | dépôt complet avant le push des commits de Tassily |
| `~/sauvegarde-avant-optim-20261007-1335.tgz` | état d'avant les optimisations |
| `git stash list` | copies manuelles mises de côté avant certains `git pull` |

Annuler des commits déjà poussés, sans réécrire l'historique : `git revert --no-edit <ancien>..<nouveau> && git push`.

## 10. Reste à faire (par priorité)

1. **Carte vidéo avec cadres dans HA** (§7, 2 minutes).
2. **Enregistrer chaque membre 2 fois** avec le bouton HA (meilleure reconnaissance de profil).
3. **Buzzer sur « inconnu »** (optionnel) : ajouter `topic write station/+/buzzer/set` au compte `ia` dans
   `mosquitto/config/acl`, puis publier la commande dans `vision.py` au moment de l'alerte (format défini par le
   firmware C++).
4. **Dossier PDF, section « Documentation de l'IA »** : reprendre `docs/journal-ia.md` et `docs/decision-serveur.md`
   (mesures 512 ms → 56 ms, contrôle d'accès, RGPD) et les captures de `docs/captures/` (non versionnées).
5. **Vidéo teaser (30-50 s)** : filmer le flux annoté avec un inconnu en rouge et une personne en vert.
6. **`Code.zip`** : README à jour, aucun secret, pas de `ia/personnes/` ni `docs/captures/`.

## 11. Scénario de démo conseillé (3 min)

1. Tableau de bord TDB-SENTINEL-X ouvert, flux IA avec cadres (§7) : un membre enregistré devant la caméra → **vert** + nom ; montrer `inference_ms` (~56 ms).
2. Une personne non enregistrée entre → **rouge « INCONNU »**, photo dans « Dernier inconnu », alerte reçue par l'API (gravité **critique** si le PIR l'a vue).
3. Au poste de sécurité (bouton HA), on l'enregistre en 5 photos → elle passe en **vert** quelques secondes après.
4. Souffler près du MQ-2 ou chauffer légèrement le DHT22 → le **score de risque** de l'IA anomalies monte, cause expliquée.
5. Phrase clé pour le jury : « l'IA tient sous 100 ms sur un Celeron de 2019 grâce à OpenVINO et la quantification INT8, et elle ne confond pas un inconnu avec un employé : seuil relevé, double confirmation, revérification du visage. »
