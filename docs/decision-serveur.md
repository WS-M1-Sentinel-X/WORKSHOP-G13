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
| 2 | go2rtc capture la webcam (réglage de l'équipe, fixe) et l'IA lit son flux RTSP | une seule capture partagée entre l'IA et le dashboard |
| 3 | **YOLOv8n exporté en OpenVINO** (moteur d'Intel) et **quantifié en INT8** (calculs en entiers) | gain typique de 2 à 3, puis encore x 1,8 |
| 4 | **Analyse en 320 px** (416 en réserve) | 4 fois moins de pixels qu'à 640 px |
| 5 | **5 analyses par seconde au maximum** | assez pour repérer une personne, laisse du CPU à Mosquitto et Home Assistant |
| 6 | Visage cherché **dans le haut du corps**, uniquement pour les personnes pas encore identifiées | reconnaissance faciale presque gratuite une fois la personne identifiée |

## Mesures sur le PC de la Région (7 octobre, webcam USB du kit)

La capture de la webcam par go2rtc (`ffmpeg:/dev/video1#video=h264`) est un réglage de l'équipe, fixe :
l'IA lit son flux RTSP. Le décodage H.264 et l'encodage par go2rtc (~40 % d'un cœur) restent donc à payer.

| Version | Inférence YOLO (médiane / p90) | CPU de l'IA vision |
|---|---|---|
| Départ : PyTorch 640 px | **512 ms** / 527 ms | 292 % (sur 400 %) |
| OpenVINO 416 px (FP32) | 161 ms / 168 ms | 350 % |
| OpenVINO 320 px (FP32), flux go2rtc | 118 ms / 128 ms | 275 % |
| + conversion d'image uniquement pour les images analysées | 107 ms / 118 ms | 214 % |
| **+ modèle quantifié INT8 (retenu)** | **59 ms / 67 ms** (max 77 ms sur 60 s) | **152 %** |

Gain : **8,7 fois plus rapide**, sous l'exigence des 100 ms, et plus de 60 % du processeur libéré.
Le modèle INT8 trouve les mêmes personnes que le modèle FP32 sur nos images de test (confiances à ±0,02).
RAM utilisée : environ 1,45 Go sur 3,4 Go.

Contrepartie : à 320 px, une personne très petite ou à moitié cachée au fond de la salle peut être manquée.
Pour un poste de contrôle d'accès (personne proche de la caméra), c'est sans conséquence.
Le temps d'inférence s'affiche en direct sur l'image annotée et dans Home Assistant (`sensor.sentinel_ia_inference_ms`).

## Risques et parades

| Risque | Parade |
|---|---|
| 4 Go de RAM pour Home Assistant + IA + broker | surveiller avec `docker stats` ; WireGuard désactivable s'il ne sert pas |
| Mots de passe MQTT faibles | les changer avant jeudi dans le `.env` (jamais commité) |
| Pas encore de TLS sur MQTT | listener 8883 + CA locale : à faire pour l'exigence cyber (preuve Wireshark) |
| Panne du PC | plan B prêt : la stack tourne aussi sur le Mac (`docker-compose.mac.yml`) |
