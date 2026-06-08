# CORE-Gateway

Passerelle locale entre le **robot Bastet**, l'**application mobile** et le **CORE-Node** (serveur de calcul).

```text
[ Robot ]  <─── WebSockets (Texte, Audio, Contrôle) ───>  [ Gateway Hub ]  <─── WebSockets ───>  [ CORE-Node ]
[ Robot ]  ─── RTSP (Vidéo) ───────────────────────────>  [ MediaMTX ]     ─── WebRTC/HLS ───>  [ App Mobile ]
[ App Mobile ] <── REST (Comptes, Intranet, Visages) ──>  [ Gateway API ]
```

## Services & Ports à ouvrir

Si le serveur tourne sur un réseau local protégé ou une machine distante, **les ports suivants doivent être ouverts** dans le pare-feu :

| Service | Port externe | Protocole | Description |
|---|---|---|---|
| **API Gateway** | `8001` | HTTPS (TCP) | API chiffrée (MyGES, Visages, État CORE) |
| **RTSP Proxy** | `8554` | RTSP (TCP/UDP) | Flux directs basse latence pour le robot et l'IA |
| **HLS Stream** | `8888` | HTTP (TCP) | Flux vidéo pour navigateurs |
| **WebRTC Stream** | `8889` | UDP/TCP | Flux vidéo ultra-basse latence (App Mobile / Web) |

---

## Démarrage rapide

```bash
# 1. Copier et configurer les variables
cp .env.example .env
# Éditer .env : mettre la VRAIE IP du robot en CAM1_SOURCE / CAM2_SOURCE
# CHANGER le API_TOKEN avec un secret fort !

# 2. Lancer les services (génère auto le certificat SSL)
docker compose up -d --build

# 3. Vérifier
docker compose ps
```

---

## API & Sécurité - Documentation
L'API tourne en **HTTPS (certificat auto-signé auto-généré)**.
Chaque requête doit inclure le Header : `X-API-Token: votre-token`.

👉 [Lisez la Documentation de l'API (API_DOC.md)](API_DOC.md) pour les détails sur les endpoints :
- Upload / Gestion de Visages
- Sauvegarde des identifiants MyGES
- Synchronisation de l'État du robot (Chat IA, vue, objets)

---

## Flux vidéo (RTSP Proxy)

| Format | URL | Lecteur recommandé |
|---|---|---|
| RTSP | `rtsp://IP_GATEWAY:8554/cam1` | VLC, ffplay, Agent CORE Python |
| HLS  | `http://IP_GATEWAY:8888/cam1` | Navigateurs, App React Native |
| WebRTC | `http://IP_GATEWAY:8889/cam1` | WebRTC Player |

Test rapide avec token ignoré (le proxy vidéo n'est pas authentifié par défaut) :
```bash
ffplay rtsp://localhost:8554/cam1
```

---

## 🌐 ROADMAP : CORE-Gateway (Le Hub Central)

La passerelle évolue pour devenir un véritable Hub temps-réel gérant la délégation des tâches (offloading) et la sécurité des utilisateurs.

### Étape 1 : Le Hub WebSockets (Instantanéité)
- [ ] **Canal Robot (`/ws/robot`)** : Recevoir le texte (STT local) ou l'audio brut, et lui renvoyer le flux TTS généré.
- [ ] **Canal Node (`/ws/node`)** : Connecter le serveur de calcul lourd pour lui envoyer les requêtes et recevoir les réponses textuelles/audio streamées.
- [ ] **Canal App (`/ws/app`)** : Permettre à l'app mobile de voir l'état du robot en direct et de le piloter (Télécommande).

### Étape 2 : L'App Mobile et la Sécurité
- [ ] **Comptes Utilisateurs** : Inscription/Connexion depuis l'app mobile (génération de JWT).
- [ ] **Identifiants Scolaires** : Stockage chiffré (AES) des identifiants MyGES. La Gateway doit les déchiffrer à la volée uniquement pour l'offloading.
- [ ] **Reconnaissance Faciale** : Upload de photos par l'utilisateur depuis l'app vers la base de la Gateway.
- [ ] **Administration** : API permettant de bannir un utilisateur ou de purger ses données sensibles (Intranet).

### Étape 3 : Le Routeur Intelligent (Le Cerveau)
- [ ] **Décisionnaire de flux** : Si le robot envoie du texte, l'envoyer au LLM du Node. S'il envoie de l'audio, l'envoyer au STT + LLM du Node.
- [ ] **Assemblage Contextuel** : Avant d'envoyer la requête au Node, la Gateway ajoute au "Super-Prompt" l'emploi du temps (via MyGES), l'historique récent, et ce que voit la caméra.

### Étape 4 : Contrôle et Vidéo
- [ ] **Télécommande** : API permettant de forcer des mouvements au robot ou de lui faire dire des phrases spécifiques.
- [ ] **Streaming Vidéo** : Intégration fluide des 2 caméras via WebRTC pour l'app mobile.
