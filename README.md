# Sentinel

Stack Docker Compose pour un broker MQTT dedie, Home Assistant, WireGuard et les flux video Ubuntu.

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
| IA vision | 8090/TCP | Flux webcam annoté par YOLOv8n (MJPEG) |
| IA anomalies | - | Isolation Forest sur les mesures MQTT (aucun port) |

## WireGuard

Le profil client est genere dans `wireguard/config/peer1/peer1.conf` apres le premier demarrage.
Ce dossier est ignore par Git car il contient des cles privees et ne doit pas etre publie.

Pour un acces depuis Internet, rediriger le port UDP `51820` de la box vers le PC Docker.

## Camera USB Ubuntu

Go2RTC capture directement la camera exposee par Linux (`/dev/video2`) et publie le flux
sous le nom `camera_usb`. Pour verifier le peripherique :

Avant le lancement, verifier que le peripherique est bien une camera :

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video2
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

Le flux relaye par go2rtc est ensuite :

```text
rtsp://go2rtc:8554/camera_usb
```

## IA (vision + anomalies)

Le dossier `ia/` contient les deux IA, chacune avec son propre client MQTT (compte `ia`) :
`vision.py` (YOLOv8n en OpenVINO + reconnaissance des personnes enregistrées, service `ia-vision`, qui ouvre la webcam) et `anomalies.py` (Isolation Forest sur les mesures MQTT, service `ia-anomalies`).
Elles publient sur le broker Mosquitto dedie (entites creees automatiquement dans Home Assistant) et envoient leurs alertes en `POST /api/v1/alerts`.
Mode d'emploi complet : [ia/README.md](ia/README.md).

Le broker MQTT est independant de Home Assistant : si Home Assistant est arrete, l'ESP8266 et les scripts IA peuvent continuer a communiquer. Ne pas publier directement les ports Home Assistant, MQTT ou go2rtc sur Internet. Utiliser WireGuard pour l'acces distant.