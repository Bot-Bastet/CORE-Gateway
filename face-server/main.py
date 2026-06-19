from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Security, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from contextlib import asynccontextmanager
from pydantic import BaseModel
import os
import uuid
import time
import json
import hashlib
import threading
import requests
from pathlib import Path
from typing import Optional
from myges_api import MyGesAPI
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# ─── Config ───────────────────────────────────────────────────────────────────
FACES_DIR = Path(os.getenv("FACES_DIR", "/data/faces"))
DATA_DIR = Path("/data") # general data dir for state/myges
META_FILE = FACES_DIR / "meta.json"
MYGES_FILE = DATA_DIR / "myges.json"
STATE_FILE = DATA_DIR / "core_state.json"
USERS_FILE = DATA_DIR / "users.json"

FACES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

API_TOKEN = os.getenv("API_TOKEN", "your-api-token-here")
api_key_header = APIKeyHeader(name="X-API-Token", auto_error=False)

# ─── Auto-Update au démarrage & Périodique ────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    def _run_update():
        try:
            from updater import check_and_apply_update
            updated = check_and_apply_update()
            if updated:
                import os, signal
                os.kill(os.getpid(), signal.SIGTERM)  # Demander un redémarrage via PM2/systemd
        except Exception as e:
            print(f"[AutoUpdater] Erreur : {e}")

    def _hourly_check():
        time.sleep(10)  # Attendre que l'app démarre
        _run_update()
        while True:
            time.sleep(3600)
            try:
                state = load_json(STATE_FILE, default={"robot_status": "offline"})
                status = state.get("robot_status", "offline")
                if status != "online":
                    print("[AutoUpdater] Robot inactif. Vérification de mise à jour Gateway...")
                    _run_update()
            except Exception as e:
                print(f"[AutoUpdater] Erreur : {e}")

    threading.Thread(target=_hourly_check, daemon=True).start()
    yield

app = FastAPI(
    title="Bastet Gateway API",
    description="API Gateway pour le robot Bastet (Faces, MyGES, Core State). Protégée par Token.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Auth ─────────────────────────────────────────────────────────────────────

def verify_token(api_key: str = Security(api_key_header)):
    if api_key != API_TOKEN:
        raise HTTPException(status_code=403, detail="Accès refusé. X-API-Token invalide ou manquant.")
    return api_key

def verify_token_optional(api_key: str = Security(api_key_header)):
    """Pour les routes où on gère l'auth différemment (ex. Dashboard)"""
    return api_key

# ─── Models ───────────────────────────────────────────────────────────────────

class MyGESCredentials(BaseModel):
    username: str
    password: str

class CoreState(BaseModel):
    seen_person: Optional[str] = None
    seen_objects: list[str] = []
    last_chat: list[dict] = []
    robot_status: str = "idle"
    robot_version: Optional[str] = "v0.0.0"
    sensors: dict = {}

class UpdateProgress(BaseModel):
    status: str
    percent: int

class AccountInfo(BaseModel):
    email: str
    pseudo: str
    last_name: str
    first_name: str
    phone: str
    password: Optional[str] = None
    password_hash: Optional[str] = None
    is_admin: bool = False
    preferences: dict = {}

class LoginRequest(BaseModel):
    email: str
    password: str

class PreferencesUpdate(BaseModel):
    full_name: str
    preferences: dict

# ─── Nettoyage & Helpers ──────────────────────────────────────────────────────

def load_json(path: Path, default=None):
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else []

def save_json(path: Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def find_entry(face_id: str) -> Optional[dict]:
    return next((e for e in load_json(META_FILE) if e["id"] == face_id), None)

def cleanup_duplicates():
    """Vérifie et supprime automatiquement les doublons d'image basés sur le hash MD5."""
    meta = load_json(META_FILE)
    if not meta: return
    
    seen_hashes = set()
    new_meta = []
    modified = False
    
    for entry in meta:
        path = FACES_DIR / entry["filename"]
        if not path.exists():
            modified = True
            continue
            
        with open(path, "rb") as f:
            file_hash = hashlib.md5(f.read()).hexdigest()
            
        unique_id = f"{entry['name']}_{file_hash}"
        
        if unique_id in seen_hashes:
            path.unlink() # Suppression automatique du fichier en double
            modified = True
        else:
            seen_hashes.add(unique_id)
            entry["hash"] = file_hash
            new_meta.append(entry)
            
    if modified:
        save_json(META_FILE, new_meta)
        print("🧹 Nettoyage des doublons terminé.")

@app.on_event("startup")
def startup_event():
    cleanup_duplicates()

# ─── WebSockets Hub (Routage Temps-Réel & Caméra On-Demand) ────────────────────

active_camera_listeners = {
    1: set(),
    2: set()
}

def cleanup_camera_listeners(websocket: WebSocket):
    import asyncio
    for cam_id in [1, 2]:
        if websocket in active_camera_listeners[cam_id]:
            active_camera_listeners[cam_id].remove(websocket)
            if len(active_camera_listeners[cam_id]) == 0:
                asyncio.create_task(manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot"))

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {
            "robot": [],
            "node": [],
            "app": []
        }

    async def connect(self, websocket: WebSocket, client_type: str):
        await websocket.accept()
        if client_type in self.active_connections:
            self.active_connections[client_type].append(websocket)

    def disconnect(self, websocket: WebSocket, client_type: str):
        if client_type in self.active_connections and websocket in self.active_connections[client_type]:
            self.active_connections[client_type].remove(websocket)
        cleanup_camera_listeners(websocket)

    async def broadcast(self, message: str, target_client_type: str):
        for connection in self.active_connections.get(target_client_type, []):
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

@app.websocket("/ws/robot")
async def websocket_robot(websocket: WebSocket, token: Optional[str] = Query(None)):
    if token != API_TOKEN:
        await websocket.accept()
        await websocket.close(code=4003)
        return

    await manager.connect(websocket, "robot")
    try:
        while True:
            data = await websocket.receive_text()
            
            # Injection contextuelle (Emploi du temps MyGES)
            try:
                msg_json = json.loads(data)
                if msg_json.get("type") == "chat":
                    comptes = load_json(MYGES_FILE, default={})
                    if comptes:
                        user_name = list(comptes.keys())[0]
                        creds = comptes[user_name]
                        api = MyGesAPI(creds["username"], creds["password"])
                        agenda_text = api.get_upcoming_agenda_text(days=7)
                        msg_json["context"] = f"[CONTEXTE CACHÉ - Agenda de {user_name} pour les 7 prochains jours] : \n{agenda_text}"
                        data = json.dumps(msg_json)
                        print(f"OK: Contexte injecte pour {user_name}.")
            except Exception as e:
                print(f"Erreur injection contexte : {e}")
                
            # Routage automatique du robot vers le noeud et l'app
            await manager.broadcast(data, "node")
            await manager.broadcast(data, "app")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "robot")

@app.websocket("/ws/node")
async def websocket_node(websocket: WebSocket, token: Optional[str] = Query(None)):
    if token != API_TOKEN:
        await websocket.accept()
        await websocket.close(code=4003)
        return

    await manager.connect(websocket, "node")
    try:
        while True:
            data = await websocket.receive_text()
            
            # Intercepter les requêtes de caméra du Node
            try:
                msg_json = json.loads(data)
                msg_type = msg_json.get("type")
                if msg_type == "request_camera":
                    cam_id = msg_json.get("camera", 1)
                    active_camera_listeners[cam_id].add(websocket)
                    if len(active_camera_listeners[cam_id]) == 1:
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id}), "robot")
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0:
                            await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
            except Exception:
                pass
                
            # Routage de la réponse du noeud (LLM streamé ou audio TTS) vers le robot et l'app
            await manager.broadcast(data, "robot")
            await manager.broadcast(data, "app")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "node")

@app.websocket("/ws/app")
async def websocket_app(websocket: WebSocket, token: Optional[str] = Query(None)):
    if token != API_TOKEN:
        await websocket.accept()
        await websocket.close(code=4003)
        return

    await manager.connect(websocket, "app")
    try:
        while True:
            data = await websocket.receive_text()
            
            # Intercepter les requêtes de caméra de l'App/Site
            try:
                msg_json = json.loads(data)
                msg_type = msg_json.get("type")
                if msg_type == "request_camera":
                    cam_id = msg_json.get("camera", 1)
                    active_camera_listeners[cam_id].add(websocket)
                    if len(active_camera_listeners[cam_id]) == 1:
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id}), "robot")
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0:
                            await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
            except Exception:
                pass
                
            # Routage des commandes de l'app mobile vers le robot
            await manager.broadcast(data, "robot")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "app")

# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.delete("/accounts/{full_name}", tags=["Accounts"], summary="Supprimer un compte", dependencies=[Depends(verify_token)])
def delete_account(full_name: str):
    users = load_json(USERS_FILE, default={})
    if full_name in users:
        del users[full_name]
        save_json(USERS_FILE, users)
        return {"status": "deleted", "user": full_name}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


@app.delete("/accounts/{full_name}", tags=["Accounts"], summary="Supprimer un compte", dependencies=[Depends(verify_token)])
def delete_account(full_name: str):
    users = load_json(USERS_FILE, default={})
    if full_name in users:
        # Supprimer des comptes
        del users[full_name]
        save_json(USERS_FILE, users)

        # Nettoyer les identifiants MyGES associés
        myges = load_json(MYGES_FILE, default={})
        if full_name in myges:
            del myges[full_name]
            save_json(MYGES_FILE, myges)

        # Nettoyer les photos de visage associées
        meta = load_json(META_FILE, default=[])
        new_meta = []
        for entry in meta:
            if entry.get("name", "").lower() == full_name.lower():
                path = FACES_DIR / entry["filename"]
                try:
                    if path.exists():
                        path.unlink()
                except Exception as e:
                    print(f"Error deleting face image {path}: {e}")
            else:
                new_meta.append(entry)
        save_json(META_FILE, new_meta)

        return {"status": "deleted", "user": full_name}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


@app.delete("/accounts/{full_name}", tags=["Accounts"], summary="Supprimer un compte", dependencies=[Depends(verify_token)])
def delete_account(full_name: str):
    users = load_json(USERS_FILE, default={})
    if full_name in users:
        # Supprimer des comptes
        del users[full_name]
        save_json(USERS_FILE, users)

        # Nettoyer les identifiants MyGES associés
        myges = load_json(MYGES_FILE, default={})
        if full_name in myges:
            del myges[full_name]
            save_json(MYGES_FILE, myges)

        # Nettoyer les photos de visage associées
        meta = load_json(META_FILE, default=[])
        new_meta = []
        for entry in meta:
            if entry.get("name", "").lower() == full_name.lower():
                path = FACES_DIR / entry["filename"]
                try:
                    if path.exists():
                        path.unlink()
                except Exception as e:
                    print(f"Error deleting face image {path}: {e}")
            else:
                new_meta.append(entry)
        save_json(META_FILE, new_meta)

        return {"status": "deleted", "user": full_name}
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")


@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def dashboard():
    """Dashboard d'administration complet de Bastet (Télémétrie, Comptes, Caméras on-demand, Photos)."""
    html = """<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>Bastet — Administration</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #09090b;
            --bg-card: #18181b;
            --border-color: #27272a;
            --text-primary: #fafafa;
            --text-secondary: #a1a1aa;
            --accent: #6366f1;
            --accent-hover: #4f46e5;
            --success: #10b981;
            --danger: #e11d48;
            --warning: #f59e0b;
            --glass: rgba(24, 24, 27, 0.8);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
        }

        body {
            background-color: var(--bg-main);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            overflow: hidden;
        }

        h1, h2, h3, h4, .font-outfit {
            font-family: 'Outfit', sans-serif;
        }

        .sidebar {
            width: 280px;
            background-color: var(--bg-card);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            padding: 2rem 1.5rem;
            height: 100vh;
            flex-shrink: 0;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 3rem;
        }

        .brand-name {
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: 0.05em;
            background: linear-gradient(135deg, #a5b4fc, var(--accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .nav-menu {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            flex-grow: 1;
        }

        .nav-item {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            color: var(--text-secondary);
            text-decoration: none;
            cursor: pointer;
            font-weight: 500;
            transition: all 0.2s ease;
        }

        .nav-item:hover, .nav-item.active {
            background-color: var(--border-color);
            color: var(--text-primary);
        }

        .nav-item.active {
            border-left: 3px solid var(--accent);
            background-color: rgba(99, 102, 241, 0.1);
        }

        .content-wrapper {
            flex: 1;
            display: flex;
            flex-direction: column;
            height: 100vh;
            overflow-y: auto;
            padding: 2.5rem;
        }

        .header-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
        }

        .header-title {
            font-size: 2rem;
            font-weight: 700;
        }

        .header-subtitle {
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }

        .tab-content {
            display: none;
            animation: fadeIn 0.3s ease;
        }

        .tab-content.active {
            display: block;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            position: relative;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }

        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(0, 0, 0, 0.3);
        }

        .card-title {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: var(--text-primary);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .gauge-container {
            display: flex;
            justify-content: space-around;
            gap: 1rem;
            margin-top: 1rem;
        }

        .gauge-item {
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            flex: 1;
        }

        .gauge-circle {
            position: relative;
            width: 80px;
            height: 80px;
        }

        .circular-chart {
            display: block;
            margin: 0 auto;
            max-width: 100%;
        }

        .circle-bg {
            fill: none;
            stroke: #27272a;
            stroke-width: 3;
        }

        .circle {
            fill: none;
            stroke-width: 3;
            stroke-linecap: round;
            transition: stroke-dasharray 0.3s ease;
        }

        .cpu-gauge .circle { stroke: #6366f1; }
        .ram-gauge .circle { stroke: #10b981; }
        .temp-gauge .circle { stroke: #f59e0b; }

        .gauge-value {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 1rem;
            font-weight: 700;
            font-family: 'Outfit', sans-serif;
        }

        .gauge-label {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 0.5rem;
            font-weight: 500;
        }

        .stream-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .stream-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        .stream-placeholder {
            height: 280px;
            background-color: #0c0c0e;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            color: var(--text-secondary);
            gap: 1rem;
            position: relative;
        }

        .stream-placeholder:hover {
            color: var(--text-primary);
        }

        .stream-placeholder svg {
            width: 48px;
            height: 48px;
            fill: currentColor;
            transition: transform 0.2s ease;
        }

        .stream-placeholder:hover svg {
            transform: scale(1.1);
        }

        .stream-iframe {
            width: 100%;
            height: 280px;
            border: none;
            background-color: #000;
            display: none;
        }

        .stream-controls {
            padding: 1rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background-color: #121214;
            border-top: 1px solid var(--border-color);
        }

        .status-badge {
            font-size: 0.75rem;
            padding: 0.25rem 0.6rem;
            border-radius: 9999px;
            font-weight: 600;
            background-color: #27272a;
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
        }

        .status-badge.active, .status-badge.online {
            background-color: rgba(16, 185, 129, 0.15);
            color: #34d399;
        }

        .status-badge.hibernating {
            background-color: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
        }

        .status-badge.offline {
            background-color: rgba(225, 29, 72, 0.15);
            color: #f87171;
        }

        .user-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .user-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: all 0.2s ease;
        }

        .user-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.25);
        }

        .user-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 1.25rem;
        }

        .user-info-meta {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .user-avatar {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--accent), #818cf8);
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Outfit', sans-serif;
            font-size: 1.1rem;
            font-weight: 700;
            color: white;
        }

        .user-title-box h3 {
            font-size: 1.05rem;
            font-weight: 600;
        }

        .user-title-box p {
            font-size: 0.8rem;
            color: var(--text-secondary);
        }

        .user-badge {
            background-color: rgba(99, 102, 241, 0.15);
            color: #a5b4fc;
            border: 1px solid rgba(99, 102, 241, 0.3);
            font-size: 0.7rem;
            padding: 0.2rem 0.5rem;
            border-radius: 9999px;
            font-weight: 600;
        }

        .user-badge.admin {
            background-color: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .user-details {
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
            margin-bottom: 1.5rem;
            padding: 0.5rem 0;
        }

        .user-detail-item {
            font-size: 0.85rem;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .user-detail-item svg {
            color: #71717a;
            flex-shrink: 0;
        }

        .user-actions {
            display: flex;
            gap: 0.5rem;
            border-top: 1px solid var(--border-color);
            padding-top: 1rem;
        }

        .btn {
            padding: 0.55rem 1rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s ease;
            border: none;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            justify-content: center;
        }

        .btn-primary {
            background-color: var(--accent);
            color: white;
        }

        .btn-primary:hover {
            background-color: var(--accent-hover);
        }

        .btn-secondary {
            background-color: var(--border-color);
            color: var(--text-primary);
            border: 1px solid #3f3f46;
        }

        .btn-secondary:hover {
            background-color: #2e2e33;
        }

        .btn-danger {
            background-color: rgba(225, 29, 72, 0.1);
            color: #fb7185;
            border: 1px solid rgba(225, 29, 72, 0.2);
        }

        .btn-danger:hover {
            background-color: var(--danger);
            color: white;
        }

        .btn-success {
            background-color: rgba(16, 185, 129, 0.1);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.2);
        }

        .btn-success:hover {
            background-color: var(--success);
            color: #09090b;
        }

        .modal-overlay {
            position: fixed;
            inset: 0;
            background-color: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 100;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }

        .modal-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        .modal-content {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            width: 95%;
            max-width: 500px;
            padding: 2rem;
            transform: scale(0.95);
            transition: transform 0.3s ease;
            max-height: 90vh;
            overflow-y: auto;
        }

        .modal-overlay.active .modal-content {
            transform: scale(1);
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        .modal-close {
            cursor: pointer;
            color: var(--text-secondary);
            background: none;
            border: none;
            font-size: 1.5rem;
        }

        .modal-close:hover {
            color: var(--text-primary);
        }

        .form-group {
            margin-bottom: 1.25rem;
        }

        .form-label {
            display: block;
            font-size: 0.85rem;
            font-weight: 500;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }

        .form-input {
            width: 100%;
            background-color: var(--bg-main);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-primary);
            padding: 0.65rem 0.85rem;
            font-size: 0.9rem;
        }

        .form-input:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
        }

        .form-row-layout {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
        }

        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-top: 1rem;
        }

        .faces-section {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
        }

        .face-user-header {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: var(--text-primary);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
        }

        .faces-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 1rem;
        }

        .face-img-card {
            background-color: #0c0c0e;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            position: relative;
            aspect-ratio: 1;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .face-img-element {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.3s ease;
            cursor: pointer;
        }

        .face-img-card:hover .face-img-element {
            transform: scale(1.05);
        }

        .face-img-overlay {
            position: absolute;
            inset: 0;
            background: linear-gradient(to top, rgba(0, 0, 0, 0.85) 0%, rgba(0,0,0,0) 60%);
            opacity: 0;
            transition: opacity 0.2s ease;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            padding: 0.5rem;
            pointer-events: none;
        }

        .face-img-card:hover .face-img-overlay {
            opacity: 1;
        }

        .face-img-info {
            font-size: 0.7rem;
            color: var(--text-secondary);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .face-delete-btn {
            position: absolute;
            top: 0.35rem;
            right: 0.35rem;
            background: rgba(225, 29, 72, 0.95);
            color: white;
            border: none;
            border-radius: 50%;
            width: 24px;
            height: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.75rem;
            opacity: 0;
            transition: opacity 0.2s ease;
            z-index: 10;
        }

        .face-img-card:hover .face-delete-btn {
            opacity: 1;
        }

        .upload-box {
            border: 2px dashed var(--border-color);
            border-radius: 12px;
            padding: 2rem;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            background-color: rgba(24, 24, 27, 0.3);
            margin-bottom: 2rem;
        }

        .upload-box:hover {
            border-color: var(--accent);
            background-color: rgba(99, 102, 241, 0.05);
        }

        .progress-bar-container {
            background-color: var(--border-color);
            height: 8px;
            border-radius: 999px;
            overflow: hidden;
            margin: 1rem 0;
        }

        .progress-bar-fill {
            height: 100%;
            background-color: var(--accent);
            width: 0%;
            transition: width 0.4s ease;
        }

        .auth-overlay {
            position: fixed;
            inset: 0;
            background-color: var(--bg-main);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }

        .auth-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 2.5rem;
            border-radius: 16px;
            width: 90%;
            max-width: 400px;
            text-align: center;
        }

        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        .sensor-item {
            display: flex;
            justify-content: space-between;
            padding: 0.5rem 0;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.9rem;
        }

        .sensor-item:last-child {
            border-bottom: none;
        }

        .sensor-label {
            color: var(--text-secondary);
        }

        .sensor-val {
            font-weight: 600;
        }
        
        #chat-messages-box::-webkit-scrollbar {
            width: 4px;
        }
        #chat-messages-box::-webkit-scrollbar-track {
            background: transparent;
        }
        #chat-messages-box::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 999px;
        }
    </style>
</head>
<body>
    <!-- Login screen -->
    <div id="authOverlay" class="auth-overlay">
        <div class="auth-card">
            <h2 class="font-outfit" style="font-size: 1.5rem; margin-bottom: 0.5rem;">Panel d'Administration</h2>
            <p style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 2rem;">Veuillez saisir votre clé d'accès Bastet</p>
            <form onsubmit="handleLoginSubmit(event)">
                <div class="form-group" style="text-align: left;">
                    <label class="form-label" for="tokenInput">Clé X-API-Token</label>
                    <input type="password" id="tokenInput" class="form-input" placeholder="bst_..." required autocomplete="current-password"/>
                </div>
                <button type="submit" class="btn btn-primary" style="width: 100%; justify-content: center; margin-top: 1rem;">Se connecter</button>
            </form>
        </div>
    </div>

    <!-- Sidebar navigation -->
    <div class="sidebar">
        <div class="brand">
            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: #6366f1;">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
            </svg>
            <span class="brand-name font-outfit">BASTET GATEWAY</span>
        </div>
        <ul class="nav-menu">
            <li class="nav-item active" onclick="switchTab('dashboard')" id="nav-dashboard">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>
                </svg>
                <span>Vue d'ensemble</span>
            </li>
            <li class="nav-item" onclick="switchTab('users')" id="nav-users">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
                <span>Comptes & MyGES</span>
            </li>
            <li class="nav-item" onclick="switchTab('faces')" id="nav-faces">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>
                </svg>
                <span>Galerie Visages</span>
            </li>
            <li class="nav-item" onclick="switchTab('system')" id="nav-system">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                </svg>
                <span>Système & Updates</span>
            </li>
        </ul>
        <div style="margin-top: auto; border-top: 1px solid var(--border-color); padding-top: 1.5rem;">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem;">
                <span style="font-size: 0.8rem; color: var(--text-secondary);">Statut Robot :</span>
                <span id="robot-status-badge" class="status-badge offline">Hors-ligne</span>
            </div>
            <button class="btn btn-secondary" onclick="logout()" style="width: 100%; justify-content: center; gap: 0.5rem;">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>
                </svg>
                Déconnexion
            </button>
        </div>
    </div>

    <!-- Main Content Panel -->
    <div class="content-wrapper">
        <!-- HEADER -->
        <div class="header-bar">
            <div>
                <h1 id="tab-title" class="header-title font-outfit">Vue d'ensemble</h1>
                <p id="tab-subtitle" class="header-subtitle">Statistiques en direct et flux caméras du robot Bastet.</p>
            </div>
        </div>

        <!-- ─────────────────── TAB 1: DASHBOARD ─────────────────── -->
        <div id="tab-dashboard-content" class="tab-content active">
            <!-- Telemetry Gauges -->
            <div class="card-grid">
                <div class="card">
                    <div class="card-title">Télémétrie Robot</div>
                    <div class="gauge-container">
                        <div class="gauge-item">
                            <div class="gauge-circle cpu-gauge">
                                <svg viewBox="0 0 36 36" class="circular-chart">
                                    <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                    <path id="gauge-cpu" class="circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                </svg>
                                <span id="gauge-cpu-val" class="gauge-value">--%</span>
                            </div>
                            <span class="gauge-label">Processeur (CPU)</span>
                        </div>
                        <div class="gauge-item">
                            <div class="gauge-circle ram-gauge">
                                <svg viewBox="0 0 36 36" class="circular-chart">
                                    <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                    <path id="gauge-ram" class="circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                </svg>
                                <span id="gauge-ram-val" class="gauge-value">--%</span>
                            </div>
                            <span class="gauge-label">Mémoire (RAM)</span>
                        </div>
                        <div class="gauge-item">
                            <div class="gauge-circle temp-gauge">
                                <svg viewBox="0 0 36 36" class="circular-chart">
                                    <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                    <path id="gauge-temp" class="circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                </svg>
                                <span id="gauge-temp-val" class="gauge-value">--°C</span>
                            </div>
                            <span class="gauge-label">Température SoC</span>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <div class="card-title">Capteurs embarqués</div>
                    <div id="sensors-container" style="display: flex; flex-direction: column; gap: 0.25rem;">
                        <div class="sensor-item"><span class="sensor-label">Dernière personne détectée</span><span id="sensor-seen-person" class="sensor-val">--</span></div>
                        <div class="sensor-item"><span class="sensor-label">Objets repérés</span><span id="sensor-seen-objects" class="sensor-val">--</span></div>
                        <div class="sensor-item"><span class="sensor-label">Version Logiciel Robot</span><span id="sensor-version" class="sensor-val">--</span></div>
                        <div class="sensor-item"><span class="sensor-label">Dernière mise à jour</span><span id="sensor-last-seen" class="sensor-val">--</span></div>
                    </div>
                </div>
                
                <!-- Live Chat Card -->
                <div class="card" style="display: flex; flex-direction: column; min-height: 280px; max-height: 280px;">
                    <div class="card-title">Conversation Live avec Bastet</div>
                    <div id="chat-messages-box" style="flex: 1; overflow-y: auto; padding-right: 0.5rem; margin-top: 0.5rem; display: flex; flex-direction: column;">
                        <div style="text-align: center; color: var(--text-secondary); font-size: 0.85rem; padding: 2rem 0;">Aucune conversation en cours.</div>
                    </div>
                </div>
            </div>

            <!-- Caméras on-demand -->
            <div class="section-header">
                <h2 class="font-outfit" style="font-size: 1.3rem;">Flux Caméras en Direct (À la demande)</h2>
            </div>
            <div class="stream-grid">
                <!-- Caméra 1 -->
                <div class="stream-card">
                    <div id="stream-placeholder-1" class="stream-placeholder" onclick="toggleStream(1)">
                        <svg viewBox="0 0 24 24">
                            <polygon points="5 3 19 12 5 21 5 3"/>
                        </svg>
                        <span>Cliquer pour activer Caméra 1 (Avant)</span>
                    </div>
                    <iframe id="stream-iframe-1" class="stream-iframe"></iframe>
                    <div class="stream-controls">
                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600;">Caméra 1 — Avant</h4>
                            <span id="stream-status-1" class="status-badge">Inactif</span>
                        </div>
                        <button class="btn btn-secondary" onclick="toggleStream(1)">
                            <span id="stream-btn-text-1">Démarrer le flux</span>
                        </button>
                    </div>
                </div>

                <!-- Caméra 2 -->
                <div class="stream-card">
                    <div id="stream-placeholder-2" class="stream-placeholder" onclick="toggleStream(2)">
                        <svg viewBox="0 0 24 24">
                            <polygon points="5 3 19 12 5 21 5 3"/>
                        </svg>
                        <span>Cliquer pour activer Caméra 2 (Arrière)</span>
                    </div>
                    <iframe id="stream-iframe-2" class="stream-iframe"></iframe>
                    <div class="stream-controls">
                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600;">Caméra 2 — Autre angle</h4>
                            <span id="stream-status-2" class="status-badge">Inactif</span>
                        </div>
                        <button class="btn btn-secondary" onclick="toggleStream(2)">
                            <span id="stream-btn-text-2">Démarrer le flux</span>
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ─────────────────── TAB 2: USERS & MYGES ─────────────────── -->
        <div id="tab-users-content" class="tab-content">
            <div class="section-header">
                <h2 class="font-outfit" style="font-size: 1.3rem;">Liste des Utilisateurs Enregistrés</h2>
                <button class="btn btn-primary" onclick="openAddUserModal()">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                    </svg>
                    Ajouter un utilisateur
                </button>
            </div>
            <div id="users-container" class="user-grid">
                <!-- User Cards will be loaded here dynamically -->
            </div>
        </div>

        <!-- ─────────────────── TAB 3: FACES GALLERY ─────────────────── -->
        <div id="tab-faces-content" class="tab-content">
            <div class="upload-box" onclick="triggerFaceUpload()">
                <svg viewBox="0 0 24 24" width="36" height="36" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--accent); margin: 0 auto 0.75rem;">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
                </svg>
                <h4 style="font-size: 1rem; font-weight: 600; margin-bottom: 0.25rem;">Charger une nouvelle photo de visage</h4>
                <p style="color: var(--text-secondary); font-size: 0.8rem;">Glissez-déposez ou cliquez ici pour sélectionner une image (Max 8 par utilisateur)</p>
                <input type="file" id="face-file-input" style="display: none;" accept="image/*" onchange="handleFaceUploadSelected(event)"/>
            </div>

            <div id="faces-gallery-container">
                <!-- Faces grouped by name will be loaded here dynamically -->
            </div>
        </div>

        <!-- ─────────────────── TAB 4: SYSTEM & UPDATES ─────────────────── -->
        <div id="tab-system-content" class="tab-content">
            <div class="card-grid">
                <!-- Gateway Update Card -->
                <div class="card">
                    <div class="card-title">Mise à jour — Gateway Serveur</div>
                    <div style="margin: 1rem 0;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                            <span>Version actuelle :</span>
                            <span id="gateway-current-version" style="font-weight: 600; color: var(--text-primary);">v0.0.0</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                            <span>Dernière version dispo :</span>
                            <span id="gateway-latest-version" style="font-weight: 600; color: var(--success);">v0.0.0</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary);">
                            <span>Statut de la Gateway :</span>
                            <span id="gateway-update-status" style="font-weight: 600; color: var(--text-primary);">Prêt</span>
                        </div>
                        <div class="progress-bar-container">
                            <div id="gateway-update-bar" class="progress-bar-value progress-bar-fill"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: 500;">
                            <span>Progression</span>
                            <span id="gateway-update-percent">0%</span>
                        </div>
                    </div>
                    <button class="btn btn-secondary" onclick="triggerUpdate('gateway')" style="width: 100%; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                        </svg>
                        Lancer la mise à jour Gateway
                    </button>
                </div>

                <!-- Robot Update Card -->
                <div class="card">
                    <div class="card-title">Mise à jour — Robot Pi</div>
                    <div style="margin: 1rem 0;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                            <span>Version actuelle :</span>
                            <span id="robot-current-version" style="font-weight: 600; color: var(--text-primary);">v0.0.0</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                            <span>Dernière version dispo :</span>
                            <span id="robot-latest-version" style="font-weight: 600; color: var(--success);">v0.0.0</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary);">
                            <span>Statut de l'agent :</span>
                            <span id="robot-update-status" style="font-weight: 600; color: var(--text-primary);">Prêt</span>
                        </div>
                        <div class="progress-bar-container">
                            <div id="robot-update-bar" class="progress-bar-value progress-bar-fill"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: 500;">
                            <span>Progression</span>
                            <span id="robot-update-percent">0%</span>
                        </div>
                    </div>
                    <button class="btn btn-secondary" onclick="triggerUpdate('robot')" style="width: 100%; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                        </svg>
                        Lancer la mise à jour Robot
                    </button>
                </div>
            </div>

            <div class="card">
                <div class="card-title">Gestion des services ROS / spotbot.service</div>
                <div style="display: flex; align-items: center; justify-content: space-between; padding: 1rem 0; border-bottom: 1px solid var(--border-color);">
                    <div>
                        <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.25rem;">Service SpotBot ROS Core</h4>
                        <p style="color: var(--text-secondary); font-size: 0.8rem;">Gère la navigation, les moteurs et la détection d'obstacles du robot.</p>
                    </div>
                    <span id="spotbot-service-badge" class="status-badge">Statut inconnu</span>
                </div>
                <div style="display: flex; gap: 0.75rem; margin-top: 1.5rem;">
                    <button class="btn btn-success" onclick="controlRobotService('start')" style="gap: 0.5rem;">
                        ▶ Démarrer SpotBot
                    </button>
                    <button class="btn btn-danger" onclick="controlRobotService('stop')" style="gap: 0.5rem;">
                        ■ Arrêter SpotBot
                    </button>
                    <button class="btn btn-secondary" onclick="controlRobotService('restart')" style="gap: 0.5rem;">
                        🔄 Redémarrer
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal : Ajouter / Éditer un utilisateur -->
    <div id="userModal" class="modal-overlay" onclick="closeUserModalOnClick(event)">
        <div class="modal-content">
            <div class="modal-header">
                <h3 id="modal-user-title" class="font-outfit" style="font-size: 1.25rem; font-weight: 700;">Ajouter un Compte</h3>
                <button class="modal-close" onclick="closeUserModal()">&times;</button>
            </div>
            <form onsubmit="handleUserSubmit(event)">
                <input type="hidden" id="form-old-fullname"/>
                <div class="form-row-layout">
                    <div class="form-group">
                        <label class="form-label" for="form-firstname">Prénom</label>
                        <input type="text" id="form-firstname" class="form-input" required/>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="form-lastname">Nom</label>
                        <input type="text" id="form-lastname" class="form-input" required/>
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label" for="form-pseudo">Pseudo</label>
                    <input type="text" id="form-pseudo" class="form-input" required/>
                </div>
                <div class="form-group">
                    <label class="form-label" for="form-email">Adresse E-mail</label>
                    <input type="email" id="form-email" class="form-input" required/>
                </div>
                <div class="form-group">
                    <label class="form-label" for="form-phone">Téléphone</label>
                    <input type="tel" id="form-phone" class="form-input" placeholder="06..."/>
                </div>
                <div class="form-group">
                    <label class="form-label" for="form-password">Mot de passe (Laisser vide pour ne pas modifier)</label>
                    <input type="password" id="form-password" class="form-input" autocomplete="new-password"/>
                </div>
                <div class="form-group">
                    <label class="form-label" for="form-preferences">Configuration / Préférences (JSON)</label>
                    <textarea id="form-preferences" class="form-input" rows="3" placeholder='{"unit": "metric"}' style="font-family: monospace; font-size: 0.85rem; resize: vertical;"></textarea>
                </div>
                <div class="checkbox-group">
                    <input type="checkbox" id="form-is-admin" style="accent-color: var(--accent); width: 16px; height: 16px;"/>
                    <label for="form-is-admin" style="font-size: 0.85rem; cursor: pointer; font-weight: 500;">Définir comme Administrateur</label>
                </div>
                <button type="submit" class="btn btn-primary" style="width: 100%; justify-content: center; margin-top: 1.5rem;">Sauvegarder l'utilisateur</button>
            </form>
        </div>
    </div>

    <!-- Modal : Identifiants MyGES -->
    <div id="mygesModal" class="modal-overlay" onclick="closeMygesModalOnClick(event)">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="font-outfit" style="font-size: 1.25rem; font-weight: 700;">Identifiants MyGES — <span id="myges-modal-username">User</span></h3>
                <button class="modal-close" onclick="closeMygesModal()">&times;</button>
            </div>
            <form onsubmit="handleMygesSubmit(event)">
                <input type="hidden" id="form-myges-name"/>
                <div class="form-group">
                    <label class="form-label" for="form-myges-username">Nom d'utilisateur MyGES</label>
                    <input type="text" id="form-myges-username" class="form-input" placeholder="nom.prenom" required/>
                </div>
                <div class="form-group">
                    <label class="form-label" for="form-myges-password">Mot de passe MyGES</label>
                    <input type="password" id="form-myges-password" class="form-input" placeholder="••••••••" required autocomplete="current-password"/>
                </div>
                <button type="submit" class="btn btn-primary" style="width: 100%; justify-content: center; margin-top: 1.5rem;">Enregistrer les identifiants</button>
            </form>
        </div>
    </div>

    <!-- Modal : Sélection de l'utilisateur pour l'upload d'un visage -->
    <div id="faceUploadUserModal" class="modal-overlay" onclick="closeFaceUploadUserModalOnClick(event)">
        <div class="modal-content">
            <div class="modal-header">
                <h3 class="font-outfit" style="font-size: 1.25rem; font-weight: 700;">Associer la photo</h3>
                <button class="modal-close" onclick="closeFaceUploadUserModal()">&times;</button>
            </div>
            <div class="form-group">
                <label class="form-label" for="face-upload-name-select">À qui appartient ce visage ?</label>
                <select id="face-upload-name-select" class="form-input">
                    <!-- Loaded dynamically -->
                </select>
                <div style="margin: 0.75rem 0; text-align: center; color: var(--text-secondary); font-size: 0.8rem;">ou créer un nouveau profil :</div>
                <input type="text" id="face-upload-new-name" class="form-input" placeholder="Saisir un nouveau nom complet..."/>
            </div>
            <button class="btn btn-primary" onclick="executeFaceUpload()" style="width: 100%; justify-content: center; margin-top: 1.5rem;">Téléverser la photo</button>
        </div>
    </div>

    <!-- Screen lightbox overlay for viewing images -->
    <div id="lightbox" class="modal-overlay" onclick="closeLightbox()" style="background-color: rgba(0,0,0,0.95); cursor: zoom-out;">
        <img id="lightbox-img" style="max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 4px; box-shadow: 0 10px 40px rgba(0,0,0,0.8);"/>
    </div>

    <script>
        let apiToken = localStorage.getItem('bastet_api_token') || '';
        let activeTab = 'dashboard';
        let telemetryInterval = null;
        let updateInterval = null;
        let accountsCached = {};
        window.camWebsockets = { 1: null, 2: null };

        // ─── INIT ─────────────────────────────────────────────────────────────
        
        async function checkAuth() {
            if (!apiToken) {
                showLogin();
                return;
            }
            try {
                // Try fetching accounts to verify X-API-Token validity
                const res = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });
                if (res.status === 200) {
                    hideLogin();
                    initDashboard();
                } else {
                    showLogin();
                }
            } catch (e) {
                showLogin();
            }
        }

        function showLogin() {
            document.getElementById('authOverlay').style.display = 'flex';
            clearIntervals();
        }

        function hideLogin() {
            document.getElementById('authOverlay').style.display = 'none';
        }

        function handleLoginSubmit(e) {
            e.preventDefault();
            apiToken = document.getElementById('tokenInput').value.trim();
            localStorage.setItem('bastet_api_token', apiToken);
            checkAuth();
        }

        function logout() {
            // Close active streams
            for (let id of [1, 2]) {
                if (window.camWebsockets[id]) {
                    window.camWebsockets[id].send(JSON.stringify({type: "release_camera", camera: id}));
                    window.camWebsockets[id].close();
                }
            }
            apiToken = '';
            localStorage.removeItem('bastet_api_token');
            showLogin();
        }

        function initDashboard() {
            switchTab(activeTab);
            startIntervals();
            initDragAndDrop();
        }

        // --- INTERVALS ---
        function startIntervals() {
            clearIntervals();
            fetchTelemetry();
            fetchUpdatesProgress();
            telemetryInterval = setInterval(fetchTelemetry, 2000);
            updateInterval = setInterval(fetchUpdatesProgress, 2000);
        }

        function clearIntervals() {
            if (telemetryInterval) clearInterval(telemetryInterval);
            if (updateInterval) clearInterval(updateInterval);
        }

        // ─── TABS SWITCHING ───────────────────────────────────────────────────

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
            
            const targetContent = document.getElementById(`tab-${tabId}-content`);
            const targetNav = document.getElementById(`nav-${tabId}`);
            if (targetContent && targetNav) {
                targetContent.classList.add('active');
                targetNav.classList.add('active');
                activeTab = tabId;
            }

            const titles = {
                'dashboard': { title: "Vue d'ensemble", subtitle: "Statistiques en direct et flux caméras du robot Bastet." },
                'users': { title: "Comptes & MyGES", subtitle: "Gérer les profils utilisateurs et leurs identifiants d'agenda." },
                'faces': { title: "Galerie Visages", subtitle: "Gérer les visages enregistrés pour la reconnaissance faciale." },
                'system': { title: "Système & Updates", subtitle: "Suivi des mises à jour logicielles et des services ROS." }
            };

            const headerInfo = titles[tabId] || titles['dashboard'];
            document.getElementById('tab-title').textContent = headerInfo.title;
            document.getElementById('tab-subtitle').textContent = headerInfo.subtitle;

            if (tabId === 'users') {
                loadAccounts();
            } else if (tabId === 'faces') {
                loadFacesGallery();
            }
        }

        // ─── TELEMETRY ────────────────────────────────────────────────────────

        async function fetchTelemetry() {
            try {
                const res = await fetch('/core/state', { headers: { 'X-API-Token': apiToken } });
                if (res.status === 403) { logout(); return; }
                if (res.ok) {
                    const state = await res.json();
                    
                    // Update robot online badge
                    const robotBadge = document.getElementById('robot-status-badge');
                    const robotStatus = state.robot_status || 'offline';
                    
                    robotBadge.className = `status-badge ${robotStatus}`;
                    if (robotStatus === 'online') {
                        robotBadge.textContent = 'En ligne';
                    } else if (robotStatus === 'hibernating') {
                        robotBadge.textContent = 'Hibernation';
                    } else if (robotStatus === 'idle') {
                        robotBadge.textContent = 'Inactif';
                    } else {
                        robotBadge.textContent = 'Hors-ligne';
                    }

                    // Update Gauges
                    const sensors = state.sensors || {};
                    const cpu = sensors.cpu_percent || 0;
                    const ram = sensors.ram_percent || 0;
                    const temp = sensors.temp_c || 0;

                    updateGaugeCircle('gauge-cpu', cpu);
                    document.getElementById('gauge-cpu-val').textContent = `${Math.round(cpu)}%`;

                    updateGaugeCircle('gauge-ram', ram);
                    document.getElementById('gauge-ram-val').textContent = `${Math.round(ram)}%`;

                    updateGaugeCircle('gauge-temp', (temp / 100) * 100); // assume max temp 100C for percentage representation
                    document.getElementById('gauge-temp-val').textContent = `${Math.round(temp)}°C`;

                    // Other sensors metadata
                    document.getElementById('sensor-seen-person').textContent = state.seen_person || 'Personne';
                    document.getElementById('sensor-seen-objects').textContent = (state.seen_objects && state.seen_objects.length > 0) ? state.seen_objects.join(', ') : 'Aucun';
                    document.getElementById('sensor-version').textContent = state.robot_version || 'v0.0.0';
                    
                    if (state.updated_at) {
                        const date = new Date(state.updated_at * 1000);
                        document.getElementById('sensor-last-seen').textContent = date.toLocaleTimeString();
                    } else {
                        document.getElementById('sensor-last-seen').textContent = '--';
                    }

                    // ROS service badge
                    const serviceBadge = document.getElementById('spotbot-service-badge');
                    const isSpotbotActive = sensors.spotbot_service_active;
                    if (isSpotbotActive === true) {
                        serviceBadge.textContent = 'Actif';
                        serviceBadge.className = 'status-badge active';
                    } else if (isSpotbotActive === false) {
                        serviceBadge.textContent = 'Arrêté';
                        serviceBadge.className = 'status-badge offline';
                    } else {
                        serviceBadge.textContent = 'Inconnu';
                        serviceBadge.className = 'status-badge';
                    }
                    
                    // Live Chat messages display
                    const chatContainer = document.getElementById('chat-messages-box');
                    if (chatContainer && state.last_chat) {
                        const chatHash = JSON.stringify(state.last_chat);
                        if (chatContainer.dataset.lastHash !== chatHash) {
                            chatContainer.dataset.lastHash = chatHash;
                            chatContainer.innerHTML = '';
                            if (state.last_chat.length === 0) {
                                chatContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); font-size: 0.85rem; padding: 2rem 0;">Aucune conversation en cours.</div>`;
                            } else {
                                state.last_chat.forEach(msg => {
                                    const role = msg.role || msg.sender || 'user';
                                    const text = msg.content || msg.text || '';
                                    
                                    const isRobot = role.toLowerCase() === 'assistant' || role.toLowerCase() === 'bot' || role.toLowerCase() === 'robot';
                                    
                                    const bubble = document.createElement('div');
                                    bubble.style.display = 'flex';
                                    bubble.style.flexDirection = 'column';
                                    bubble.style.alignItems = isRobot ? 'flex-start' : 'flex-end';
                                    bubble.style.marginBottom = '0.75rem';
                                    
                                    const senderName = isRobot ? 'Bastet' : 'Utilisateur';
                                    const senderColor = isRobot ? '#a5b4fc' : 'var(--text-secondary)';
                                    
                                    bubble.innerHTML = `
                                        <span style="font-size: 0.7rem; color: ${senderColor}; margin-bottom: 0.15rem; font-weight: 600; padding: 0 0.25rem;">${senderName}</span>
                                        <div style="background-color: ${isRobot ? 'rgba(99, 102, 241, 0.15)' : 'var(--border-color)'}; 
                                                    border: 1px solid ${isRobot ? 'rgba(99, 102, 241, 0.3)' : 'transparent'};
                                                    color: var(--text-primary);
                                                    padding: 0.6rem 0.85rem;
                                                    border-radius: 12px;
                                                    max-width: 85%;
                                                    font-size: 0.85rem;
                                                    line-height: 1.4;
                                                    word-break: break-word;
                                                    white-space: pre-wrap;">${text}</div>
                                    `;
                                    chatContainer.appendChild(bubble);
                                });
                                // Scroll to bottom
                                chatContainer.scrollTop = chatContainer.scrollHeight;
                            }
                        }
                    }
                }
            } catch (e) {
                console.error("Telemetry fetch error:", e);
            }
        }

        function updateGaugeCircle(id, val) {
            const el = document.getElementById(id);
            if (el) {
                const percent = Math.min(Math.max(val, 0), 100);
                el.setAttribute('stroke-dasharray', `${percent}, 100`);
            }
        }

        // ─── CAMERA STREAM ON-DEMAND ─────────────────────────────────────────

        function toggleStream(camId) {
            const placeholder = document.getElementById(`stream-placeholder-${camId}`);
            const iframe = document.getElementById(`stream-iframe-${camId}`);
            const statusEl = document.getElementById(`stream-status-${camId}`);
            const btnText = document.getElementById(`stream-btn-text-${camId}`);
            
            if (!window.camWebsockets[camId]) {
                // Request stream
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${window.location.host}/ws/app?token=${apiToken}`;
                const ws = new WebSocket(wsUrl);
                
                ws.onopen = () => {
                    ws.send(JSON.stringify({type: "request_camera", camera: camId}));
                    iframe.src = `http://${window.location.hostname}:48889/cam${camId}/`;
                    iframe.style.display = 'block';
                    placeholder.style.display = 'none';
                    statusEl.textContent = 'En direct';
                    statusEl.className = 'status-badge active';
                    btnText.textContent = 'Couper Caméra';
                };
                
                ws.onclose = () => {
                    iframe.style.display = 'none';
                    placeholder.style.display = 'flex';
                    iframe.src = '';
                    statusEl.textContent = 'Inactif';
                    statusEl.className = 'status-badge';
                    btnText.textContent = 'Démarrer Caméra';
                    window.camWebsockets[camId] = null;
                };

                ws.onerror = (err) => {
                    console.error("WebSocket camera error", err);
                    ws.close();
                };
                
                window.camWebsockets[camId] = ws;
            } else {
                // Close stream
                window.camWebsockets[camId].send(JSON.stringify({type: "release_camera", camera: camId}));
                window.camWebsockets[camId].close();
                window.camWebsockets[camId] = null;
            }
        }

        // ─── ACCOUNTS MANAGEMENT ─────────────────────────────────────────────

        async function loadAccounts() {
            try {
                const accountsRes = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });
                const mygesRes = await fetch('/myges', { headers: { 'X-API-Token': apiToken } });
                
                if (accountsRes.ok) {
                    const accounts = await accountsRes.json();
                    accountsCached = accounts;
                    
                    let mygesList = {};
                    if (mygesRes.ok) {
                        mygesList = await mygesRes.json();
                    }

                    const container = document.getElementById('users-container');
                    container.innerHTML = '';

                    const keys = Object.keys(accounts);
                    if (keys.length === 0) {
                        container.innerHTML = `
                            <div style="grid-column: 1/-1; text-align: center; padding: 3rem; color: var(--text-secondary);">
                                Aucun compte utilisateur configuré.
                            </div>`;
                        return;
                    }

                    for (const fullName of keys) {
                        const u = accounts[fullName];
                        const initials = ((u.first_name ? u.first_name[0] : '') + (u.last_name ? u.last_name[0] : '')).toUpperCase() || 'U';
                        const adminClass = u.is_admin ? 'admin' : '';
                        const adminLabel = u.is_admin ? 'Administrateur' : 'Utilisateur';
                        
                        const mygesCreds = mygesList[fullName];
                        const mygesBadge = mygesCreds 
                            ? `<span class="status-badge active" style="font-size: 0.75rem;">✅ MyGES : ${mygesCreds.username}</span>`
                            : `<span class="status-badge" style="font-size: 0.75rem; background-color: rgba(225, 29, 72, 0.05); color: #fb7185; border: 1px solid rgba(225, 29, 72, 0.15)">❌ MyGES non configuré</span>`;

                        const card = document.createElement('div');
                        card.className = 'user-card';
                        card.innerHTML = `
                            <div>
                                <div class="user-header">
                                    <div class="user-info-meta">
                                        <div class="user-avatar">${initials}</div>
                                        <div class="user-title-box">
                                            <h3>${u.first_name} ${u.last_name}</h3>
                                            <p>@${u.pseudo || 'sans-pseudo'}</p>
                                        </div>
                                    </div>
                                    <span class="user-badge ${adminClass}">${adminLabel}</span>
                                </div>
                                <div class="user-details">
                                    <div class="user-detail-item">
                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
                                        <span>${u.email}</span>
                                    </div>
                                    <div class="user-detail-item">
                                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 9.24z"/></svg>
                                        <span>${u.phone || 'Non renseigné'}</span>
                                    </div>
                                    <div style="margin-top: 0.5rem;">
                                        ${mygesBadge}
                                    </div>
                                </div>
                            </div>
                            <div class="user-actions">
                                <button class="btn btn-secondary" style="flex: 1;" onclick="openEditUserModal('${fullName}')">Modifier</button>
                                <button class="btn btn-secondary" onclick="openMygesModal('${fullName}')" title="Identifiants MyGES">MyGES</button>
                                <button class="btn btn-danger" onclick="deleteUser('${fullName}')">Supprimer</button>
                            </div>
                        `;
                        container.appendChild(card);
                    }
                }
            } catch (e) {
                console.error("Load accounts error:", e);
            }
        }

        async function deleteUser(fullName) {
            if (!confirm(`Voulez-vous vraiment supprimer le compte de ${fullName} ?
(Cela supprimera également ses identifiants MyGES et ses photos de visage)`)) return;
            try {
                const res = await fetch(`/accounts/${encodeURIComponent(fullName)}`, {
                    method: 'DELETE',
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    loadAccounts();
                } else {
                    alert('Erreur lors de la suppression.');
                }
            } catch (e) {
                alert('Erreur de connexion.');
            }
        }

        // Modals Accounts
        function openAddUserModal() {
            document.getElementById('modal-user-title').textContent = "Ajouter un Compte";
            document.getElementById('form-old-fullname').value = '';
            document.getElementById('form-firstname').value = '';
            document.getElementById('form-lastname').value = '';
            document.getElementById('form-firstname').disabled = false;
            document.getElementById('form-lastname').disabled = false;
            document.getElementById('form-pseudo').value = '';
            document.getElementById('form-email').value = '';
            document.getElementById('form-phone').value = '';
            document.getElementById('form-password').value = '';
            document.getElementById('form-preferences').value = '{}';
            document.getElementById('form-is-admin').checked = false;
            
            document.getElementById('userModal').classList.add('active');
        }

        function openEditUserModal(fullName) {
            const u = accountsCached[fullName];
            if (!u) return;

            document.getElementById('modal-user-title').textContent = `Modifier le profil`;
            document.getElementById('form-old-fullname').value = fullName;
            document.getElementById('form-firstname').value = u.first_name || '';
            document.getElementById('form-lastname').value = u.last_name || '';
            document.getElementById('form-firstname').disabled = true; // Key index part
            document.getElementById('form-lastname').disabled = true; // Key index part
            document.getElementById('form-pseudo').value = u.pseudo || '';
            document.getElementById('form-email').value = u.email || '';
            document.getElementById('form-phone').value = u.phone || '';
            document.getElementById('form-password').value = '';
            document.getElementById('form-preferences').value = JSON.stringify(u.preferences || {}, null, 2);
            document.getElementById('form-is-admin').checked = u.is_admin || false;

            document.getElementById('userModal').classList.add('active');
        }

        function closeUserModal() {
            document.getElementById('userModal').classList.remove('active');
        }

        function closeUserModalOnClick(e) {
            if (e.target === document.getElementById('userModal')) closeUserModal();
        }

        async function handleUserSubmit(e) {
            e.preventDefault();
            const oldFullName = document.getElementById('form-old-fullname').value;
            const firstName = document.getElementById('form-firstname').value.trim();
            const lastName = document.getElementById('form-lastname').value.trim();
            const pseudo = document.getElementById('form-pseudo').value.trim();
            const email = document.getElementById('form-email').value.trim();
            const phone = document.getElementById('form-phone').value.trim();
            const password = document.getElementById('form-password').value;
            const isAdmin = document.getElementById('form-is-admin').checked;

            let preferences = {};
            const prefVal = document.getElementById('form-preferences').value.trim();
            if (prefVal) {
                try {
                    preferences = JSON.parse(prefVal);
                } catch (err) {
                    alert("Format JSON invalide pour les préférences.");
                    return;
                }
            }

            const payload = {
                first_name: firstName,
                last_name: lastName,
                pseudo: pseudo,
                email: email,
                phone: phone,
                is_admin: isAdmin,
                preferences: preferences
            };

            if (password) {
                payload.password = password;
            }

            try {
                // Save user
                const res = await fetch('/accounts', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify(payload)
                });

                if (res.ok) {
                    closeUserModal();
                    loadAccounts();
                } else {
                    const err = await res.text();
                    alert(`Erreur lors de la sauvegarde : ${err}`);
                }
            } catch (e) {
                alert('Erreur de réseau.');
            }
        }

        // Modals MyGES
        function openMygesModal(name) {
            document.getElementById('myges-modal-username').textContent = name;
            document.getElementById('form-myges-name').value = name;
            document.getElementById('form-myges-username').value = '';
            document.getElementById('form-myges-password').value = '';
            
            document.getElementById('mygesModal').classList.add('active');
        }

        function closeMygesModal() {
            document.getElementById('mygesModal').classList.remove('active');
        }

        function closeMygesModalOnClick(e) {
            if (e.target === document.getElementById('mygesModal')) closeMygesModal();
        }

        async function handleMygesSubmit(e) {
            e.preventDefault();
            const name = document.getElementById('form-myges-name').value;
            const username = document.getElementById('form-myges-username').value.trim();
            const password = document.getElementById('form-myges-password').value;

            try {
                const res = await fetch(`/myges?name=${encodeURIComponent(name)}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify({ username, password })
                });

                if (res.ok) {
                    closeMygesModal();
                    loadAccounts();
                } else {
                    alert('Erreur lors de la sauvegarde MyGES.');
                }
            } catch (e) {
                alert('Erreur réseau.');
            }
        }

        // ─── FACES GALLERY ───────────────────────────────────────────────────

        async function loadFacesGallery() {
            try {
                const res = await fetch('/faces', { headers: { 'X-API-Token': apiToken } });
                if (res.ok) {
                    const data = await res.json();
                    const faces = data.faces || [];
                    
                    // Group faces by user name
                    const grouped = {};
                    faces.forEach(f => {
                        if (!grouped[f.name]) grouped[f.name] = [];
                        grouped[f.name].push(f);
                    });

                    const container = document.getElementById('faces-gallery-container');
                    container.innerHTML = '';

                    const keys = Object.keys(grouped);
                    if (keys.length === 0) {
                        container.innerHTML = `
                            <div style="text-align: center; padding: 4rem; color: var(--text-secondary); border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card);">
                                Aucune photo enregistrée pour la reconnaissance faciale.
                            </div>`;
                        return;
                    }

                    for (const name of keys) {
                        const userFaces = grouped[name];
                        const section = document.createElement('div');
                        section.className = 'faces-section';
                        
                        section.innerHTML = `
                            <div class="face-user-header">
                                <span>${name}</span>
                                <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: 500;">${userFaces.length} / 8 photos</span>
                            </div>
                            <div class="faces-grid"></div>
                        `;
                        
                        container.appendChild(section);
                        
                        const grid = section.querySelector(`.faces-grid`);
                        for (const f of userFaces) {
                            const card = document.createElement('div');
                            card.className = 'face-img-card';
                            card.innerHTML = `
                                <img src="#" id="face-img-${f.id}" class="face-img-element" onclick="showLightbox(this.src)" title="Agrandir"/>
                                <button class="face-delete-btn" onclick="deleteFace('${f.id}')" title="Supprimer">✕</button>
                                <div class="face-img-overlay">
                                    <div class="face-img-info">${f.original_name}</div>
                                    <div style="font-size: 0.6rem; color: #71717a;">${new Date(f.uploaded_at).toLocaleDateString()}</div>
                                </div>
                            `;
                            grid.appendChild(card);
                            
                            // Load image with Authorization Headers
                            fetch(`/faces/${f.id}/image`, { headers: { 'X-API-Token': apiToken } })
                                .then(res => res.blob())
                                .then(blob => {
                                    const img = document.getElementById(`face-img-${f.id}`);
                                    if (img) img.src = URL.createObjectURL(blob);
                                })
                                .catch(err => console.error("Error loading face image file:", err));
                        }
                    }
                }
            } catch (e) {
                console.error("Gallery loading error:", e);
            }
        }

        async function deleteFace(faceId) {
            if (!confirm("Voulez-vous supprimer cette photo pour la reconnaissance faciale ?")) return;
            try {
                const res = await fetch(`/faces/${faceId}`, {
                    method: 'DELETE',
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    loadFacesGallery();
                } else {
                    alert('Erreur suppression.');
                }
            } catch (e) {
                alert('Erreur réseau.');
            }
        }

        // Upload faces
        let currentUploadFile = null;
        
        function triggerFaceUpload() {
            document.getElementById('face-file-input').click();
        }

        function handleFaceUploadSelected(e) {
            const files = e.target.files;
            if (!files || files.length === 0) return;
            currentUploadFile = files[0];
            
            // Open modal to choose user
            openFaceUploadUserModal();
        }

        function initDragAndDrop() {
            const uploadBox = document.querySelector('.upload-box');
            if (uploadBox) {
                ['dragenter', 'dragover'].forEach(eventName => {
                    uploadBox.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        uploadBox.style.borderColor = 'var(--accent)';
                        uploadBox.style.backgroundColor = 'rgba(99, 102, 241, 0.08)';
                    }, false);
                });
                ['dragleave', 'drop'].forEach(eventName => {
                    uploadBox.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        uploadBox.style.borderColor = 'var(--border-color)';
                        uploadBox.style.backgroundColor = 'rgba(24, 24, 27, 0.3)';
                    }, false);
                });
                uploadBox.addEventListener('drop', (e) => {
                    const dt = e.dataTransfer;
                    const files = dt.files;
                    if (files && files.length > 0) {
                        currentUploadFile = files[0];
                        openFaceUploadUserModal();
                    }
                }, false);
            }
        }

        async function openFaceUploadUserModal() {
            // Load users list inside selector
            const select = document.getElementById('face-upload-name-select');
            select.innerHTML = '';
            
            // Load accounts
            try {
                const res = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });
                if (res.ok) {
                    const accounts = await res.json();
                    Object.keys(accounts).forEach(name => {
                        const opt = document.createElement('option');
                        opt.value = name;
                        opt.textContent = name;
                        select.appendChild(opt);
                    });
                }
            } catch(e) {}
            
            document.getElementById('face-upload-new-name').value = '';
            document.getElementById('faceUploadUserModal').classList.add('active');
        }

        function closeFaceUploadUserModal() {
            document.getElementById('faceUploadUserModal').classList.remove('active');
            document.getElementById('face-file-input').value = '';
            currentUploadFile = null;
        }

        function closeFaceUploadUserModalOnClick(e) {
            if (e.target === document.getElementById('faceUploadUserModal')) closeFaceUploadUserModal();
        }

        async function executeFaceUpload() {
            if (!currentUploadFile) return;

            let name = document.getElementById('face-upload-new-name').value.trim();
            if (!name) {
                name = document.getElementById('face-upload-name-select').value;
            }

            if (!name) {
                alert("Veuillez sélectionner ou saisir le nom de la personne.");
                return;
            }

            const fd = new FormData();
            fd.append('file', currentUploadFile);

            try {
                // Post to face upload API with name in query param
                const res = await fetch(`/faces/upload?name=${encodeURIComponent(name)}`, {
                    method: 'POST',
                    headers: { 'X-API-Token': apiToken },
                    body: fd
                });

                if (res.ok) {
                    const json = await res.json();
                    if (json.status === 'already_exists') {
                        alert(json.msg);
                    }
                    closeFaceUploadUserModal();
                    loadFacesGallery();
                } else {
                    const txt = await res.text();
                    alert(`Erreur d'upload : ${txt}`);
                }
            } catch (e) {
                alert("Erreur de connexion.");
            }
        }

        // Lightbox
        function showLightbox(src) {
            document.getElementById('lightbox-img').src = src;
            document.getElementById('lightbox').classList.add('active');
        }

        function closeLightbox() {
            document.getElementById('lightbox').classList.remove('active');
        }

        // ─── UPDATER & SERVICES ───────────────────────────────────────────────

        async function fetchUpdatesProgress() {
            try {
                const gatewayRes = await fetch('/system/update/gateway/progress', { headers: { 'X-API-Token': apiToken } });
                const robotRes = await fetch('/system/update/robot/progress', { headers: { 'X-API-Token': apiToken } });

                if (gatewayRes.ok) {
                    const gw = await gatewayRes.json();
                    document.getElementById('gateway-update-status').textContent = gw.status || 'Prêt';
                    document.getElementById('gateway-update-bar').style.width = `${gw.percent}%`;
                    document.getElementById('gateway-update-percent').textContent = `${gw.percent}%`;
                    document.getElementById('gateway-current-version').textContent = gw.current_version || 'Inconnu';
                    document.getElementById('gateway-latest-version').textContent = gw.latest_version || 'Inconnu';
                }

                if (robotRes.ok) {
                    const rb = await robotRes.json();
                    document.getElementById('robot-update-status').textContent = rb.status || 'Prêt';
                    document.getElementById('robot-update-bar').style.width = `${rb.percent}%`;
                    document.getElementById('robot-update-percent').textContent = `${rb.percent}%`;
                    document.getElementById('robot-current-version').textContent = rb.current_version || 'Inconnu';
                    document.getElementById('robot-latest-version').textContent = rb.latest_version || 'Inconnu';
                }
            } catch (e) {
                console.error("Updates progress fetch error:", e);
            }
        }

        async function triggerUpdate(target) {
            if (!confirm(`Voulez-vous vraiment lancer la mise à jour de la ${target === 'gateway' ? 'Gateway' : 'Robot Pi'} ?`)) return;
            try {
                const res = await fetch(`/system/update/${target}`, {
                    method: 'POST',
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    alert('Mise à jour démarrée.');
                    fetchUpdatesProgress();
                } else {
                    alert('Impossible de démarrer la mise à jour.');
                }
            } catch (e) {
                alert('Erreur réseau.');
            }
        }

        async function controlRobotService(action) {
            if (!confirm(`Confirmer l'opération '${action}' sur le service spotbot ?`)) return;
            try {
                // Sent as a websocket action or general command through app gateway route to robot
                // Spotbot agent triggers this when receiving commands from ws. We send a ws command
                const ws = window.camWebsockets[1] || window.camWebsockets[2];
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({ type: "service_control", service: "spotbot", action: action }));
                    alert(`Commande '${action}' envoyée au robot.`);
                } else {
                    // Open a temporary websocket to send the command
                    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                    const wsUrl = `${protocol}//${window.location.host}/ws/app?token=${apiToken}`;
                    const tmpWs = new WebSocket(wsUrl);
                    tmpWs.onopen = () => {
                        tmpWs.send(JSON.stringify({ type: "service_control", service: "spotbot", action: action }));
                        alert(`Commande '${action}' envoyée.`);
                        setTimeout(() => tmpWs.close(), 500);
                    };
                    tmpWs.onerror = () => {
                        alert("Erreur de connexion WebSocket temporaire pour la commande.");
                    };
                }
            } catch (e) {
                alert('Erreur lors du contrôle.');
            }
        }

        // Start checking auth on load
        checkAuth();
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)





# ─── Faces API ────────────────────────────────────────────────────────────────

@app.post("/faces/upload", tags=["Faces"], summary="Upload une image", dependencies=[Depends(verify_token)])
async def upload_face(
    name: str = Query(..., description="Nom de la personne"),
    file: UploadFile = File(...),
):
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Format non supporté : {ext}")

    meta = load_json(META_FILE)
    
    # Normalisation du nom par rapport aux comptes existants
    users = load_json(USERS_FILE, default={})
    normalized_name = name
    for u_name in users.keys():
        if u_name.lower() == name.lower():
            normalized_name = u_name
            break
    
    # Vérification de la limite de 8 photos par utilisateur
    user_photos = [e for e in meta if e["name"].lower() == normalized_name.lower()]
    if len(user_photos) >= 8:
        raise HTTPException(status_code=400, detail=f"Limite atteinte : Impossible d'ajouter plus de 8 photos pour {normalized_name}.")

    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()
    
    # Anti-doublon (même contenu (hash) et même user)
    for e in meta:
        if e["name"].lower() == normalized_name.lower() and e.get("hash") == file_hash:
            return {"status": "already_exists", "face": e, "msg": "Image identique déjà présente."}
        # Fallback sur original_name si pas de hash
        if e["name"].lower() == normalized_name.lower() and e.get("original_name") == file.filename and "hash" not in e:
            return {"status": "already_exists", "face": e, "msg": "Image avec le même nom déjà présente."}

    face_id = str(uuid.uuid4())
    dest = FACES_DIR / f"{face_id}{ext}"
    
    with open(dest, "wb") as f_out:
        f_out.write(content)

    entry = {
        "id": face_id,
        "name": normalized_name,
        "filename": f"{face_id}{ext}",
        "original_name": file.filename,
        "hash": file_hash,
        "size_bytes": len(content),
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    meta.append(entry)
    save_json(META_FILE, meta)

    return {"status": "ok", "face": entry}

@app.get("/faces", tags=["Faces"], summary="Lister tous les visages", dependencies=[Depends(verify_token)])
def list_faces(name: Optional[str] = Query(None)):
    meta = load_json(META_FILE)
    if name:
        meta = [e for e in meta if name.lower() in e["name"].lower()]
    return {"count": len(meta), "faces": meta}

@app.get("/faces/{face_id}/image", tags=["Faces"], summary="Télécharger l'image", dependencies=[Depends(verify_token)])
def get_face_image(face_id: str):
    entry = find_entry(face_id)
    if not entry: raise HTTPException(status_code=404)
    path = FACES_DIR / entry["filename"]
    if not path.exists(): raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/*", filename=entry["original_name"])

@app.delete("/faces/{face_id}", tags=["Faces"], summary="Supprimer un visage", dependencies=[Depends(verify_token)])
def delete_face(face_id: str):
    meta = load_json(META_FILE)
    entry = next((e for e in meta if e["id"] == face_id), None)
    if not entry: raise HTTPException(status_code=404)

    path = FACES_DIR / entry["filename"]
    if path.exists(): path.unlink()

    meta = [e for e in meta if e["id"] != face_id]
    save_json(META_FILE, meta)
    return {"status": "deleted", "id": face_id}


# ─── MyGES Credentials ────────────────────────────────────────────────────────

@app.post("/myges", tags=["MyGES"], summary="Enregistrer id/mdp MyGES", dependencies=[Depends(verify_token)])
def save_myges(creds: MyGESCredentials, name: str = Query(..., description="Nom de l'utilisateur (ex: Teano)")):
    """Sauvegarde les identifiants MyGES fournis par l'app mobile, lisibles par le robot."""
    all_comptes = load_json(MYGES_FILE, default={})
    all_comptes[name] = {"username": creds.username, "password": creds.password, "updated_at": time.time()}
    save_json(MYGES_FILE, all_comptes)
    return {"status": "saved", "user": name}

@app.get("/myges", tags=["MyGES"], summary="Récupérer id/mdp MyGES", dependencies=[Depends(verify_token)])
def get_myges():
    """Le robot appelle ceci pour récupérer les id/mdp."""
    data = load_json(MYGES_FILE, default={})
    if not data:
        raise HTTPException(status_code=404, detail="No credentials stored")
    return data


# ─── CORE State ───────────────────────────────────────────────────────────────

@app.post("/auth/register", tags=["Auth"], summary="Créer un compte utilisateur (Alias)", dependencies=[Depends(verify_token)])
@app.post("/accounts", tags=["Accounts"], summary="Créer ou MAJ un compte utilisateur", dependencies=[Depends(verify_token)])
def save_account(info: AccountInfo):
    users = load_json(USERS_FILE, default={})
    full_name = f"{info.first_name} {info.last_name}"
    
    existing = users.get(full_name, {})
    dumped_info = info.model_dump()
    
    if info.password:
        dumped_info["password_hash"] = get_password_hash(info.password)
    else:
        dumped_info["password_hash"] = existing.get("password_hash")
        
    if not info.preferences and "preferences" in existing:
        dumped_info["preferences"] = existing["preferences"]
        
    dumped_info.pop("password", None) # Do not save raw password
    
    users[full_name] = dumped_info
    save_json(USERS_FILE, users)
    return {"status": "saved", "user": full_name}

@app.post("/preferences", tags=["Accounts"], summary="MAJ des préférences utilisateur", dependencies=[Depends(verify_token)])
def update_preferences(req: PreferencesUpdate):
    users = load_json(USERS_FILE, default={})
    if req.full_name not in users:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    
    if "preferences" not in users[req.full_name]:
        users[req.full_name]["preferences"] = {}
        
    users[req.full_name]["preferences"].update(req.preferences)
    save_json(USERS_FILE, users)
    return {"status": "updated", "preferences": users[req.full_name]["preferences"]}

@app.post("/auth/login", tags=["Auth"], summary="Connexion utilisateur", dependencies=[Depends(verify_token)])
def login_user(creds: LoginRequest):
    users = load_json(USERS_FILE, default={})
    for name, u in users.items():
        if u.get("email", "").lower() == creds.email.lower():
            if "password_hash" in u and verify_password(creds.password, u["password_hash"]):
                return {"status": "success", "user": u}
            raise HTTPException(status_code=401, detail="Mot de passe incorrect")
    raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

@app.get("/accounts", tags=["Accounts"], summary="Lister les comptes", dependencies=[Depends(verify_token)])
def get_accounts():
    return load_json(USERS_FILE, default={})

@app.post("/core/state", tags=["CORE State"], summary="Mettre à jour l'état du robot", dependencies=[Depends(verify_token)])
def update_state(state: CoreState):
    """Le robot publie son état courant (ce qu'il voit, le chat, etc)."""
    data = state.model_dump()
    data["updated_at"] = time.time()
    save_json(STATE_FILE, data)
    return {"status": "updated"}

@app.get("/core/state", tags=["CORE State"], summary="Récupérer l'état du robot", dependencies=[Depends(verify_token)])
def get_state():
    """L'app mobile appelle ceci pour afficher ce que fait/voit le robot."""
    return load_json(STATE_FILE, default={"robot_status": "offline"})

# ─── System Updates API ───────────────────────────────────────────────────────
GITHUB_RELEASES_CACHE = {} # repo_name -> (tag_name, timestamp)

def get_cached_latest_release(repo: str) -> str:
    now = time.time()
    if repo in GITHUB_RELEASES_CACHE:
        tag, cached_time = GITHUB_RELEASES_CACHE[repo]
        if now - cached_time < 300: # 5 minutes cache
            return tag
            
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = requests.get(url, timeout=3, headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            tag = resp.json().get("tag_name", "v0.0.0")
            GITHUB_RELEASES_CACHE[repo] = (tag, now)
            return tag
    except Exception as e:
        print(f"Error fetching latest release for {repo}: {e}")
        
    if repo in GITHUB_RELEASES_CACHE:
        return GITHUB_RELEASES_CACHE[repo][0]
    return "v0.0.0"

GATEWAY_UPDATE_FILE = DATA_DIR / "gateway_update_state.json"
ROBOT_UPDATE_FILE = DATA_DIR / "robot_update_state.json"

@app.post("/system/update/gateway", tags=["System Update"], summary="Lancer la mise à jour de la Gateway", dependencies=[Depends(verify_token)])
async def trigger_gateway_update():
    """Lancer instantanément la mise à jour de la Gateway."""
    def run_up():
        try:
            from updater import check_and_apply_update
            # Reset progress
            save_json(GATEWAY_UPDATE_FILE, {"status": "starting", "percent": 0})
            updated = check_and_apply_update()
            if updated:
                import os, signal
                save_json(GATEWAY_UPDATE_FILE, {"status": "done", "percent": 100})
                os.kill(os.getpid(), signal.SIGTERM)
            else:
                save_json(GATEWAY_UPDATE_FILE, {"status": "idle", "percent": 100})
        except Exception as e:
            save_json(GATEWAY_UPDATE_FILE, {"status": f"failed: {e}", "percent": 0})
            
    threading.Thread(target=run_up, daemon=True).start()
    return {"status": "triggered"}

@app.get("/system/update/gateway/progress", tags=["System Update"], summary="Récupérer le progrès de mise à jour Gateway", dependencies=[Depends(verify_token)])
def get_gateway_update_progress():
    progress = load_json(GATEWAY_UPDATE_FILE, default={"status": "idle", "percent": 100})
    from updater import get_current_version
    progress["current_version"] = get_current_version()
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE-Gateway")
    return progress

@app.post("/system/update/gateway/progress", tags=["System Update"], summary="Mettre à jour le progrès Gateway", dependencies=[Depends(verify_token)])
async def update_gateway_progress(progress: UpdateProgress):
    data = progress.model_dump()
    save_json(GATEWAY_UPDATE_FILE, data)
    await manager.broadcast(json.dumps({"type": "gateway_update_progress", **data}), "app")
    return {"status": "ok"}

@app.post("/system/update/robot", tags=["System Update"], summary="Lancer la mise à jour du robot", dependencies=[Depends(verify_token)])
async def trigger_robot_update():
    """Lancer instantanément la mise à jour du robot."""
    save_json(ROBOT_UPDATE_FILE, {"status": "starting", "percent": 0})
    await manager.broadcast(json.dumps({"type": "trigger_update"}), "robot")
    await manager.broadcast(json.dumps({"type": "robot_update_progress", "status": "starting", "percent": 0}), "app")
    return {"status": "triggered"}

@app.post("/system/update/robot/progress", tags=["System Update"], summary="Mettre à jour le progrès du robot", dependencies=[Depends(verify_token)])
async def update_robot_progress(progress: UpdateProgress):
    data = progress.model_dump()
    save_json(ROBOT_UPDATE_FILE, data)
    await manager.broadcast(json.dumps({"type": "robot_update_progress", **data}), "app")
    return {"status": "ok"}

@app.get("/system/update/robot/progress", tags=["System Update"], summary="Récupérer le progrès de mise à jour robot", dependencies=[Depends(verify_token)])
def get_robot_update_progress():
    progress = load_json(ROBOT_UPDATE_FILE, default={"status": "idle", "percent": 100})
    state = load_json(STATE_FILE, default={})
    progress["current_version"] = state.get("robot_version", "v0.0.0")
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE")
    return progress

# ─── System ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok", "https": True}
