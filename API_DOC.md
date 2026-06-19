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

---

## 2. Authentification & Comptes (REST)

L'API utilise maintenant des endpoints dédiés pour l'authentification, tout en conservant la gestion des profils.

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

Géré par MediaMTX (intégré à la Gateway).
- **RTSP (Publication/Lecture)** : `rtsp://GATEWAY_IP:48554/robot/cam1` (Basse latence, pour le Node/IA)
- **HLS** : `http://GATEWAY_IP:48888/robot/cam1` (Streaming web)
- **WebRTC** : `http://GATEWAY_IP:48889/robot/cam1` (Ultra-basse latence pour l'App Mobile)

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
  "robot_version": "v0.1.6",
  "sensors": {
    "system": {
      "cpu_temp": 45.2,
      "cpu_load_1m": 0.8,
      "ram_total_mb": 8192,
      "ram_used_mb": 2048,
      "ram_percent": 25.0
    },
    "spotbot_service": "active / inactive"
  }
}
```

---

## 7. Mises à Jour & Télémétrie (REST + WebSockets)

Permet de contrôler et surveiller les mises à jour de la Gateway et du Robot.

### **POST `/system/update/gateway`**
Lance instantanément la recherche et l'application de mise à jour sur la Gateway (redémarre le conteneur si une mise à jour est installée).

### **GET `/system/update/gateway/progress`**
Récupère le statut de progression de l'update de la Gateway.
```json
{
  "status": "idle / downloading / extracting / done",
  "percent": 100
}
```

### **POST `/system/update/robot`**
Envoie un signal de mise à jour instantanée au robot via WebSocket (`{"type": "trigger_update"}`).

### **GET `/system/update/robot/progress`**
Récupère le statut de progression de l'update du robot (reconstruit de manière réelle pendant le `colcon build`).
```json
{
  "status": "idle / downloading / extracting / compiling / done / failed",
  "percent": 45
}
```

### Télémétrie en temps réel (WebSockets App)
Lorsqu'un processus de mise à jour est en cours, la Gateway diffuse ces messages en direct sur le canal mobile `/ws/app` :
- `{"type": "gateway_update_progress", "status": "...", "percent": ...}`
- `{"type": "robot_update_progress", "status": "...", "percent": ...}`
