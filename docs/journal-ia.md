# Journal de l'IA Sentinel-X : chaque étape, avec ses preuves

*Tenu par Louis (filière IA). Les captures sont dans `docs/captures/` (non versionnées : elles montrent des visages, voir la note RGPD en bas).*

## Lundi 5 octobre : les deux IA

- **IA vision** : YOLOv8n pré-entraîné (classe « personne »), image réduite à 640 px, anti-rebond, flux vidéo annoté, capture horodatée de chaque intrusion, alerte `POST /api/v1/alerts`.
- **IA anomalies** : Isolation Forest sur une fenêtre glissante de 30 mesures (valeurs + pentes), score de risque 0-100 expliqué, **aucun seuil statique**.
- Test avec le simulateur (`simulateur.py`) : 0 fausse alerte sur 2 000 mesures normales ; surchauffe lente détectée à **24,3 °C** (départ 23 °C), avant tout seuil critique.

## Mardi 6 octobre : intégration Docker

- Les deux IA deviennent des services du `docker-compose.yml` (image commune, utilisateur non-root, volume `ia_donnees`).
- Premier test sur la vraie caméra : détection correcte, mais **plusieurs centaines de millisecondes par image** sur le PC de la Région.

## Mercredi 7 octobre : contrôle d'accès et optimisation

**Contrôle d'accès** : bibliothèque de personnes autorisées (`enregistrer.py`), reconnaissance faciale YuNet + SFace (OpenCV).
Personne connue : cadre vert + nom. Inconnue : cadre rouge, alerte `personne_inconnue`, capture, message MQTT.

**Diagnostic de la « saturation »** : la caméra envoie 30 images/s, l'IA en traitait 2 ; les images s'empilaient et l'IA
analysait le passé avec des minutes de retard. Correctif : ne lire que la dernière image.

**Optimisation pour le Celeron N4120** (détail et tableau complet dans `docs/decision-serveur.md`) :

| Étape | Inférence YOLO | CPU de l'IA vision |
|---|---|---|
| Départ : PyTorch 640 px | 512 ms | 292 % |
| OpenVINO 320 px | 118 ms | 275 % |
| + conversion d'image à la demande | 107 ms | 214 % |
| **+ quantification INT8** | **59 ms** | **152 %** |

## Jeudi 8 octobre : pentest, puis détection en direct

**Pendant le pentest (matin)** : déconnexions Wi-Fi répétées (trame de déauthentification). Le broker MQTT a reçu de
nouveaux mots de passe ; l'IA s'est reconnectée avec son compte `ia` (vérifié par écoute du broker).

**Allègement d'urgence, puis retour au direct** : pour soulager le CPU, une version qui n'analysait qu'une image toutes
les 5 s avait été mise en place. Remplacée par la version optimisée (sauvegarde : `*.bak-avant-restauration-20261008-101417`).
L'équipe a ajouté une **validation des mesures** dans `anomalies.py` (valeurs impossibles rejetées) : conservée.

Mesure en direct (60 s, vraie caméra, 5 analyses/s) : **55 ms par image** (p90 62 ms, max 68 ms), CPU de l'IA 130 % sur 400 %.

**Enregistrement de Louis au poste de sécurité (12h27)** :

| Avant | Après |
|---|---|
| ![Louis inconnu, cadre rouge](captures/01-avant-enregistrement-inconnu.jpg) | ![Louis reconnu, cadre vert](captures/02-apres-enregistrement-connu.jpg) |
| Cadre rouge « INCONNU », alerte envoyée | Cadre vert « Louis Gardet », aucune alerte |

```bash
docker compose exec ia-vision python enregistrer.py --nom "Louis Gardet" --source http://127.0.0.1:8090/flux
```

5 prises de vue en 8 s, puis reconnaissance **2 secondes après** l'enregistrement, avec une similarité de **0,84**
(seuil 0,363), sans redémarrer l'IA. Journaux : `captures/03-journal-enregistrement.txt`, `captures/04-journal-ia-vision-et-ressources.txt`.

**Réglages optimisés par défaut (12h31)** : le `docker-compose.yml` du serveur a été remplacé pendant la journée et les
réglages du modèle avaient disparu ; un conteneur recréé serait reparti sur PyTorch 640 px (~500 ms). `vision.py` choisit
désormais tout seul le modèle INT8 320 px s'il est présent dans l'image. Contrôle après reconstruction : **55 ms**
(p90 57 ms), Louis toujours reconnu, bibliothèque conservée dans le volume `ia_donnees`.

## Note RGPD

Un visage est une donnée biométrique (article 9 du RGPD). Enregistrement volontaire uniquement ; on stocke une signature
de 128 nombres et une photo d'identification par personne, jamais de vidéo ; `personnes/` et `docs/captures/` sont exclus
de Git ; `enregistrer.py --supprimer "Nom"` efface tout pour une personne.
