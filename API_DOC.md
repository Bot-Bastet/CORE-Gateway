# Documentation API : CORE-Gateway

La passerelle (Gateway) sert de pont central entre le **Robot Bastet (CORE)**, le **CORE-Node (Traitement lourd)**, et l'**Application Mobile**. Elle assure le routage instantané (WebSockets), la sécurité (chiffrement) et la gestion des comptes.

---

## 1. Hub WebSockets (Communication Temps-Réel)

Pour garantir une latence minimale (< 50ms) et gérer le streaming (texte ou audio), les flux principaux utilisent des WebSockets.

### `ws://GATEWAY_IP:44888/ws/robot` (Connexion Robot)
Canal bidirectionnel exclusif pour le robot.
- **Envoi (Robot -> Gateway)** : 
  - Audio brut (si STT offloaded).
  - Texte (si STT calculé localement sur le robot).
  - État système (Batterie, position, visage détecté).
- **Réception (Gateway -> Robot)** :
  - Audio TTS (généré par le Node et streamé au robot).
  - Texte (réponse LLM).
  - Ordres de contrôle (ex: avance, tourne).

### `ws://GATEWAY_IP:44888/ws/node` (Connexion CORE-Node)
Canal bidirectionnel exclusif pour le serveur de calcul.
- **Réception (Gateway -> Node)** : Audio à transcrire, texte à processer dans le LLM, ou images pour YOLO. Inclut le contexte complet (identifiants intranet déchiffrés par la gateway).
- **Envoi (Node -> Gateway)** : Texte streamé (token par token) ou flux audio TTS généré.

### `ws://GATEWAY_IP:44888/ws/app` (Connexion Application Mobile)
Canal pour l'utilisateur.
- Reçoit en direct l'état du robot (ce qu'il fait, ce qu'il dit).
- Envoie des instructions de télécommande (joystick virtuel).

---

## 2. Comptes Utilisateurs (REST)

L'application mobile permet de créer des profils utilisateurs distincts. La Gateway gère la création et la récupération des profils.
L'API est protégée par le Token Global de la Gateway (`X-API-Token`).

- **POST `/accounts`** : Création ou mise à jour d'un compte.
  ```json
  {
    "email": "utilisateur@bastet.com",
    "pseudo": "Pseudo",
    "last_name": "Nom",
    "first_name": "Prénom",
    "phone": "0600000000",
    "is_admin": false
  }
  ```
- **GET `/accounts`** : Liste de tous les comptes enregistrés.

---

## 3. Identifiants Intranet / Écoles (REST - Sécurisé)

Le robot a besoin d'accéder aux données scolaires/persos (MyGES). Ces données sont stockées **chiffrées** (AES) sur la Gateway. Seule la Gateway peut les déchiffrer avant de les envoyer dans le contexte au Node.

- **POST `/users/me/intranet`**
  ```json
  { "service": "myges", "username": "id", "password": "mot_de_passe" }
  ```
  *(La Gateway chiffre "mot_de_passe" avant l'insertion en BDD)*
- **PUT `/users/me/intranet/{service}`** : Mettre à jour les identifiants.
- **DELETE `/users/me/intranet/{service}`** : Supprimer ses accès.

---

## 4. Base de Visages et Reconnaissance (REST)

L'App permet à l'utilisateur de s'enregistrer pour être reconnu par le robot.

- **POST `/users/me/faces`** : Upload de plusieurs photos (Multipart/FormData) par l'utilisateur via l'app.
- **GET `/faces/sync`** : Endpoint appelé par le robot au démarrage pour télécharger le dictionnaire des encodages ou des images brutes autorisées.

---

## 5. Flux Vidéos (RTSP / WebRTC)

La Gateway inclut un proxy vidéo (MediaMTX).
- **Caméra 1 & Caméra 2** : Le robot envoie ses flux en RTSP à la Gateway.
- **App Mobile** : Lit le flux en WebRTC (ultra-basse latence) via `http://GATEWAY_IP:48889/cam1` (ou cam2).
- **Node / Robot (Analyse)** : Consomme le flux en RTSP `rtsp://GATEWAY_IP:48554/cam1`.

---

## 6. Contrôle à Distance depuis l'App (REST)

En plus des WebSockets, une API REST permet d'envoyer des commandes spécifiques.

- **POST `/robot/command`**
  ```json
  {
    "action": "move",
    "direction": "forward",
    "speed": 1.0
  }
  ```
  *La Gateway traduit cette requête et l'injecte instantanément dans `/ws/robot`.*

---

## 7. Administration (Pour le Créateur / Admin)

L'utilisateur Admin (Teano) peut gérer la passerelle.

- **GET `/admin/users`** : Lister tous les utilisateurs.
- **DELETE `/admin/users/{user_id}`** : Supprimer un compte (entraîne la suppression en cascade de ses visages et identifiants).
- **DELETE `/admin/users/{user_id}/intranet`** : Purger les accès intranet d'un utilisateur par sécurité.
- **PUT `/admin/settings`** : Configurer la Gateway (ex: Délais de cooldown entre les interactions, activation/désactivation de modules).
