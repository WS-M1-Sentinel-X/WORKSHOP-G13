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

**Enregistrement de Joris Martins (14h30)** : 5 prises en 9 s, reconnu **3 secondes après**, similarité **0,85**,
y compris tête tournée. Captures `05-joris-avant.jpg` / `07-joris-apres.jpg`, journal `06-journal-enregistrement-joris.txt`.
Bibliothèque : 2 personnes (Joris Martins, Louis Gardet).

**Réglages optimisés par défaut (12h31)** : le `docker-compose.yml` du serveur a été remplacé pendant la journée et les
réglages du modèle avaient disparu ; un conteneur recréé serait reparti sur PyTorch 640 px (~500 ms). `vision.py` choisit
désormais tout seul le modèle INT8 320 px s'il est présent dans l'image. Contrôle après reconstruction : **55 ms**
(p90 57 ms), Louis toujours reconnu, bibliothèque conservée dans le volume `ia_donnees`.

**Durcissement de l'équipe poussé sur GitHub (14h16)** : les 3 commits de Tassily (services et réseau durcis, ACL de
l'ESP restreintes, abonnement au buzzer) étaient bloqués sur le serveur, faute d'identifiants GitHub. Vérifiés sans
secret, puis poussés depuis le poste de Louis (`fed9028..0032adf`, auteur conservé : Tassily). Retour arrière possible :
branche `sauvegarde/avant-push-20261008-1216` et archive `~/sauvegarde-avant-push-20261008-1216.tgz` sur le serveur.

**Bouton d'enregistrement dans Home Assistant (14h37)** : champ « Nom à enregistrer », bouton « Enregistrer la personne
devant la caméra » et capteur « Enregistrement », créés automatiquement par MQTT Discovery. Testé avant déploiement :
bouton sans nom refusé, puis 5 prises, enregistrement et reconnaissance (similarité 0,96). ACL Mosquitto : le compte
`homeassistant` peut écrire uniquement sur les 2 topics d'enregistrement. Sauvegardes : branche
`sauvegarde/avant-bouton-20261008-1436` et dossier `~/sauvegarde-avant-bouton-20261008-1436/` (code + ACL) sur le serveur.

**Bouton utilisé en conditions réelles (14h40)** : Tasilimy Kaba enregistré depuis Home Assistant. Bibliothèque : 3 personnes.

**Double confirmation PIR + caméra rétablie (14h54)** : le firmware publie le mouvement à part (`station/station1/mouvement`,
« ON »/« OFF »). L'IA vision écoute désormais ce topic : un inconnu vu par la caméra pendant que le PIR détecte un
mouvement donne une alerte **critique** (« mouvement confirmé par le PIR ») au lieu de « haute ». Testé avant déploiement.

## Note RGPD

Un visage est une donnée biométrique (article 9 du RGPD). Enregistrement volontaire uniquement ; on stocke une signature
de 128 nombres et une photo d'identification par personne, jamais de vidéo ; `personnes/` et `docs/captures/` sont exclus
de Git ; `enregistrer.py --supprimer "Nom"` efface tout pour une personne.
