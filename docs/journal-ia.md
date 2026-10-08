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

**API des alertes en service (15h03)** : aucune API n'existait encore pour `POST /api/v1/alerts`. Ajout d'un service
`api` (Python standard + SQLite, non-root, interne au réseau Docker, jeton `API_JETON`). Testé : requête sans jeton
refusée (401), JSON invalide refusé (400), gravité inconnue refusée (422), tentative d'injection SQL stockée comme simple
texte (requêtes paramétrées). Sur le serveur, première vraie alerte reçue à 15h04 : « Personne non enregistrée détectée
(1 inconnu, 1 personne identifiée : Joris Martins) ». Sauvegarde : `~/sauvegarde-avant-api-20261008-1503/`.

**Noms des personnes de nouveau visibles (15h10)** : quand une personne est proche de la caméra, son cadre touche le
haut de l'image et l'étiquette (nom ou « INCONNU ») était dessinée hors de l'image. Elle est maintenant placée dans le cadre
dans ce cas. Capture `08-noms-visibles-connu-inconnu.jpg` : Louis en vert avec son nom, un inconnu en rouge, 49,5 ms.

**« Dernier inconnu » instantané dans Home Assistant (15h12)** : la photo était publiée sur MQTT dès l'alerte, mais
l'entité « caméra » n'est rafraîchie que toutes les 10 s par le tableau de bord. Elle est remplacée par une entité
« image » (`image.sentinel_ia_dernier_inconnu`), affichée dès qu'une nouvelle photo arrive.

**Vidéo annotée plus fluide (15h17)** : la webcam envoie en réalité 30 images/s (go2rtc inchangé), mais le flux annoté
n'affichait que les 5 images analysées par seconde. Entre deux analyses, l'IA affiche maintenant les images suivantes avec
les derniers cadres connus. Mesures sur le serveur : flux annoté **5 → 11 images/s**, inférence inchangée (**56 ms**,
p90 62 ms), CPU de l'IA vision 125 % → 160 % sur 400 %, go2rtc inchangé (20 %), charge 1,7 → 2,6 sur 4.
Réglage : `IPS_AFFICHAGE` (15 par défaut). Sauvegarde : `ia/vision.py.bak-avant-fluide-*`.

**Fausse identification corrigée : deux « Joris Martins » à l'écran (15h24)** : une personne non enregistrée, vue de
profil, ressemblait à Joris à 0,36-0,40, juste au-dessus du seuil de 0,363 recommandé par SFace. Trois garde-fous :
seuil relevé à **0,45** (réglable : `SEUIL_VISAGE`), **même nom sur 2 analyses d'affilée** avant de l'afficher, et
**un nom sur un seul cadre à la fois** (le plus ressemblant le garde). En contrepartie, une personne enregistrée vue de
profil peut rester « identification... » : se réenregistrer sous plusieurs angles **ajoute** des signatures (30 au plus)
au lieu de remplacer les anciennes. Après déploiement : Joris reconnu à 0,70.

**Cadres en direct sur le tableau de bord (15h36)** : la carte vidéo du tableau de bord « TDB-SENTINEL-X » lit le flux
brut de go2rtc (sans cadres). Le flux annoté de l'IA plafonnait à ~11 images/s car il était dessiné par la boucle
d'analyse. Il est maintenant produit par un fil séparé, au rythme du flux : **18 images/s** mesurées sur le serveur, avec
une inférence inchangée (56 ms, p90 63 ms) et un CPU de l'IA plus bas qu'avant (135 % au lieu de 160 %, charge 1,85 sur 4).
Home Assistant joint le flux annoté (`http://ia-vision:8090/flux`, réponse 200, MJPEG) : il suffit de l'ajouter comme
caméra « MJPEG IP Camera » et de l'afficher sur la carte vidéo.

**« Joris reconnu sans son visage » (15h43)** : ce n'était pas un biais sur la couleur du t-shirt (la reconnaissance ne
regarde que le visage, recadré et aligné). Joris avait été identifié à 15h36 visage visible (0,74), puis le **suivi** a gardé
son nom sur son cadre pendant qu'il se penchait (capture `09-torse-sans-visage-avant-correctif.jpg`). Correctif : le visage
d'une personne identifiée est **revérifié toutes les 2 s** ; s'il n'a pas été revu depuis 10 s, le nom passe en orange
« Joris Martins ? » ; si le visage revu ne correspond plus deux fois de suite (cadres échangés entre deux personnes), le nom
est retiré et l'identification recommence. Après déploiement : 17,8 images/s, CPU de l'IA 139 %.

## Note RGPD

Un visage est une donnée biométrique (article 9 du RGPD). Enregistrement volontaire uniquement ; on stocke une signature
de 128 nombres et une photo d'identification par personne, jamais de vidéo ; `personnes/` et `docs/captures/` sont exclus
de Git ; `enregistrer.py --supprimer "Nom"` efface tout pour une personne.
