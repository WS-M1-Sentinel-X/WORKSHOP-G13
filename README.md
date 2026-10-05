# Sentinel

Stack Docker Compose pour Home Assistant, MQTT, WireGuard et les flux video.

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
| Mosquitto | 1883/TCP | Broker MQTT pour l'ESP8266 |
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
ffmpeg -f dshow -i "video=NOM_DE_LA_CAMERA" -pix_fmt yuv420p -c:v libx264 -preset ultrafast -tune zerolatency -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8556/usb
```

Le flux relaye par go2rtc est ensuite :

```text
rtsp://go2rtc:8554/usb
```

Ne pas publier directement les ports Home Assistant, MQTT ou go2rtc sur Internet. Utiliser WireGuard pour l'acces distant.