# Décision : le PC de la Région est le PC Serveur Local, optimisé pour l'IA

*7 octobre 2026. À présenter au coach et à reprendre dans le dossier (schéma réseau, documentation IA).*

## Contexte

Option B retenue (un portable comme PC Serveur Local). Machine : **HP ProBook x360 11 G5 EE** prêté par la Région
(Intel Celeron N4120, 4 cœurs à 1,1 GHz, 4 Go de RAM, Ubuntu). Nous avons envisagé le Mac de Louis puis un HP Dragonfly,
et gardé le PC de la Région : pas de machine personnelle exposée au pentest, et une démo qui prouve que l'IA tient
sur du matériel modeste, comme un vrai boîtier de terrain.

## Problème constaté

Première version : YOLOv8n en PyTorch à 640 px, flux vidéo réencodé en H.264 par go2rtc puis décodé par l'IA.
Sur ce Celeron, l'analyse dépassait largement les 100 ms par image et prenait **des minutes de retard** sur le direct :
la caméra envoie 30 images par seconde, l'IA en traitait quelques-unes, et les autres s'empilaient.

## Optimisations

| # | Changement | Effet attendu |
|---|---|---|
| 1 | L'IA lit **la dernière image seulement** (fil de lecture séparé) | plus aucun retard sur le direct |
| 2 | L'IA ouvre la webcam en **MJPEG 640×480** ; go2rtc relaie le flux annoté au lieu de réencoder en H.264 | supprime un encodage et un décodage vidéo complets |
| 3 | **YOLOv8n exporté en OpenVINO** (moteur d'Intel) | gain typique de 2 à 3 sur processeur Intel |
| 4 | **Analyse en 320 px** (416 en réserve) | 4 fois moins de pixels qu'à 640 px |
| 5 | **5 analyses par seconde au maximum** | assez pour repérer une personne, laisse du CPU à Mosquitto et Home Assistant |
| 6 | Visage cherché **dans le haut du corps**, uniquement pour les personnes pas encore identifiées | reconnaissance faciale presque gratuite une fois la personne identifiée |

## Mesures sur le PC de la Région (7 octobre, webcam USB du kit, 2 personnes dans le champ)

| Version | Inférence YOLO (médiane / p90) | CPU de l'IA vision | Charge système (1 min) |
|---|---|---|---|
| Avant : PyTorch 640 px, flux H.264 via go2rtc | **512 ms** / 527 ms | 292 % (sur 400 %) | 4,6 |
| OpenVINO 416 px, MJPEG direct | 161 ms / 168 ms | 350 % | 4,0 |
| **OpenVINO 320 px, MJPEG direct (retenu)** | **77 ms / 80 ms** | **175 %** | **1,2** |

Gain : **6,6 fois plus rapide**, sous l'exigence des 100 ms, et la moitié du processeur libérée pour le reste de la stack.
go2rtc passe de 38 % à 0 % de CPU (il relaie sans réencoder). RAM utilisée : environ 1,5 Go sur 3,4 Go.

Contrepartie mesurée : à 320 px, une personne très petite ou à moitié cachée au fond de la salle peut être manquée.
Pour un poste de contrôle d'accès (personne proche de la caméra), c'est sans conséquence.
Le temps d'inférence s'affiche en direct sur l'image annotée et dans Home Assistant (`sensor.sentinel_ia_inference_ms`).

## Risques et parades

| Risque | Parade |
|---|---|
| 4 Go de RAM pour Home Assistant + IA + broker | surveiller avec `docker stats` ; WireGuard désactivable s'il ne sert pas |
| La vidéo du dashboard dépend de l'IA vision (elle seule ouvre la webcam) | `restart: unless-stopped` sur `ia-vision` |
| Mots de passe MQTT faibles | les changer avant jeudi dans le `.env` (jamais commité) |
| Pas encore de TLS sur MQTT | listener 8883 + CA locale : à faire pour l'exigence cyber (preuve Wireshark) |
| Panne du PC | plan B prêt : la stack tourne aussi sur le Mac (`docker-compose.mac.yml`) |
