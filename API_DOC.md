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
  - **Arrêter un flux** :
    ```json
    {
      "type": "release_camera",
      "camera": 1
    }
    ```

### `wss://ha.arthonetwork.fr:44888/ws/app` (Connexion Application Mobile / Dashboard)
Canal bidirectionnel principal pour l'utilisateur.
- À la connexion, la Gateway renvoie immédiatement un message d'état initial pour chaque caméra :
  ```json
  {
    "type": "stream_status",
    "camera": 1,
    "active": true
  }
  ```
- **Routage** : Les messages reçus de l'App sont diffusés au `robot` et éventuellement au `node`.
- **Messages WebSocket gérés (App → Gateway → Robot) :**
  - **`request_camera`** : S'abonner et démarrer le flux WebRTC d'une caméra.
    ```json
    { "type": "request_camera", "camera": 1, "v_slam": false }
    ```
  - **`release_camera`** : Libérer l'abonnement à une caméra.
    ```json
    { "type": "release_camera", "camera": 1 }
    ```
  - **`stop_camera`** : Arrêter ou annuler une caméra ou une calibration en cours.
    ```json
    { "type": "stop_camera", "camera": 1 }
    ```
  - **`cmd_vel`** : Téléopération manuelle (vitesse linéaire et angulaire).
    ```json
    { "type": "cmd_vel", "linear": 0.2, "angular": -0.5 }
    ```
  - **`nav_goal`** : Envoyer une coordonnée cible de navigation autonome sur la carte.
    ```json
    { "type": "nav_goal", "x": 1.25, "y": -0.8 }
    ```
  - **`arduino_cmd`** : Contrôle bas niveau des servomoteurs de l'Arduino Mega.
    ```json
    { "type": "arduino_cmd", "cmd": "stand" }
    { "type": "arduino_cmd", "cmd": "sit" }
    { "type": "arduino_cmd", "cmd": "attach", "index": 4 }
    { "type": "arduino_cmd", "cmd": "detach", "index": 4 }
    { "type": "arduino_cmd", "cmd": "write", "index": 4, "angle": 95.0 }
    { "type": "arduino_cmd", "cmd": "reset_imu" }
    ```
  - **`manual_joint_control`** : Contrôle angulaire direct des 12 moteurs ROS.
    ```json
    { "type": "manual_joint_control", "angles": [90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0] }
    ```
  - **`chat`** : Envoi d'un message textuel ou vocal (qui sera traité par l'IA et MyGES).
    ```json
    { "type": "chat", "text": "Bonjour Bastet" }
    ```
  - **`run_mono_calib`** : Lancer la tâche de calibration mono.
    ```json
    { "type": "run_mono_calib", "camera": 1, "chessboard_cols": 9, "chessboard_rows": 6, "square_size_mm": 25, "timeout_seconds": 300 }
    ```
  - **`run_stereo_calib`** : Lancer la calibration stéréo.
    ```json
    { "type": "run_stereo_calib", "chessboard_cols": 9, "chessboard_rows": 6, "square_size_mm": 25, "timeout_seconds": 300 }
    ```

- **Messages WebSocket de notification (Robot → Gateway → App) :**
  - **`stream_status`** : Notification d'activation/désactivation de flux.
    ```json
    { "type": "stream_status", "camera": 1, "active": true }
    ```
  - **`mono_calib_frame` / `stereo_calib_frame`** : Retour vidéo temps réel en Base64 avec détection de damier.
    ```json
    { "type": "mono_calib_frame", "camera": 1, "image": "/9j/4AAQSkZ..." }
    ```
  - **`mono_calib_progress` / `stereo_calib_progress`** : Progression textuelle et pourcentage.
    ```json
    { "type": "mono_calib_progress", "camera": 1, "message": "Recherche du damier...", "progress": 45 }
    ```
  - **`mono_calib_result` / `stereo_calib_result`** : Fin de tâche de calibration.
    ```json
    { "type": "mono_calib_result", "camera": 1, "success": true, "message": "OK", "fx": 521.3, "reprojection_error": 0.18 }
    ```

---

## 2. Authentification & Comptes (REST)

Endpoints pour la gestion des profils et sessions.

- **POST `/auth/register`** (ou `/accounts`) : Créer ou mettre à jour un compte.
- **POST `/auth/login`** : Connexion et récupération des infos.
- **GET `/accounts`** : Lister les comptes.
- **DELETE `/accounts/{full_name}`** : Supprimer un compte (MyGES et visages inclus).
- **POST `/preferences`** : Enregistrer les préférences de style (Ex: `{"dark_mode": true}`).

---

## 3. Identifiants Intranet / MyGES (REST)

- **POST `/myges`** : Enregistrer les identifiants MyGES (username/password) d'un utilisateur.
- **GET `/myges`** : Récupérer les identifiants (interrogé par le robot).
- **POST `/myges/test`** : Tester la validité des identifiants et retourner un aperçu du planning.

---

## 4. Base de Visages (REST)

- **POST `/faces/upload`** : Upload d'image de visage (limité à 8 photos par personne).
- **GET `/faces`** : Lister tous les visages enregistrés.
- **GET `/faces/{face_id}/image`** : Télécharger l'image.
- **DELETE `/faces/{face_id}`** : Supprimer un visage.

---

## 5. Flux Vidéos (RTSP / WebRTC)

- **RTSP (Publication/Lecture)** : `rtsp://GATEWAY_IP:48554/robot/cam1` (Basse latence, pour le Node/IA)
- **HLS** : `https://ha.arthonetwork.fr:48888/robot/cam1/` (Streaming web via lecteur intégré Caddy)
- **WebRTC (WHEP)** : `https://ha.arthonetwork.fr:48889/robot/cam1/whep` (Ultra-basse latence pour navigateurs et App Mobile)

### 5.1 API REST des Flux Caméras (On-Demand)

Tous ces endpoints nécessitent `X-API-Token` dans le header.

- **GET `/api/cameras`** : Retourne le manifest des caméras détectées par le robot.
- **GET `/api/streams`** : Retourne l'état de tous les flux caméras (viewers, timers, keep_alive).
- **GET `/api/streams/{cam}`** : Retourne l'état d'un flux spécifique.
- **POST `/api/streams/{cam}/join`** : Rejoindre un flux (démarre la caméra sur le robot au premier viewer).
- **DELETE `/api/streams/{cam}/leave`** : Quitter un flux (lance un minuteur d'arrêt de 60s si dernier viewer).
- **POST `/api/streams/{cam}/stop`** : Arrêt forcé du flux (si aucun autre viewer connecté).

### 5.2 Configuration Qualité Stream (REST)

- **GET `/core/stream/config`** : Récupérer la configuration de résolution, framerate et bitrate.
- **POST `/core/stream/config`** : Modifier la configuration de qualité.
  ```json
  {
    "resolution": "640x480",
    "framerate": 10.0,
    "bitrate": "2M"
  }
  ```

### 5.3 [NOUVEAU - REST] Commandes et Lancement Calibration

Endpoints permettant de lancer, stopper ou superviser la calibration en mode REST :
- **POST `/api/calibration/camera/run/mono`** : Démarre une tâche de calibration monoculaire.
  ```json
  {
    "camera": 1,
    "chessboard_cols": 9,
    "chessboard_rows": 6,
    "square_size_mm": 25
  }
  ```
- **POST `/api/calibration/camera/run/stereo`** : Démarre une tâche de calibration stéréo.
  ```json
  {
    "chessboard_cols": 9,
    "chessboard_rows": 6,
    "square_size_mm": 25
  }
  ```
- **POST `/api/calibration/camera/abort`** : Arrête immédiatement la calibration en cours sur le robot.

---

## 6. État du Robot, Calibration & Téléopération (REST)

### 6.1 Diagnostic & État Général
- **POST `/core/state`** : Mise à jour de l'état général (publié périodiquement par le robot).
- **GET `/core/state`** : Récupération de l'état général du robot.
- **GET `/core/diagnostics`** : Récupère les diagnostics ROS complets envoyés par le robot en direct.

### 6.2 Calibration Servomoteurs & Fichiers Intrinsèques
- **GET `/core/calibration`** : Récupérer la liste des 12 offsets des servomoteurs de Bastet.
- **POST `/core/calibration`** : Enregistrer la liste des 12 offsets.
- **GET `/core/camera/calibration/{cam_id}`** : Récupérer le fichier de calibration intrinsèque YAML (ou JSON) de la caméra.
- **POST `/core/camera/calibration/{cam_id}`** : Enregistrer la calibration intrinsèque de la caméra.
- **GET `/core/camera/calibration/stereo`** : Récupérer la calibration extrinsèque stéréo.
- **POST `/core/camera/calibration/stereo`** : Enregistrer la calibration extrinsèque stéréo.
- **POST `/core/camera/calibration/reset`** : Effacer toutes les calibrations de caméra pour repartir à zéro.

### 6.3 [NOUVEAU - REST] Téléopération, Chat & Contrôle Moteur

Endpoints permettant de piloter le robot et de chater sans connexion WebSocket permanente :
- **POST `/api/robot/navigation/goal`** : Envoyer une destination autonome.
  ```json
  {
    "x": 1.5,
    "y": -0.5
  }
  ```
- **POST `/api/robot/motion/velocity`** : Envoyer une commande de vitesse (téléopération Joystick).
  ```json
  {
    "linear": 0.2,
    "angular": -0.1
  }
  ```
- **POST `/api/robot/motion/joints`** : Envoyer un tableau d'angles pour les 12 servos ROS.
  ```json
  {
    "angles": [90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0]
  }
  ```
- **POST `/api/robot/arduino/command`** : Envoyer une commande directe à l'Arduino.
  ```json
  {
    "cmd": "stand", // stand, sit, attach, detach, write, reset_imu
    "index": 3,    // Optionnel, pour attach/detach/write
    "angle": 95.0  // Optionnel, pour write
  }
  ```
- **POST `/api/robot/chat`** : Envoyer un message textuel au système de chat IA.
  ```json
  {
    "text": "Fais un pas en avant"
  }
  ```

---

## 7. Mises à Jour & Télémétrie (REST)

Endpoints permettant de contrôler et surveiller les mises à jour et rollbacks de versions logicielles.

- **POST `/system/update/gateway`** : Lancer la mise à jour de la Gateway.
- **GET `/system/update/gateway/progress`** : Récupérer l'état et progression de mise à jour Gateway (`?force=true` optionnel).
- **POST `/system/update/gateway/progress`** : Mettre à jour la progression Gateway.
- **POST `/system/update/gateway/rollback`** : Forcer le rollback de la Gateway vers une version spécifique.
  ```json
  {
    "version": "v0.3.7"
  }
  ```
- **POST `/system/update/robot`** : Déclencher la mise à jour du robot.
- **GET `/system/update/robot/progress`** : Récupérer la progression du robot (`?force=true` optionnel).
- **POST `/system/update/robot/progress`** : Mettre à jour la progression du robot.
- **POST `/system/update/robot/rollback`** : Forcer le rollback du robot et de l'Arduino vers une version spécifique.
  ```json
  {
    "version": "v0.2.27"
  }
  ```
- **POST `/system/update/arduino`** : Lancer le flashage de l'Arduino Mega.
- **GET `/system/update/arduino/progress`** : Récupérer la progression de l'Arduino (`?force=true` optionnel).
- **POST `/system/update/arduino/progress`** : Mettre à jour la progression de l'Arduino.

---

## 8. Diagnostic Santé (REST)

- **GET `/health`** : Vérifier la santé de l'API.
  ```json
  {
    "status": "healthy"
  }
  ```
