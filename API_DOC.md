# Documentation API : CORE-Gateway

La passerelle (Gateway) sert de pont central entre le **Robot Bastet (CORE)**, le **CORE-Node (traitement lourd IA)**, et l'**Application Mobile / Dashboard**. Elle assure le routage instantané (WebSockets), la sécurité, la gestion des comptes, la persistance des calibrations et le cycle de mise à jour.

- **Application** : FastAPI (`face-server/main.py`), version `2.1.0`, servie par uvicorn.
- **Architecture réseau** : Caddy (`:44888`, TLS) → proxy → uvicorn (`localhost:44887`).
- **Conteneur** : `bastet-face-server`.

> ⚠️ Ce document décrit le **contrat réel** exposé par le code (`face-server/routes/*.py`). Les messages WebSocket non listés explicitement dans un handler sont **relayés tels quels** au robot (voir §1.4).

---

## 0. Accès & Sécurité

L'API est accessible en **HTTPS standard** (sans mTLS / certificat client) via le domaine `ha.arthonetwork.fr`.

- **Port** : `44888`
- **URL de base** : `https://ha.arthonetwork.fr:44888`
- **Auth REST** : header `X-API-Token: <token>` (sur la quasi-totalité des endpoints, via `Depends(verify_token)`).
- **Auth WebSocket** : paramètre de requête `?token=<token>`. Un token invalide provoque une fermeture immédiate avec le code `4003`.
- **CORS** : toutes origines autorisées (`allow_origins=["*"]`).
- **Cache** : les assets statiques (`/static/`, `.js`, `.css`, `.html`, `/`) sont servis avec `Cache-Control: no-cache` pour que les déploiements soient visibles sans rechargement forcé.

Endpoints **sans** token : `GET /health`, `GET /` (dashboard HTML), `GET /logo.webp`, `POST /auth/login`, `POST /auth/register`.

---

## 1. Hub WebSockets (Communication Temps-Réel)

Trois canaux, un par type de client. Tous exigent `?token=<token>`. Le routeur central (`ConnectionManager`) diffuse les messages entre canaux selon les règles ci-dessous.

| Canal | Rôle | Diffusion par défaut des messages entrants |
|---|---|---|
| `/ws/robot` | Robot Bastet (Pi) | → `node` **et** `app` |
| `/ws/node` | CORE-Node (PC/IA) | → `robot` **et** `app` |
| `/ws/app` | Dashboard / App mobile | → `robot` (fallthrough) |

### 1.1 `wss://…/ws/robot` (Connexion Robot)

Canal bidirectionnel exclusif pour le robot.

- **À la connexion**, la Gateway envoie au robot une séquence de synchronisation **sécurisée** :
  1. `{"type":"demo_mode","enabled":<bool>}`
  2. `{"type":"arduino_cmd","cmd":"stop"}` — 🔴 sécurité : détache **toujours** tous les servos à la connexion (jamais de `stand`/`sit` auto, moteurs peut-être non calibrés).
  3. `{"type":"cmd_vel","linear":0.0,"angular":0.0}`
  4. Un `{"type":"robot_posture","key":…,"value":…}` par paramètre de posture mémorisé (hauteur, inclinaisons).
  5. Pour chaque caméra déjà active : `{"type":"start_camera","camera":<id>,"v_slam":<bool>}`.
- **Injection contextuelle MyGES** : si le robot émet un message `{"type":"chat", …}`, la Gateway charge les identifiants MyGES enregistrés, récupère l'emploi du temps des 7 prochains jours et l'injecte dans un champ `"context"` avant de rediffuser.
- **Interception télémétrie** : un message `{"type":"telemetry_diagnostics", …}` met à jour l'état interne (`sensors` normalisés) exposé via `GET /core/diagnostics`, puis est rediffusé à `node` et `app`.

### 1.2 `wss://…/ws/node` (Connexion CORE-Node)

Canal bidirectionnel pour le serveur de traitement IA.

- Gère les mêmes commandes caméra que l'app : `request_camera`, `release_camera`, `stop_camera`, `toggle_keep_stream`, `join_stream`, `leave_stream`.
- **Gate V-SLAM** : une demande de flux avec `v_slam:true` sur une caméra non calibrée renvoie au Node `{"type":"vslam_blocked","camera":<id>,"reason":"Calibration requise avant V-SLAM."}`.
- `{"type":"camera_resolutions", …}` et `{"type":"vslam_blocked", …}` reçus du Node sont relayés vers `app`.
- Tout autre message est diffusé à `robot` **et** `app`.

### 1.3 `wss://…/ws/app` (Connexion Application Mobile / Dashboard)

Canal bidirectionnel principal pour l'utilisateur.

**À la connexion**, la Gateway envoie l'état initial complet. Pour **chaque** caméra (1 et 2) :

```json
{ "type": "stream_status", "camera": 1, "active": false }
{ "type": "keep_stream_status", "camera": 1, "keep": false }
{ "type": "stream_state_sync", "camera": 1, "running": false, "viewers": 0,
  "ws_viewers": 0, "rest_viewers": 0, "keep_alive": false, "v_slam": false, "idle_kill_ms": 0 }
```

Puis, une fois :

```json
{ "type": "ai_state_update", "ai_state": { "tts": "robot", "stt": "robot", "chat": "node", "yolo": "disabled", "face_rec": "robot" } }
{ "type": "robot_posture_sync", "robot_posture": { "height": 100, "roll": 0, "pitch": 0, "yaw": 0, "powered": false, "demo_mode": false } }
```

**Messages gérés explicitement (App → Gateway) :**

- **`request_camera`** — s'abonner et démarrer le flux WebRTC d'une caméra.
  ```json
  { "type": "request_camera", "camera": 1, "v_slam": false }
  ```
- **`release_camera`** — libérer l'abonnement.
  ```json
  { "type": "release_camera", "camera": 1 }
  ```
- **`stop_camera`** — arrêter/annuler une caméra ou une calibration en cours.
  ```json
  { "type": "stop_camera", "camera": 1 }
  ```
- **`toggle_keep_stream`** — forcer le maintien d'un flux (désactive l'arrêt auto pour inactivité).
  ```json
  { "type": "toggle_keep_stream", "camera": 1, "keep": true }
  ```
- **`join_stream`** / **`leave_stream`** — alias sémantiques de `request_camera` / `release_camera`.
- **`ai_control`** — router une fonction IA vers le `robot` ou le `node`. Si `node` est ciblé mais déconnecté, la fonction passe à `disabled` et un `ai_state_update` (état effectif) est rediffusé à tous les dashboards.
  ```json
  { "type": "ai_control", "feature": "chat", "target": "node" }
  ```
- **`cmd_vel`** — téléopération manuelle. Le dashboard envoie 3 axes ; le relais REST (§6.3) n'en envoie que 2. Diffusé au **robot uniquement**.
  ```json
  { "type": "cmd_vel", "linear": -0.2, "lateral": 0.0, "angular": 1.0 }
  ```
- **`manual_joint_control`** — contrôle angulaire direct des 12 servos ROS (0-180°, 90 = neutre).
  ```json
  { "type": "manual_joint_control", "angles": [90,90,90,90,90,90,90,90,90,90,90,90] }
  ```
- **`arduino_cmd`** — contrôle bas niveau de l'Arduino Mega. Pour `attach`/`write`, la Gateway force `manual:true` ; pour `write` sans checksum, elle calcule `chk = (index + angle) % 100`.
  ```json
  { "type": "arduino_cmd", "cmd": "stand" }
  { "type": "arduino_cmd", "cmd": "sit" }
  { "type": "arduino_cmd", "cmd": "attach", "index": 4 }
  { "type": "arduino_cmd", "cmd": "detach", "index": 4 }
  { "type": "arduino_cmd", "cmd": "write", "index": 4, "angle": 95.0 }
  { "type": "arduino_cmd", "cmd": "reset_imu" }
  ```
- **`robot_posture_update`** — modifier un paramètre de posture (hauteur, inclinaison…).
  ```json
  { "type": "robot_posture_update", "key": "height", "value": 80 }
  ```
- **`demo_mode`** — basculer le mode démonstration (mouvements simulés).
  ```json
  { "type": "demo_mode", "enabled": true }
  ```
- **`query_camera_resolutions`** — demander au robot les résolutions supportées d'une caméra.
  ```json
  { "type": "query_camera_resolutions", "camera": 1 }
  ```
- **`nav_goal`** / **`nav_path`** — navigation autonome.
  ```json
  { "type": "nav_goal", "x": 1.25, "y": -0.8 }
  ```

### 1.4 Messages relayés (fallthrough)

Tout message `type` **non listé ci-dessus** est diffusé tel quel au `robot`. Cela couvre notamment :
`chat`, `scan_wifi`, `connect_wifi`, `forget_wifi`, `save_camera_mapping`, `reset_calibration`, `motor_calibration`, `stream_quality_config`, `run_stereo_calib`, `run_mono_calib`, `feature_request`, `trigger_update`, `trigger_arduino_flash`.

### 1.5 Notifications (Robot/Node → Gateway → App)

- **`state`** — l'état complet du robot (poussé après chaque `POST /core/state`) : `{"type":"state","payload":{…}}`.
- **`stream_status`** — activation/désactivation d'un flux : `{"type":"stream_status","camera":1,"active":true}`.
- **`stream_state_sync`** — état détaillé (viewers, keep-alive, minuteur d'arrêt) — voir §1.3.
- **`mono_calib_frame` / `stereo_calib_frame`** — retour vidéo Base64 avec détection de damier :
  `{"type":"mono_calib_frame","camera":1,"image":"/9j/4AAQ…"}`.
- **`mono_calib_progress` / `stereo_calib_progress`** — `{"type":"mono_calib_progress","camera":1,"message":"Recherche du damier…","progress":45}`.
- **`mono_calib_result` / `stereo_calib_result`** — `{"type":"mono_calib_result","camera":1,"success":true,"message":"OK","fx":521.3,"reprojection_error":0.18}`.
- **`vslam_blocked`** — V-SLAM refusé faute de calibration (voir §1.2).
- **`robot_update_progress`** — progression d'une mise à jour/rollback du robot.

---

## 2. Authentification & Comptes (REST)

- **POST `/auth/register`** (alias de `/accounts`, **sans token**) : créer un compte.
- **POST `/accounts`** : créer ou mettre à jour un compte. Corps = `AccountInfo` (`email`, `pseudo`, `last_name`, `first_name`, `phone`, `password?`, `is_admin`, `preferences`). La clé est `"{first_name} {last_name}"` ; le mot de passe est haché (`password_hash`) et jamais stocké en clair. → `{"status":"saved","user":"<full_name>"}`.
- **POST `/auth/login`** (**sans token**) : corps `{"email","password"}`. → `{"status":"success","user":{…sans password_hash…},"api_token":"<token>"}` ou `401`/`404`.
- **GET `/accounts`** : lister les comptes (dict indexé par nom complet).
- **DELETE `/accounts/{full_name}`** : supprimer un compte.
- **POST `/preferences`** : fusionner des préférences. Corps `{"full_name","preferences":{…}}`. → préférences mises à jour.

---

## 3. Identifiants Intranet / MyGES (REST)

- **POST `/myges?name=<user>`** : enregistrer les identifiants MyGES. Corps `{"username","password"}`.
- **GET `/myges`** : récupérer tous les identifiants stockés (interrogé par le robot). `404` si vide.
- **POST `/myges/test`** : tester des identifiants. Corps `{"username","password"}`. → `{"status":"success","message":"…","agenda_preview":"…"}` ou `{"status":"error","message":"…"}`.

---

## 4. Base de Visages (REST) — préfixe `/faces`

- **POST `/faces/upload?name=<personne>`** : upload multipart (`file`). Formats : `.jpg .jpeg .png .webp .bmp`. Limite **8 photos/personne**. Déduplication par hash MD5. → `{"status":"ok","face":{id,name,filename,original_name,hash,size_bytes,uploaded_at}}` (ou `already_exists`).
- **GET `/faces?name=<filtre?>`** : lister. → `{"count":N,"faces":[…]}`.
- **GET `/faces/{face_id}/image`** : télécharger l'image (FileResponse).
- **DELETE `/faces/{face_id}`** : supprimer visage + fichier.

---

## 5. Flux Vidéos (RTSP / HLS / WebRTC)

- **RTSP** : `rtsp://GATEWAY_IP:48554/robot/cam{1,2}` (basse latence, pour le Node/IA).
- **HLS** : `https://ha.arthonetwork.fr:48888/robot/cam{1,2}/index.m3u8` (lecteur web via MediaMTX).
- **WebRTC (WHEP)** : `https://ha.arthonetwork.fr:48889/robot/cam{1,2}/whep` (ultra-basse latence, navigateurs & app mobile).

### 5.1 API REST des Flux Caméras (On-Demand) — préfixe `/api`

Tous ces endpoints exigent `X-API-Token`.

- **GET `/api/cameras`** : manifest des caméras détectées par le robot.
- **GET `/api/streams`** : état de tous les flux (viewers, timers, keep-alive).
- **GET `/api/streams/{cam}`** : état d'un flux (`cam` ∈ {1,2}).
- **POST `/api/streams/{cam}/join`** : rejoindre un flux (démarre la caméra robot au 1er viewer). Corps `{"client_id":"<uuid>"}`. **Heartbeat** : re-POST toutes les ≤ 75 s tant qu'on regarde, sinon le viewer est purgé. `409` si la caméra n'est pas branchée. → `{"status":"starting|joined|already", "client_id", …état…}`.
- **DELETE `/api/streams/{cam}/leave`** : quitter (planifie l'arrêt à 60 s si dernier viewer). Corps `{"client_id":"<uuid>"}` **requis** (`400` sinon).
- **POST `/api/streams/{cam}/stop`** : arrêt forcé (`409` si un autre viewer regarde encore).
- **GET `/api/debug/state`** : dump de l'état interne des flux (débogage).

### 5.2 Configuration Qualité Stream (REST)

- **GET `/core/stream/config`** : récupérer la config **par caméra**.
- **POST `/core/stream/config`** : sauvegarder et rediffuser au robot (`stream_quality_config`). Structure réelle :
  ```json
  {
    "cam1": { "stream_res": "1280x720", "stream_fps": 20, "vslam_res": "640x480", "codec": "auto" },
    "cam2": { "stream_res": "1280x720", "stream_fps": 20, "vslam_res": "640x480", "codec": "auto" }
  }
  ```

### 5.3 Commandes & Lancement Calibration Caméra (REST) — préfixe `/api`

- **POST `/api/calibration/camera/run/mono`** : corps `{"camera":1,"chessboard_cols":9,"chessboard_rows":6,"square_size_mm":25}`.
- **POST `/api/calibration/camera/run/stereo`** : corps `{"chessboard_cols":9,"chessboard_rows":6,"square_size_mm":25}`.
- **POST `/api/calibration/camera/abort`** : arrêter immédiatement la calibration en cours.

---

## 6. État du Robot, Calibration & Téléopération (REST)

### 6.1 Diagnostic & État Général
- **POST `/core/state`** : mise à jour de l'état (publié périodiquement par le robot). Corps = `CoreState` (`seen_person?`, `seen_objects`, `last_chat`, `robot_status`, `robot_version`, `arduino_version`, `sensors`, `ai_state`). Rediffuse `{"type":"state","payload":…}` à l'app.
- **GET `/core/state`** : état courant. Ajoute `active_streams` et force `robot_status:"offline"` si aucune mise à jour depuis > 25 s.
- **GET `/core/diagnostics`** : diagnostics ROS complets envoyés en direct par le robot.
- **GET `/gateway/telemetry`** : télémétrie **de la Gateway** (Pi hôte) — `{cpu_percent, ram_percent, disk_percent, temp_c, uptime_s}`.

### 6.2 Calibration Servomoteurs & Fichiers Intrinsèques
- **GET `/core/calibration`** : offsets/limites/miroirs des 12 servos (défaut `{"offsets":[0]*12}`).
- **POST `/core/calibration`** : enregistrer la calibration servos (persistée dans `calibration.json`).
- **GET / POST `/core/camera/calibration/{cam_id}`** : calibration intrinsèque d'une caméra.
- **GET / POST `/core/camera/calibration/stereo`** : calibration extrinsèque stéréo.
- **POST `/core/camera/calibration/reset`** : réinitialiser **toutes** les calibrations caméra (mono 1, mono 2, stéréo) aux valeurs par défaut.

### 6.3 Téléopération, Chat & Contrôle Moteur (REST)

Permettent de piloter le robot sans WebSocket permanent. Chaque endpoint relaie un message au robot et renvoie `503` si le robot n'est pas connecté.

- **POST `/api/robot/navigation/goal`** : `{"x":1.5,"y":-0.5}`.
- **POST `/api/robot/motion/velocity`** : `{"linear":0.2,"angular":-0.1}` (relais → `{"type":"cmd_vel","linear","angular"}`).
- **POST `/api/robot/motion/joints`** : `{"angles":[90,…]}` (12 valeurs).
- **POST `/api/robot/arduino/command`** : `{"cmd":"stand","index":3,"angle":95.0}` (`index`/`angle` optionnels).
- **POST `/api/robot/chat`** : `{"text":"Fais un pas en avant"}`.

---

## 7. Mises à Jour & Rollback (REST)

- **POST `/system/update/gateway`** : lancer la MAJ de la Gateway.
- **GET `/system/update/gateway/progress`** (`?force=true` optionnel) / **POST** (mettre à jour la progression).
- **POST `/system/update/gateway/rollback`** : `{"version":"v0.3.7"}` — applique la release puis redémarre le process (SIGTERM).
- **POST `/system/update/robot`** : déclencher la MAJ du robot.
- **GET `/system/update/robot/progress`** (`?force=true`) / **POST**.
- **POST `/system/update/robot/rollback`** : `{"version":"v0.2.27"}` — diffuse `trigger_update` **et** `trigger_arduino_flash` au robot.
- **POST `/system/update/arduino`** : lancer le flashage de l'Arduino Mega.
- **GET `/system/update/arduino/progress`** (`?force=true`) / **POST**.

---

## 8. Diagnostic Santé (REST)

- **GET `/health`** (**sans token**) : `{"status":"healthy"}`.

---

## 9. Dashboard (HTML)

- **GET `/`** : page HTML du dashboard (référence `static/css/dashboard.css` et `static/js/dashboard.js`).
- **GET `/logo.webp`** : logo.
- **GET `/static/*`** : assets (CSS, JS, modèles 3D, Three.js).
