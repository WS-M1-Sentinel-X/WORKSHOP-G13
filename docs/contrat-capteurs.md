# Contrat MQTT : mesures capteurs du boîtier Sentinel-X

Ce document fixe le format des messages que l'ESP8266 publie et que l'IA lit.
Tant que l'ESP ne publie pas ce message, l'IA anomalies n'a rien à apprendre ni à surveiller,
et la fusion PIR + caméra de l'IA vision ne fonctionne pas.

## Le message

- **Broker** : Mosquitto dédié de la stack (`192.168.0.100:1883`), utilisateur `esp8266` (mot de passe dans le `.env` du serveur, jamais dans le code commité)
- **Topic** : `station/station1/capteurs`
- **Cadence** : **une mesure toutes les 2 secondes, à intervalle fixe**
  (le DHT22 ne sait pas mesurer plus vite, et l'IA calcule des pentes « par mesure » : un rythme irrégulier fausserait les tendances)
- **QoS** : 0, **non retenu** (`retain: false`) : une vieille mesure ne doit pas être rejouée à l'IA au redémarrage

```json
{"id": "sentinel-x-01", "temperature": 23.4, "humidite": 45.1, "gaz": 182, "mouvement": 0}
```

| Champ | Type | Unité / plage | Capteur | Obligatoire | Utilisé par |
|---|---|---|---|---|---|
| `id` | texte | `sentinel-x-01` | - | oui | API, dashboard |
| `temperature` | nombre | °C, 1 décimale | DHT22 | **oui** | IA anomalies |
| `humidite` | nombre | %, 1 décimale | DHT22 | **oui** | IA anomalies |
| `gaz` | entier | **valeur brute de A0, 0 à 1023** | MQ-2 | **oui** | IA anomalies |
| `mouvement` | entier | `1` = mouvement, `0` = rien | PIR HC-SR501 | **oui** | IA vision (intrusion confirmée) |
| `ts` | texte | ISO 8601 UTC | - | non | API (sinon horodatage à la réception) |

Règles :

- **Noms de champs exacts, en minuscules, sans accent.** Un autre nom (`temp`, `humidity`) et l'IA ignore le message.
- **Jamais de `null`, `NaN` ni de chaîne vide** : si le DHT22 rate une lecture, on **n'envoie pas** le message plutôt que d'envoyer un trou.
- **`gaz` en valeur brute** (0-1023), pas en volts ni en ppm : l'Isolation Forest apprend la normalité de la salle, l'unité n'a pas d'importance tant qu'elle ne change pas.
- **`mouvement` reflète l'état du PIR au moment de la mesure** ; si possible, publier aussi un message immédiat quand il passe à 1 (sans attendre les 2 s).

## Les commandes (sens inverse)

Les commandes buzzer/LED passent par `station/station1/cmd/#` (ACL : écrites par Home Assistant, lues par l'ESP).
Leur format est défini par le firmware C++ de l'équipe.

## Côté ESP

Le firmware est écrit en **C++** (exigence du coach : MQTT codé à la main, pas via Home Assistant).
Exemple de publication avec PubSubClient + ArduinoJson :

```cpp
// toutes les 2 s, à intervalle fixe
float t = dht.readTemperature(), h = dht.readHumidity();
if (isnan(t) || isnan(h)) return;           // contrat : jamais de NaN, on saute la mesure
JsonDocument doc;
doc["id"] = "sentinel-x-01";
doc["temperature"] = roundf(t * 10) / 10;
doc["humidite"] = roundf(h * 10) / 10;
doc["gaz"] = analogRead(A0);                // MQ-2, valeur brute 0-1023
doc["mouvement"] = digitalRead(PIN_PIR);    // PIR : 1 ou 0
char tampon[160];
serializeJson(doc, tampon);
mqtt.publish("station/station1/capteurs", tampon, false);  // non retenu
```

## Côté IA

Après le premier branchement de l'ESP, faire réapprendre l'IA sur les vraies mesures (environ 20 min de salle « normale ») :

```bash
docker compose run --rm ia-anomalies python anomalies.py --reset --apprentissage 600
```
