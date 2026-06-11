# CORE-Gateway

Passerelle locale entre le **robot Bastet**, l'**application mobile** et le **CORE-Node** (serveur de calcul).

```text
[ Robot ]  <─── WebSockets (Texte, Audio, Contrôle) ───>  [ Gateway Hub ]  <─── WebSockets ───>  [ CORE-Node ]
[ Robot ]  ─── RTSP (Vidéo) ───────────────────────────>  [ MediaMTX ]     ─── WebRTC/HLS ───>  [ App Mobile ]
[ App Mobile ] <── REST (Comptes, Intranet, Visages) ──>  [ Gateway API ]
```

## Services & Ports à ouvrir

Pour un accès externe via IP publique, les ports suivants doivent être redirigés vers votre machine :

| Service | Port externe | Protocole | Description |
|---|---|---|---|
| **API Gateway** | `44888` | HTTP (TCP) | API (Auth, MyGES, Visages, État CORE) |
| **RTSP Proxy** | `48554` | RTSP (TCP/UDP) | Flux directs pour le robot et l'IA |
| **HLS Stream** | `48888` | HTTP (TCP) | Flux vidéo pour navigateurs |
| **WebRTC Stream** | `48889` | UDP/TCP | Flux vidéo ultra-basse latence (App Mobile) |

---

## Démarrage rapide

### Installation
```bash
git clone https://github.com/Bot-Bastet/CORE-Gateway.git
cd CORE-Gateway

# 1. Configurer les variables
cp .env.example .env
# Éditer .env : configurer les sources CAM et votre API_TOKEN
```

### Lancement (Docker Compose)
```bash
# Lancer les services (API en HTTP + Proxy Vidéo)
docker compose up -d --build

# Vérifier le fonctionnement
docker compose ps
```

---

## API & Sécurité
L'API est accessible en **HTTP** (port `44888`).
Chaque requête doit inclure le Header : `X-API-Token: votre-token`.

👉 [Consultez la Documentation complète de l'API (API_DOC.md)](API_DOC.md) pour les détails sur les endpoints d'authentification (`/auth/login`, `/auth/register`), MyGES et Visages.

---

## Flux vidéo (MediaMTX)

| Format | URL |
|---|---|
| RTSP | `rtsp://IP_GATEWAY:48554/cam1` |
| HLS  | `http://IP_GATEWAY:48888/cam1` |
| WebRTC | `http://IP_GATEWAY:48889/cam1` |

---

## 🌐 ROADMAP

- [x] **Hub WebSockets** : Routage temps-réel entre Robot, Node et App.
- [x] **Authentification** : Endpoints `/auth/login` et `/auth/register` (BCrypt).
- [x] **Accès Public** : Passage en HTTP pour éviter les erreurs SSL sur IP.
- [x] **Intégration MyGES** : Synchronisation du planning scolaire.
- [ ] **Interface d'Admin** : Dashboard web pour gérer les utilisateurs et les visages.
