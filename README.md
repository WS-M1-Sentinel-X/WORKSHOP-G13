# Sentinel

Stack Docker Compose pour un broker MQTT dedie, Home Assistant, WireGuard et les flux video.

## Demarrage

```powershell
Copy-Item .env.example .env
docker compose up -d
docker compose ps
```

Interfaces locales :

- Home Assistant: http://localhost:8123
- go2rtc: http://localhost:1984

## Services

| Service | Port | Role |
| --- | --- | --- |
| Home Assistant | 8123/TCP | Supervision et tableau de bord |
| Mosquitto | 1883/TCP | Broker MQTT dedie et authentifie pour l'ESP8266, l'IA et Home Assistant |
| Mosquitto | 9001/TCP | MQTT over WebSocket (optionnel) |
| WireGuard | 51820/UDP | Acces VPN distant |
| go2rtc | 1984/TCP | API et relais video |
| go2rtc | 8554/TCP | Sortie RTSP |
| go2rtc | 8555/TCP+UDP | Sortie WebRTC |
| MediaMTX | 8556/TCP | Ingestion RTSP depuis Windows |

## WireGuard

Le profil client est genere dans `wireguard/config/peer1/peer1.conf` apres le premier demarrage.
Ce dossier est ignore par Git car il contient des cles privees et ne doit pas etre publie.

Pour un acces depuis Internet, rediriger le port UDP `51820` de la box vers le PC Docker.

## Camera USB Windows

La camera USB doit etre capturee par FFmpeg sur Windows, puis publiee vers MediaMTX :

```powershell
.\scripts\start-camera.ps1 -CameraName "USB Camera"
```

Le script utilise un profil adapte a une connexion plus fluide :

- resolution de sortie : `854x480` (480p)
- cadence : `20 FPS`
- debit video : `1200 kb/s`
- encodage H.264 avec faible latence

La meme configuration est disponible en Bash pour Windows Git Bash et Linux :

Sous Windows avec Git Bash :

```bash
./scripts/start-camera.sh
```

Le script utilise `USB Camera` par defaut. Pour choisir l'autre camera :

```bash
CAMERA_NAME="HD Camera" ./scripts/start-camera.sh
```

Sous Linux, `ffmpeg` utilise `/dev/video0` par defaut. Pour choisir un autre peripherique :

```bash
VIDEO_DEVICE=/dev/video2 ./scripts/start-camera.sh
```

Avant le lancement, verifier que le peripherique est bien une camera :

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video0
```

Si la camera ne supporte pas `1280x720` a `30 FPS`, utiliser un mode annonce par la
commande precedente, par exemple :

```bash
VIDEO_DEVICE=/dev/video0 INPUT_WIDTH=640 INPUT_HEIGHT=480 INPUT_FRAMERATE=15 \
./scripts/start-camera.sh
```

Pour une camera qui expose le format MJPEG :

```bash
VIDEO_DEVICE=/dev/video0 VIDEO_FORMAT=mjpeg ./scripts/start-camera.sh
```

L'erreur `Link has been severed` indique generalement que le peripherique USB a ete
deconnecte, que `/dev/video0` n'est pas le bon noeud video, ou que la camera n'est
pas accessible depuis une VM/WSL. Rebrancher la camera, puis relancer les commandes
`v4l2-ctl` avant de retester.

Si FFmpeg n'est pas installe sur Linux :

Ubuntu ou Debian :

```bash
sudo apt update
sudo apt install -y ffmpeg v4l-utils
```

Fedora :

```bash
sudo dnf install -y ffmpeg v4l-utils
```

Arch Linux :

```bash
sudo pacman -S --needed ffmpeg v4l-utils
```

Verifier ensuite l'installation :

```bash
ffmpeg -version
```

Les parametres peuvent etre ajustes sans modifier le script :

```bash
WIDTH=640 HEIGHT=360 FRAMERATE=15 BITRATE_KBPS=700 ./scripts/start-camera.sh
```

Pour tester l'autre camera detectee par Windows :

```powershell
.\scripts\start-camera.ps1 -CameraName "HD Camera"
```

Si PowerShell bloque l'execution du script pour cette session :

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Le flux relaye par go2rtc est ensuite :

```text
rtsp://go2rtc:8554/usb
```

## IA (vision + anomalies)

Le dossier `ia/` contient les deux IA, lancées en Python sur le PC hôte (hors Docker) :
`vision.py` (YOLOv8n sur le flux webcam relayé par go2rtc) et `anomalies.py` (Isolation Forest sur les mesures MQTT).
Elles publient sur le broker Mosquitto dedie (entites creees automatiquement dans Home Assistant) et envoient leurs alertes en `POST /api/v1/alerts`.
Mode d'emploi complet : [ia/README.md](ia/README.md).

Le broker MQTT est independant de Home Assistant : si Home Assistant est arrete, l'ESP8266 et les scripts IA peuvent continuer a communiquer. Ne pas publier directement les ports Home Assistant, MQTT ou go2rtc sur Internet. Utiliser WireGuard pour l'acces distant.