# Documentation API : CORE-Gateway

La passerelle (Gateway) sert de pont central entre le **Robot Bastet (CORE)**, le **CORE-Node (Traitement lourd)**, et l'**Application Mobile**. Elle assure le routage instantané (WebSockets), la sécurité et la gestion des comptes.

---

## 0. Accès & Sécurité

L'API est accessible en **HTTPS standard** (sans mTLS / certificat client) via le domaine `ha.arthonetwork.fr`.
Toutes les requêtes (REST et WebSockets) doivent inclure l'authentification :

- **Port** : `44888`
- **URL** : `https://ha.arthonetwork.fr:44888`
- **Auth** : HTTPS Simple + API Token (X-API-Token)
- **Header REST** : `X-API-Token: votre_token`
- **Paramètre WebSocket** : `?token=votre_token`

---

## 1. Hub WebSockets (Communication Temps-Réel)

Pour garantir une latence minimale (< 50ms), les flux principaux utilisent des WebSockets.

### `wss://ha.arthonetwork.fr:44888/ws/robot` (Connexion Robot)
Canal bidirectionnel exclusif pour le robot.

### `wss://ha.arthonetwork.fr:44888/ws/node` (Connexion CORE-Node)
Canal bidirectionnel exclusif pour le serveur de calcul.

### `wss://ha.arthonetwork.fr:44888/ws/app` (Connexion Application Mobile)
Canal pour l'utilisateur (État du robot, télécommande).

#### Messages WebSockets Importants
- **`telemetry_diagnostics`** : Envoyé périodiquement par le robot. Contient :
  - `joints` : Liste des 12 angles de servomoteurs (de 0 à 11).
  - `imu` : Données gyroscopiques (`roll`, `pitch`, `yaw`).
  - `topics` : Liste des topics ROS 2 actifs (`name`, `type`, `hz`).
  - `pose` & `path` : Coordonnées de localisation SLAM.
  - `ai_state` : État actif des modules IA.
- **`scan_wifi`** : Envoyé par l'App pour demander la recherche des réseaux à proximité.
- **`wifi_list`** : Renvoyé par le robot avec les réseaux triés par force de signal décroissante.
- **`camera_setup`** : Active ou désactive un flux caméra sur le robot (`camera`: 1 ou 2, `enable`: true/false).

---

## 2. Authentification & Comptes (REST)

L'API utilise des endpoints dédiés pour l'authentification, tout en conservant la gestion des profils.

### **POST `/auth/register`** (ou `/accounts`)
Crée ou met à jour un compte utilisateur.
```json
{
  "email": "utilisateur@bastet.com",
  "pseudo": "Pseudo",
  "first_name": "Prénom",
  "last_name": "Nom",
  "phone": "0600000000",
  "password": "votre_mot_de_passe",
  "is_admin": false,
  "preferences": {}
}
```

### **POST `/auth/login`**
Vérifie les identifiants et retourne les informations de l'utilisateur.
```json
{
  "email": "utilisateur@bastet.com",
  "password": "votre_mot_de_passe"
}
```

### **GET `/accounts`**
Liste tous les comptes enregistrés (nécessite d'être Admin).

---

## 3. Identifiants Intranet / MyGES (REST)

Le robot accède aux données MyGES via ces endpoints.

- **POST `/myges`** : Enregistre les identifiants MyGES (username/password) pour un utilisateur.
- **GET `/myges`** : Récupère les identifiants MyGES (interrogé par le robot).

---

## 4. Base de Visages (REST)

L'App permet à l'utilisateur de s'enregistrer pour être reconnu.

- **POST `/faces/upload`** : Upload de photos (Multipart). Limité à 8 photos par personne.
- **GET `/faces`** : Lister tous les visages enregistrés.
- **GET `/faces/{face_id}/image`** : Récupérer l'image correspondante.
- **DELETE `/faces/{face_id}`** : Supprimer un visage.

---

## 5. Flux Vidéos (RTSP / WebRTC)

Géré par MediaMTX (intégré à la Gateway). L'encodage vidéo H.264 utilise obligatoirement le profil standard `yuv420p` pour assurer une compatibilité totale avec les navigateurs web récents.
- **RTSP (Publication/Lecture)** : `rtsp://GATEWAY_IP:48554/robot/cam1` (Basse latence, pour le Node/IA)
- **HLS** : `https://ha.arthonetwork.fr:48888/robot/cam1/` (Streaming web via lecteur intégré Caddy)
- **WebRTC** : `https://ha.arthonetwork.fr:48889/robot/cam1` (Ultra-basse latence pour l'App Mobile)

---

## 6. État du Robot (CORE State)

Permet de suivre en temps réel ce que voit et fait le robot.

- **POST `/core/state`** : Mise à jour de l'état (par le robot).
- **GET `/core/state`** : Récupération de l'état (par l'app).

**Payload de l'état :**
```json
{
  "seen_person": "Nom reconnu ou null",
  "seen_objects": ["liste", "objets", "detectes"],
  "last_chat": [{"role": "user", "content": "..."}],
  "robot_status": "online / hibernating / offline",
  "active_streams": {
    "1": true,
    "2": false
  },
  "robot_version": "v0.2.5",
  "arduino_version": "v0.0.0",
  "sensors": {
    "cpu_percent": 45,
    "ram_percent": 25.0,
    "temp_c": 65.0,
    "spotbot_service_active": true,
    "spotbot_service": "active / inactive",
    "cam1_connected": true,
    "cam2_connected": false,
    "arduino_connected": true,
    "system": {
      "cpu_temp": 65.0,
      "cpu_load_1m": 1.8,
      "ram_total_mb": 8062,
      "ram_used_mb": 2048,
      "ram_percent": 25.0
    }
  },
  "ai_state": {
    "tts": "robot / node / disabled",
    "stt": "robot / node / disabled",
    "chat": "robot / node / disabled",
    "yolo": "enabled / disabled",
    "face_rec": "enabled / disabled"
  }
}
```

---

## 7. Mises à Jour & Télémétrie (REST + WebSockets)

Permet de contrôler et surveiller les mises à jour de la Gateway, du Robot et de l'Arduino. Un mécanisme de sécurité réinitialise le statut à `failed` en cas d'absence de progression pendant plus de 10 minutes (anti-blocage).

### **POST `/system/update/gateway`**
Lance la mise à jour sur la Gateway (redémarre le conteneur).

### **GET `/system/update/gateway/progress`**
Récupère le statut de progression de la Gateway.
- **Query Parameter (facultatif)** : `?force=true` (force le contournement du cache local et interroge l'API GitHub directement pour actualiser la version).
```json
{
  "status": "idle / downloading / extracting / done / failed",
  "percent": 100
}
```

### **POST `/system/update/gateway/progress`**
Permet de mettre à jour la progression de la mise à jour de la Gateway.

### **POST `/system/update/robot`**
Déclenche la mise à jour du robot via WebSocket.

### **GET `/system/update/robot/progress`**
Récupère la progression de la mise à jour du robot (compilation `colcon build`).
- **Query Parameter (facultatif)** : `?force=true` (force le contournement du cache local et interroge l'API GitHub directement).

### **POST `/system/update/robot/progress`**
Permet au robot de notifier son état et sa progression de mise à jour.

### **POST `/system/update/arduino`**
Déclenche le flashage du code sur l'Arduino Mega (uniquement si le robot est en ligne et l'Arduino connecté).

### **GET `/system/update/arduino/progress`**
Récupère le statut et la progression du flashage Arduino.
- **Query Parameter (facultatif)** : `?force=true` (force le contournement du cache local).

### **POST `/system/update/arduino/progress`**
Permet au robot de notifier l'avancement du flash de l'Arduino.
