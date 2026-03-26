# CORE-Gateway

Passerelle locale entre le **robot Bastet**, l'**application mobile** et l'application **CORE** (agent IA).

```
Robot  ──RTSP──►  [ RTSP Proxy / MediaMTX ]  ──RTSP/HLS/WebRTC──►  CORE / App Mobile
Robot  ──HTTP──►  [ Face Server / FastAPI  ]  ──HTTP REST──►         CORE / App Mobile
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
