# Documentation API : CORE-Gateway

La passerelle (Gateway) sert de pont central entre le **Robot Bastet (CORE)**, le **CORE-Node (Traitement lourd)**, et l'**Application Mobile**. Elle assure le routage instantané (WebSockets), la sécurité et la gestion des comptes.

---

## 0. Accès & Sécurité

L'API est accessible en **HTTPS** via le domaine `ha.arthonetwork.fr`.
Toutes les requêtes (REST et WebSockets) doivent inclure l'authentification :

- **Port** : `44888`
- **URL** : `https://ha.arthonetwork.fr:44888`
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

- **POST `/myges/login`** : Enregistre les identifiants MyGES pour l'utilisateur.
- **GET `/myges/planning`** : Récupère le planning (interroge l'API Kordis via la gateway).

---

## 4. Base de Visages (REST)

L'App permet à l'utilisateur de s'enregistrer pour être reconnu.

- **POST `/faces/upload`** : Upload de photos (Multipart).
- **GET `/faces/sync`** : Synchronisation des encodages pour le robot.
- **DELETE `/faces/{face_id}`** : Supprimer un visage.

---

## 5. Flux Vidéos (RTSP / WebRTC)

Géré par MediaMTX (intégré à la Gateway).
- **RTSP** : `rtsp://GATEWAY_IP:48554/cam1` (Basse latence, pour le Node/IA)
- **HLS** : `http://GATEWAY_IP:48888/cam1` (Streaming web)
- **WebRTC** : `http://GATEWAY_IP:48889/cam1` (Ultra-basse latence pour l'App Mobile)

---

## 6. État du Robot (CORE State)

- **POST `/core/state`** : Mise à jour de l'état (par le robot).
- **GET `/core/state`** : Récupération de l'état (par l'app).
