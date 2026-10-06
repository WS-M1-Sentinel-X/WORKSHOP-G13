# Sentinel-X : brancher l'IA sur Home Assistant

## La recommandation, en une phrase

**Tes deux IA restent des scripts Python autonomes sur le PC serveur. Elles publient leurs résultats sur Mosquitto avec MQTT Discovery, ce qui les fait apparaître automatiquement dans Home Assistant, et elles envoient chaque alerte en `POST /api/v1/alerts` à l'API de l'équipe.**

Tu n'écris donc **rien dans Home Assistant lui-même** : pas d'add-on, pas d'intégration custom, pas de code à recompiler. Home Assistant se contente d'écouter le même broker MQTT que tout le monde.

```
Webcam USB ──> vision.py (YOLOv8n) ──┐
                                     ├── MQTT (Mosquitto :8883) ──> Home Assistant (entités auto, automatisations, buzzer/LED)
ESP8266 ──> sentinel/capteurs ──> anomalies.py (Isolation Forest) ──┘
                                     └── POST /api/v1/alerts ──> API de l'équipe ──> BDD + dashboard
vision.py ──> flux MJPEG :8090/flux ──> carte caméra HA (et dashboard des devs)
```

> **Rester conforme au sujet avec Home Assistant :** HA est un plus, pas un remplaçant. Le firmware de l'ESP8266 doit rester du C++ écrit par l'équipe (ESPHome génère le firmware à votre place à partir de YAML, ce n'est pas la même chose), et l'API avec `POST /api/v1/alerts` doit exister : c'est pour ça que les scripts IA l'appellent aussi.

### Pourquoi pas un add-on ou une intégration Home Assistant ?

- Les **add-ons** n'existent que sur Home Assistant OS / Supervised. Sur un PC portable avec Docker, vous aurez **Home Assistant Container**, qui n'a pas d'add-ons.
- Une **intégration custom** (`custom_components/`) s'écrit en Python asynchrone dans les entrailles de HA : long à apprendre, et un plantage de YOLO ferait tomber HA.
- **Frigate** (l'add-on vidéo connu) ferait la détection à ta place : le jury veut voir *ton* IA.
- Avec MQTT, si HA tombe, tes alertes arrivent quand même à l'API. Chaque brique est indépendante : c'est l'esprit de l'architecture v2.

---

## Les fichiers

| Fichier | Rôle |
|---|---|
| `commun.py` | Configuration, connexion MQTT (TLS), MQTT Discovery, envoi `POST /api/v1/alerts` |
| `vision.py` | YOLOv8n sur la webcam, 640 px de large (proportions gardées), anti-rebond, flux MJPEG, capture de preuve, fusion avec le PIR |
| `anomalies.py` | Isolation Forest sur fenêtre glissante, score de risque 0-100 expliqué, aucun seuil statique |
| `simulateur.py` | Faux ESP8266 : mesures normales puis scénario `surchauffe`, `fuite` ou `pic` |
| `outils/fausse_api.py` | Fausse API qui affiche les alertes, en attendant celle des devs |
| `home-assistant/` | Automatisations (buzzer/LED), carte de dashboard, extrait de `configuration.yaml` |
| `.env.example` | Modèle de configuration (copie-le en `.env`, jamais commité) |

## Lancer dans Docker (le mode de la démo)

Les deux IA sont des services du `docker-compose.yml` racine, construits depuis `ia/Dockerfile` (une image commune, PyTorch CPU, YOLOv8n intégré, utilisateur non-root) :

```bash
docker compose up -d --build ia-vision ia-anomalies
docker compose logs -f ia-vision ia-anomalies
```

- `ia-vision` lit `rtsp://go2rtc:8554/<CAMERA_STREAM>` (dans le `.env` racine : `camera_usb` sur le serveur Sentinel) et sert le flux annoté sur http://<serveur>:8090/flux.
- `ia-anomalies` apprend sur `IA_APPRENTISSAGE` mesures (1 200 par défaut, soit 20 min), puis surveille.
- Le modèle appris et les captures d'intrusion sont dans le volume `ia_donnees` : ils survivent aux redémarrages. Pour réapprendre : `docker compose run --rm ia-anomalies python anomalies.py --reset`.
- `API_ALERTES` (`.env` racine) : l'adresse de l'API des devs, vide tant qu'elle n'existe pas (les alertes sont alors journalisées).

> **Pourquoi la vision peut être dans Docker :** la webcam USB est branchée sur le serveur et publiée par go2rtc sur le réseau. Le conteneur n'a donc pas besoin d'accéder au périphérique USB, seulement au flux RTSP. YOLO y tourne sur le CPU seul : vérifie `sensor.sentinel_ia_inference_ms` (objectif < 100 ms).

## Lancer en 5 minutes (sur ton PC, sans le boîtier)

```bash
cd ia
python -m venv .venv && source .venv/bin/activate     # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Le `.env.example` est déjà réglé pour la stack Docker de ce dépôt : Mosquitto sur `127.0.0.1:1883` sans TLS, la webcam lue via go2rtc (`SOURCE_VIDEO=rtsp://127.0.0.1:8554/usb`) et la fausse API sur le port 8000. Il suffit donc de démarrer la stack depuis la racine du dépôt :

```bash
docker compose up -d mqtt mediamtx go2rtc homeassistant
```

> **Pourquoi lire la webcam via go2rtc ?** Sous Windows, une webcam USB ne peut être ouverte que par un seul programme. Comme FFmpeg la capture déjà pour MediaMTX (voir le README racine), `vision.py` lit le flux relayé par go2rtc au lieu d'ouvrir la caméra. Pour tester sans la stack, vide `SOURCE_VIDEO` : le script ouvre alors la webcam directement.

Puis, dans 4 terminaux :

```bash
python outils/fausse_api.py                                   # 1. reçoit les alertes
python anomalies.py --reset --apprentissage 300               # 2. IA prédictive
python simulateur.py --scenario surchauffe --normal 400 --periode 0.05   # 3. faux boîtier, accéléré
python vision.py                                              # 4. IA vision (fenêtre + http://localhost:8090/flux)
# sans webcam : python vision.py --source une_video.mp4
```

Ce que tu dois voir : l'IA prédictive apprend sur 300 mesures, puis lève **une seule** alerte pendant que la température n'est qu'à 24-26 °C, parce que c'est la *pente* qui trahit la surchauffe, pas la valeur. C'est exactement ce que le sujet appelle « détecter avant le seuil critique ».

### Résultats mesurés (5 octobre, simulateur, 2 tirages par scénario)

| Scénario | Ce qui se passe | Résultat |
|---|---|---|
| `normal` (2 000 mesures) | ambiance stable + bruit | **0 fausse alerte** |
| `surchauffe` | +0,04 °C par mesure, gaz qui frémit | alerte à **24,3 et 25,9 °C** (départ 23 °C), cause « pente température » ou « température » |
| `fuite` | gaz +1,2 par mesure | alerte à gaz **213 et 311** (normal ≈ 180, max 1023) |
| `pic` | un saut isolé de +8 °C toutes les 40 mesures | **ignoré exprès** : un pic d'une seule mesure ressemble à un faux contact du DHT22, l'anti-rebond le filtre |
| YOLOv8n | photo de bus en 640×480, CPU sans GPU | 4 personnes trouvées, confiance 88 %, **59 à 81 ms** par image |

Ces chiffres sont parfaits pour la documentation IA du dossier : refais-les sur tes vraies données mercredi.

### Une limite à connaître (et à dire au jury)

Une Isolation Forest **sature** en dehors de ce qu'elle a appris : un gaz à 900 peut obtenir le même score que le point le plus extrême vu pendant l'apprentissage, donc parfois « normal ». Le script en tient compte : une fois l'alerte levée, elle ne retombe que lorsque le score redevient **typique** (< 40) pendant 10 mesures. Autre conséquence : l'apprentissage doit couvrir toute la variation normale. Sur le vrai boîtier, apprends plutôt **15 à 30 minutes** (`--apprentissage 1200` à une mesure par seconde) en faisant varier un peu la pièce.

Quand l'équipe cloud aura monté Mosquitto en MQTTS : remets `MQTT_PORT=8883`, `MQTT_CA=` vers le `ca.crt` de la CA locale, un utilisateur `ia` avec ses ACL, et `API_ALERTES=https://192.168.10.1/api/v1/alerts`.

## Côté Home Assistant

1. Home Assistant tourne déjà dans la stack (`sentinel-homeassistant`, http://localhost:8123).
2. Dans HA : **Paramètres > Appareils et services > Ajouter > MQTT**, broker `mqtt` (le nom du service Docker), port 1883. Quand Mosquitto passera en MQTTS : port 8883 + TLS avec le `ca.crt`. La découverte (préfixe `homeassistant`) est active par défaut.
3. Lancer tes scripts : l'appareil **« Sentinel-X IA »** apparaît tout seul avec ses entités :
   - `binary_sensor.sentinel_ia_presence_humaine`, `sensor.sentinel_ia_personnes`, `sensor.sentinel_ia_inference_ms`, `camera.sentinel_ia_derniere_intrusion`
   - `sensor.sentinel_ia_score_risque`, `binary_sensor.sentinel_ia_anomalie`, `sensor.sentinel_ia_cause`, `sensor.sentinel_ia_phase`
4. Ajouter l'intégration **MJPEG IP Camera** sur `http://ia-vision:8090/flux` (nomme-la « Flux webcam ») : HA et l'IA sont sur le même réseau Docker. Si tu lances `vision.py` hors Docker, utilise l'IP du PC à la place.
5. Copier `home-assistant/automations.yaml` (buzzer + LED rouge sur intrusion ou anomalie) dans la config de HA, qui vit dans le volume Docker `ha_config` : `docker cp home-assistant/automations.yaml sentinel-homeassistant:/config/automations.yaml`, puis **Outils de développement > YAML > Recharger les automatisations**. Coller ensuite la carte `carte-dashboard.yaml` dans un tableau de bord.

Si un identifiant d'entité diffère chez vous, regarde-le dans l'appareil « Sentinel-X IA » et corrige-le dans les YAML.

### Le contrat JSON (à valider avec les devs dès aujourd'hui)

Message capteur publié par l'ESP8266 sur `sentinel/capteurs` :
```json
{"id": "sentinel-x-01", "temperature": 23.4, "humidite": 45.1, "gaz": 182, "mouvement": 0, "ts": "2026-10-06T09:12:00+00:00"}
```
Alerte envoyée par l'IA en `POST /api/v1/alerts` :
```json
{"source": "ia_vision", "type": "intrusion", "gravite": "critique",
 "message": "Intrusion confirmée (PIR + caméra) : 1 personne(s), confiance 87%",
 "details": {"personnes": 1, "confiance": 0.87, "pir_concordant": true, "capture": "intrusion-20261006-091200.jpg"},
 "horodatage": "2026-10-06T09:12:00+00:00"}
```
Si les devs choisissent d'autres noms de champs, il suffit de changer `GRANDEURS` dans `anomalies.py`.

---

## Les notions, avec tes 3 questions

### MQTT Discovery
Un script publie un message de configuration « retenu » sur `homeassistant/<type>/<id>/config` ; HA le lit et crée l'entité tout seul.
- **À quoi ça sert dans la vraie vie ?** À brancher un nouvel appareil sans toucher à la configuration du serveur domotique.
- **Qui l'utilise et pourquoi ?** Zigbee2MQTT, Tasmota, les fabricants de capteurs industriels : ils veulent que leurs appareils « apparaissent » chez le client sans manipulation.
- **Quel problème ça résout ?** Le couplage : ton IA n'a pas besoin de connaître HA, et HA n'a pas besoin de connaître ton code.

### YOLOv8n (vision)
Un réseau de neurones qui trouve et encadre des objets en une seule passe ; le « n » (nano) est la version la plus légère. Pré-entraîné sur COCO, il connaît déjà la classe `person` : **aucun entraînement nécessaire**.
- **À quoi ça sert ?** Détecter des personnes, véhicules, objets en temps réel sur une caméra.
- **Qui l'utilise ?** Vidéosurveillance (Axis, Hikvision), comptage en magasin, robots, voitures.
- **Quel problème ça résout ?** Un détecteur de mouvement réagit à tout ; YOLO dit *quoi* bouge.

Le script réduit l'image à 640 px de large en gardant ses proportions (640×360 pour le flux 854×480 de go2rtc) et affiche le temps d'inférence (`sensor.sentinel_ia_inference_ms`) : c'est ta preuve des < 100 ms devant le jury.

### Anti-rebond (vision et anomalies)
On n'alerte que si l'événement est vu plusieurs fois de suite, et on ne retombe qu'après un calme durable.
- **À quoi ça sert ?** Une seule image ratée ou une mesure bruitée ne doit pas faire hurler le buzzer.
- **Qui l'utilise ?** Tous les systèmes d'alarme et de supervision (on parle aussi d'hystérésis).
- **Quel problème ça résout ?** Les alertes qui clignotent, et la fatigue d'alerte des opérateurs.

### Isolation Forest (maintenance prédictive)
Des arbres coupent l'espace des mesures au hasard ; un point **anormal s'isole en peu de coupes**, un point normal en demande beaucoup. Le modèle apprend la « normalité » sans qu'on lui montre d'anomalie.
- **À quoi ça sert ?** Repérer un comportement inhabituel sans avoir d'exemples de pannes.
- **Qui l'utilise ?** Détection de fraude bancaire, maintenance industrielle (vibrations de turbines), cybersécurité.
- **Quel problème ça résout ?** Les seuils fixes (`if temp > 40`), interdits par le sujet : ils réagissent trop tard et ignorent les combinaisons (température qui monte doucement + gaz qui frémit).

### La fenêtre glissante et la pente
Pour chaque mesure, le script calcule sur les 30 dernières la **pente** de la température, de l'humidité et du gaz. Le modèle voit donc « valeur + tendance ».
- **À quoi ça sert ?** Voir qu'un phénomène démarre, avant qu'il soit grave.
- **Qui l'utilise ?** Les équipes de maintenance prédictive (EDF, Airbus) et les traders.
- **Quel problème ça résout ?** Une valeur normale qui monte anormalement vite reste invisible pour un modèle qui ne regarde que la valeur.

### Le score de risque expliqué
`0` = aussi normal que la mesure normale médiane, `80` = la frontière apprise par la forêt, au-delà = anomalie franche. La `cause` dit quelle grandeur s'écarte le plus de ce qui a été appris (« pente température en hausse, +4,7 écarts-types »).
- **À quoi ça sert ?** Le superviseur comprend en une seconde.
- **Qui l'utilise ?** Salles de contrôle, SOC, banques (obligation d'expliquer une décision automatique).
- **Quel problème ça résout ?** L'effet « boîte noire » : personne ne fait confiance à une IA qui ne dit pas pourquoi.

---

## Ton plan pour la semaine

1. **Lundi** : faire valider le contrat JSON aux devs ; lancer `vision.py` sur ta webcam.
2. **Mardi** : brancher les scripts sur le vrai Mosquitto (TLS) ; tester avec le simulateur ; brancher HA.
3. **Mercredi** : quand le boîtier publie, `python anomalies.py --reset` pour réapprendre **sur les vraies mesures** (15 à 30 min de fonctionnement normal) ; filmer YOLO pour la vidéo.
4. **Jeudi** : rédiger la doc IA du dossier à partir des sections ci-dessus.

Commite à chaque étape (`feat(ia): ...`) : la rigueur Git vaut 4 points au suivi individuel.
