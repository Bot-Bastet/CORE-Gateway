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

Pour garantir une latence minimale (< 50ms), les flux principaux utilisent des WebSockets. Toutes les connexions nécessitent le paramètre `?token=votre_token`.

### `wss://ha.arthonetwork.fr:44888/ws/robot` (Connexion Robot)
Canal bidirectionnel exclusif pour le robot.
- **Routage** : Les messages reçus du robot sont diffusés à `node` et `app`.
- **Injection contextuelle MyGES** : Si le robot envoie un message de type `"chat"`, la Gateway charge automatiquement les identifiants MyGES enregistrés pour l'utilisateur, interroge l'API MyGES pour récupérer son emploi du temps des 7 prochains jours, et l'injecte dans le champ `"context"` du JSON avant de le diffuser aux autres clients.
- **Mise à jour de la télémétrie** : Si le robot envoie un message de type `"telemetry_diagnostics"`, la Gateway intercepte le message et met à jour ses variables globales internes (accessibles via `GET /core/diagnostics`).

### `wss://ha.arthonetwork.fr:44888/ws/node` (Connexion CORE-Node)
Canal bidirectionnel pour le serveur de traitement.
- **Routage** : Les messages reçus du Node sont diffusés à `robot` et `app`.
- **Commandes caméras** :
  - **Démarrer un flux** :
    ```json
    {
      "type": "request_camera",
      "camera": 1,
      "v_slam": false
    }
    ```
    Active la caméra demandée, ajoute la connexion à la liste des auditeurs actifs, annule tout minuteur d'arrêt en cours, et notifie le robot (`start_camera`) ainsi que l'application mobile (`stream_status`).
  - **Arrêter un flux** :
    ```json
    {
      "type": "release_camera",
      "camera": 1
    }
    ```
    Retire la connexion de la liste des auditeurs de la caméra. Si aucun auditeur n'est actif, un minuteur d'extinction de 10 secondes est lancé pour couper le flux de la caméra sur le robot.

### `wss://ha.arthonetwork.fr:44888/ws/app` (Connexion Application Mobile)
Canal bidirectionnel pour l'utilisateur (Application mobile / Dashboard).
- À la connexion, la Gateway renvoie immédiatement un message d'état initial pour chaque caméra :
  ```json
  {
    "type": "stream_status",
    "camera": 1,
    "active": true
  }
  ```
- **Routage** : Les commandes et messages reçus de l'App sont diffusés à `robot`.
- **Commandes caméras** :
  - Identique à l'interface `node`, supporte le message `request_camera` pour démarrer et s'abonner à un flux, et `release_camera` pour s'en désabonner.

---

## 2. Authentification & Comptes (REST)

L'API utilise des endpoints dédiés pour l'authentification et la gestion des profils.

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
Liste tous les comptes enregistrés.

### **DELETE `/accounts/{full_name}`**
Supprime un compte utilisateur par son nom complet. Supprime également les identifiants MyGES associés et les photos de visage de la base de données.

### **POST `/preferences`**
Met à jour les préférences de l'utilisateur.
```json
{
  "full_name": "Nom Utilisateur",
  "preferences": {
    "dark_mode": true
  }
}
```

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
- **WebRTC (WHEP)** : `https://ha.arthonetwork.fr:48889/robot/cam1/whep` (Ultra-basse latence pour navigateurs et App Mobile)

### 5.1 API REST des Flux Caméras (On-Demand)

Tous les endpoints ci-dessous nécessitent `X-API-Token` dans le header.

#### **GET `/api/cameras`**
Retourne le manifest des caméras actuellement détectées par le robot.
```json
{
  "cameras": {
    "1": { "connected": true, "device": "/dev/video0", "calibrated": true },
    "2": { "connected": false, "device": null, "calibrated": false }
  }
}
```

#### **GET `/api/streams`**
Retourne l'état de tous les flux caméras.
```json
{
  "streams": {
    "1": { "running": true, "viewers": 2, "rest_viewers": ["web-abc123"], "browser_viewers": 1, "idle_kill_ms": 58000, "v_slam": false, "keep_alive": false },
    "2": { "running": false, "viewers": 0, "rest_viewers": [], "browser_viewers": 0, "idle_kill_ms": 0, "v_slam": false, "keep_alive": false }
  }
}
```

#### **GET `/api/streams/{cam}`**
Retourne l'état d'un flux caméra spécifique (cam = 1 ou 2).

#### **POST `/api/streams/{cam}/join`**
S'enregistre comme viewer REST de la caméra. Si premier viewer, **déclenche `start_camera` sur le robot** (lancement ffmpeg → MediaMTX).
- **Body** : `{ "client_id": "web-abc123" }` (optionnel, auto-généré si absent)
- **Réponse** : `{ "status": "starting", "client_id": "web-abc123", "running": true, "viewers": 1, ... }`
- **Erreur 409** : Si la caméra n'est pas branchée physiquement

#### **DELETE `/api/streams/{cam}/leave`**
Retire un viewer REST. Si plus aucun viewer (WS + REST = 0), lance un **minuteur d'extinction de 60s**.
- **Body** : `{ "client_id": "web-abc123" }` (obligatoire)
- **Réponse** : `{ "status": "left", "cooldown_starts": true, ... }`

#### **POST `/api/streams/{cam}/stop`**
Arrêt forcé (anti-griefing : 409 si d'autres viewers regardent encore).

### 5.2 Messages WebSocket (Stream)

Envoyés via `wss://ha.arthonetwork.fr:44888/ws/app`.

- **`request_camera`** (App → Gateway → Robot) : Démarre le streaming d'une caméra.
  ```json
  { "type": "request_camera", "camera": 1, "v_slam": false }
  ```
- **`release_camera`** (App → Gateway) : Libère l'abonnement WebSocket à la caméra.
  ```json
  { "type": "release_camera", "camera": 1 }
  ```
- **`stream_status`** (Gateway → App) : Notifie l'app du changement d'état d'un flux.
  ```json
  { "type": "stream_status", "camera": 1, "active": true }
  ```
- **`stream_state_sync`** (Gateway → App) : Synchronisation périodique de l'état complet (viewers, idle timer).
  ```json
  { "type": "stream_state_sync", "camera": 1, "running": true, "viewers": 3 }
  ```

---

## 6. État du Robot (CORE State)

Permet de suivre en temps réel ce que voit et fait le robot et de gérer la calibration.

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

### **GET `/core/calibration`**
Récupère les offsets de calibration des 12 servos de Bastet sous forme de liste.
```json
{
  "offsets": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
}
```

### **POST `/core/calibration`**
Sauvegarde les offsets de calibration.
```json
{
  "offsets": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
}
```

### **GET `/core/diagnostics`**
Récupère les diagnostics de télémétrie temps-réel reçus du robot (`telemetry_diagnostics` via WebSocket).

---

## 7. Mises à Jour & Télémétrie (REST + WebSockets)

Permet de contrôler et surveiller les mises à jour de la Gateway, du Robot et de l'Arduino.

### **POST `/system/update/gateway`**
Lance la mise à jour sur la Gateway (redémarre le conteneur).

### **GET `/system/update/gateway/progress`**
Récupère le statut de progression de la Gateway.
- **Query Parameter (facultatif)** : `?force=true` (force l'interrogation de l'API GitHub).

### **POST `/system/update/gateway/progress`**
Met à jour la progression de la mise à jour de la Gateway.

### **POST `/system/update/robot`**
Déclenche la mise à jour du robot.

### **GET `/system/update/robot/progress`**
Récupère la progression de la mise à jour du robot.
- **Query Parameter (facultatif)** : `?force=true`.

### **POST `/system/update/robot/progress`**
Met à jour la progression de la mise à jour du robot.

### **POST `/system/update/arduino`**
Déclenche le flashage de l'Arduino Mega.

### **GET `/system/update/arduino/progress`**
Récupère la progression du flashage Arduino.
- **Query Parameter (facultatif)** : `?force=true`.

### **POST `/system/update/arduino/progress`**
Met à jour la progression du flash de l'Arduino.

---

## 8. Diagnostic Santé (REST)

### **GET `/health`**
Endpoint de vérification de l'état de l'API. Retourne un statut de santé.
```json
{
  "status": "healthy"
}
```
