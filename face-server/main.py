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
latest_diagnostics = {}


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
    arduino_version: Optional[str] = "v0.0.0"
    sensors: dict = {}
    ai_state: dict = {}

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
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[load_json] Error loading {path}: {e}")
    return default if default is not None else []

def save_json(path: Path, data):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        import os
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"[save_json] Error saving {path}: {e}")

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

stream_active = {
    1: False,
    2: False
}

stream_v_slam = {
    1: False,
    2: False
}

stream_keep_alive = {
    1: False,
    2: False
}

camera_stop_timers = {
    1: None,
    2: None
}

async def stop_camera_delayed(cam_id: int):
    await asyncio.sleep(30)
    if stream_keep_alive[cam_id]:
        print(f"[Gateway] Camera {cam_id} stop bypassed because Keep Stream is active.")
        return
    if len(active_camera_listeners[cam_id]) == 0:
        stream_active[cam_id] = False
        await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": False}), "app")
        print(f"[Gateway] Camera {cam_id} stopped after 30 seconds of inactivity.")

def cleanup_camera_listeners(websocket: WebSocket):
    import asyncio
    for cam_id in [1, 2]:
        if websocket in active_camera_listeners[cam_id]:
            active_camera_listeners[cam_id].remove(websocket)
            if len(active_camera_listeners[cam_id]) == 0:
                if camera_stop_timers[cam_id] is not None:
                    camera_stop_timers[cam_id].cancel()
                camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id))

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
    for cam_id in [1, 2]:
        if stream_active[cam_id]:
            try:
                await websocket.send_json({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]})
            except Exception:
                pass
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
                
            try:
                msg_json = json.loads(data)
                if msg_json.get("type") == "telemetry_diagnostics":
                    global latest_diagnostics
                    latest_diagnostics = msg_json
            except Exception:
                pass
            
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
                    v_slam = msg_json.get("v_slam", False)
                    active_camera_listeners[cam_id].add(websocket)
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
                    stream_v_slam[cam_id] = v_slam
                    if not stream_active[cam_id] or v_slam_changed:
                        stream_active[cam_id] = True
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id))
                elif msg_type == "toggle_keep_stream":
                    cam_id = msg_json.get("camera", 1)
                    keep = msg_json.get("keep", False)
                    stream_keep_alive[cam_id] = keep
                    if keep:
                        stream_active[cam_id] = True
                        if camera_stop_timers[cam_id] is not None:
                            camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = None
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    else:
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id))
                    await manager.broadcast(json.dumps({"type": "keep_stream_status", "camera": cam_id, "keep": keep}), "app")
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
    # Envoi de l'état initial des caméras au client qui se connecte
    for cam_id in [1, 2]:
        is_active = len(active_camera_listeners[cam_id]) > 0 or stream_active[cam_id]
        await websocket.send_json({"type": "stream_status", "camera": cam_id, "active": is_active})
        await websocket.send_json({"type": "keep_stream_status", "camera": cam_id, "keep": stream_keep_alive[cam_id]})
    try:
        while True:
            data = await websocket.receive_text()
            
            # Intercepter les requêtes de caméra de l'App/Site
            try:
                msg_json = json.loads(data)
                msg_type = msg_json.get("type")
                if msg_type == "request_camera":
                    cam_id = msg_json.get("camera", 1)
                    v_slam = msg_json.get("v_slam", False)
                    active_camera_listeners[cam_id].add(websocket)
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
                    stream_v_slam[cam_id] = v_slam
                    if not stream_active[cam_id] or v_slam_changed:
                        stream_active[cam_id] = True
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id))
                elif msg_type == "toggle_keep_stream":
                    cam_id = msg_json.get("camera", 1)
                    keep = msg_json.get("keep", False)
                    stream_keep_alive[cam_id] = keep
                    if keep:
                        stream_active[cam_id] = True
                        if camera_stop_timers[cam_id] is not None:
                            camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = None
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    else:
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id))
                    await manager.broadcast(json.dumps({"type": "keep_stream_status", "camera": cam_id, "keep": keep}), "app")
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


CALIBRATION_FILE = DATA_DIR / "calibration.json"

@app.post("/core/calibration", tags=["CORE State"], summary="Sauvegarder les offsets de calibration", dependencies=[Depends(verify_token)])
def save_calibration(data: dict):
    save_json(CALIBRATION_FILE, data)
    return {"status": "saved"}

@app.get("/core/calibration", tags=["CORE State"], summary="Récupérer les offsets de calibration", dependencies=[Depends(verify_token)])
def get_calibration():
    return load_json(CALIBRATION_FILE, default={"offsets": [0]*12})

@app.get("/core/diagnostics", tags=["CORE State"], summary="Récupérer les diagnostics temps-réel", dependencies=[Depends(verify_token)])
def get_diagnostics():
    return latest_diagnostics

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
    <link href="https://fonts.googleapis.com/css2?family=Raleway:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest/dist/hls.min.js"></script>
    <style>
        :root {
            --bg-main: #1a1a1a;
            --bg-card: #242424;
            --border-color: #333333;
            --text-primary: #f0f0f0;
            --text-secondary: #a0a0a0;
            --accent: #FF6F61;
            --accent-hover: #e5634f;
            --success: #48D1CC;
            --danger: #e11d48;
            --warning: #f59e0b;
            --glass: rgba(26, 26, 26, 0.85);
            --brand-gradient: linear-gradient(135deg, #FF6F61, #48D1CC);
        }

        :root[data-theme="light"] {
            --bg-main: #F5F5F0;
            --bg-card: #ffffff;
            --border-color: #d0d0c8;
            --text-primary: #333333;
            --text-secondary: #666666;
            --accent: #FF6F61;
            --accent-hover: #e5634f;
            --success: #48D1CC;
            --danger: #e11d48;
            --warning: #d4920a;
            --glass: rgba(245, 245, 240, 0.9);
            --brand-gradient: linear-gradient(135deg, #FF6F61, #48D1CC);
        }

        :root[data-theme="light"] .card:hover,
        :root[data-theme="light"] .user-card:hover {
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.08);
        }

        :root[data-theme="light"] .stream-placeholder {
            background-color: #e8e8e0;
        }

        :root[data-theme="light"] .stream-controls {
            background-color: #f0f0ea;
        }

        :root[data-theme="light"] .auth-card {
            box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
        }

        :root[data-theme="light"] .spinner {
            border-color: rgba(0, 0, 0, 0.1);
        }

        :root[data-theme="light"] .modal-overlay {
            background-color: rgba(0, 0, 0, 0.3);
        }

        :root[data-theme="light"] .folder-avatar-badge {
            background: var(--border-color);
            box-shadow: 0 2px 5px rgba(0,0,0,0.15);
        }

        :root[data-theme="light"] .face-img-card {
            background-color: #e8e8e0;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Raleway', system-ui, -apple-system, sans-serif;
        }

        body {
            background-color: var(--bg-main);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            overflow: hidden;
            transition: background-color 0.3s ease, color 0.3s ease;
        }

        h1, h2, h3, h4, .font-outfit {
            font-family: 'Raleway', sans-serif;
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
            background: var(--brand-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand-logo {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            object-fit: contain;
        }

        .theme-toggle {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            width: 36px;
            height: 36px;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
            flex-shrink: 0;
        }

        .theme-toggle:hover {
            background-color: var(--border-color);
            border-color: var(--accent);
        }

        .theme-toggle svg {
            width: 18px;
            height: 18px;
            fill: none;
            stroke: currentColor;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
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
            background-color: rgba(255, 111, 97, 0.1);
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
            stroke: var(--border-color);
            stroke-width: 3;
        }

        .circle {
            fill: none;
            stroke-width: 3;
            stroke-linecap: round;
            transition: stroke-dasharray 0.3s ease;
        }

        .cpu-gauge .circle { stroke: var(--accent); }
        .ram-gauge .circle { stroke: var(--success); }
        .temp-gauge .circle { stroke: #f59e0b; }

        .gauge-value {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 1rem;
            font-weight: 700;
            font-family: 'Raleway', sans-serif;
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
            background-color: var(--bg-main);
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

        .video-container {
            width: 100%;
            height: 280px;
            background-color: #000;
            position: relative;
            display: none;
            overflow: hidden;
            border-radius: 8px;
        }

        .video-container:fullscreen {
            width: 100vw;
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background-color: #000;
        }

        .video-container:fullscreen .stream-video {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }

        .stream-video {
            width: 100%;
            height: 100%;
            object-fit: contain;
            display: block;
        }

        .stream-loader {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            background-color: var(--glass);
            color: var(--text-primary);
            gap: 12px;
            z-index: 10;
        }

        .spinner {
            width: 36px;
            height: 36px;
            border: 3px solid rgba(255, 255, 255, 0.1);
            border-radius: 50%;
            border-top-color: var(--accent);
            animation: spin 1s ease-in-out infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        .video-fs-btn {
            position: absolute;
            bottom: 12px;
            right: 12px;
            width: 36px;
            height: 36px;
            background-color: var(--glass);
            backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            color: var(--text-primary);
            display: none;
            justify-content: center;
            align-items: center;
            cursor: pointer;
            z-index: 12;
            transition: all 0.2s ease;
            opacity: 0.8;
        }

        .video-fs-btn:hover {
            opacity: 1;
            transform: scale(1.05);
            background-color: var(--accent);
            border-color: var(--accent);
        }

        .video-fs-btn svg {
            width: 18px;
            height: 18px;
            fill: none;
            stroke: currentColor;
            stroke-width: 2;
            stroke-linecap: round;
            stroke-linejoin: round;
        }

        .stream-controls {
            padding: 1rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background-color: var(--bg-card);
            border-top: 1px solid var(--border-color);
        }

        .status-badge {
            font-size: 0.75rem;
            padding: 0.25rem 0.6rem;
            border-radius: 9999px;
            font-weight: 600;
            background-color: var(--border-color);
            color: var(--text-secondary);
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
        }

        .status-badge.active, .status-badge.online {
            background-color: rgba(72, 209, 204, 0.15);
            color: var(--success);
        }

        .status-badge.error {
            background-color: rgba(239, 68, 68, 0.15);
            color: var(--danger);
        }

        .status-badge.hibernating {
            background-color: rgba(245, 158, 11, 0.15);
            color: var(--warning);
        }

        .status-badge.offline {
            background-color: rgba(225, 29, 72, 0.15);
            color: var(--danger);
        }

        .btn-keep {
            background-color: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            font-size: 0.8rem;
            padding: 0.35rem 0.65rem;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s ease;
            font-weight: 500;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            user-select: none;
        }

        .btn-keep:hover {
            border-color: var(--text-secondary);
            color: var(--text-primary);
        }

        .btn-keep.active {
            background-color: rgba(72, 209, 204, 0.15);
            border-color: var(--success);
            color: var(--success);
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
            background: var(--brand-gradient);
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: 'Raleway', sans-serif;
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
            background-color: rgba(255, 111, 97, 0.15);
            color: var(--accent);
            border: 1px solid rgba(255, 111, 97, 0.3);
            font-size: 0.7rem;
            padding: 0.2rem 0.5rem;
            border-radius: 9999px;
            font-weight: 600;
        }

        .user-badge.admin {
            background-color: rgba(72, 209, 204, 0.15);
            color: var(--success);
            border: 1px solid rgba(72, 209, 204, 0.3);
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
            border: 1px solid var(--border-color);
        }

        .btn-secondary:hover {
            background-color: var(--border-color);
        }

        .btn-danger {
            background-color: rgba(225, 29, 72, 0.1);
            color: var(--danger);
            border: 1px solid rgba(225, 29, 72, 0.2);
        }

        .btn-danger:hover {
            background-color: var(--danger);
            color: white;
        }

        .btn-success {
            background-color: rgba(72, 209, 204, 0.1);
            color: var(--success);
            border: 1px solid rgba(72, 209, 204, 0.2);
        }

        .btn-success:hover {
            background-color: var(--success);
            color: var(--bg-main);
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
            box-shadow: 0 0 0 2px rgba(255, 111, 97, 0.2);
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
            background-color: var(--bg-main);
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
            background-color: var(--glass);
            margin-bottom: 2rem;
        }

        .upload-box:hover {
            border-color: var(--accent);
            background-color: rgba(255, 111, 97, 0.05);
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
        
        #chat-messages-box::-webkit-scrollbar, #json-traffic-console::-webkit-scrollbar, #chat-tab-messages::-webkit-scrollbar {
            width: 4px;
        }
        #chat-messages-box::-webkit-scrollbar-track, #json-traffic-console::-webkit-scrollbar-track, #chat-tab-messages::-webkit-scrollbar-track {
            background: transparent;
        }
        #chat-messages-box::-webkit-scrollbar-thumb, #json-traffic-console::-webkit-scrollbar-thumb, #chat-tab-messages::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 999px;
        }

        /* Dossiers Galerie Style */
        .folders-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .folder-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            align-items: center;
            cursor: pointer;
            position: relative;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            overflow: hidden;
        }

        .folder-card:hover {
            transform: translateY(-4px);
            border-color: var(--accent);
            box-shadow: 0 12px 24px rgba(255, 111, 97, 0.15);
        }

        .folder-icon-wrapper {
            position: relative;
            margin-bottom: 1rem;
            color: var(--accent);
            transition: transform 0.3s ease;
        }

        .folder-card:hover .folder-icon-wrapper {
            transform: scale(1.08);
        }

        .folder-avatar-badge {
            position: absolute;
            bottom: -2px;
            right: -2px;
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: var(--border-color);
            color: var(--text-primary);
            font-size: 0.7rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 2px solid var(--bg-card);
            box-shadow: 0 2px 5px rgba(0,0,0,0.5);
            font-family: 'Raleway', sans-serif;
        }

        .folder-name {
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 0.25rem;
            text-align: center;
            width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .folder-count {
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-weight: 500;
        }

        .folder-open-view {
            display: none;
            animation: slideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .folder-open-view.active {
            display: block;
        }

        .back-btn-wrapper {
            margin-bottom: 1.5rem;
        }

        @keyframes slideIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Custom calibration & control CSS */
        .joint-group-card {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1rem;
        }

        .btn-secondary.active-control {
            background-color: var(--accent) !important;
            color: white !important;
            border-color: var(--accent) !important;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @keyframes scanLine {
            0% { top: 10%; }
            50% { top: 90%; }
            100% { top: 10%; }
        }

        @keyframes pulse {
            0%, 100% { opacity: 0.5; }
            50% { opacity: 0.9; }
        }

        @keyframes scaleUp {
            from { transform: scale(0.8); opacity: 0; }
            to { transform: scale(1); opacity: 1; }
        }

        /* ─── RESPONSIVE DESIGN ─────────────────────────────────────────────── */
        
        .hamburger-btn {
            display: none;
            background: none;
            border: none;
            color: var(--text-primary);
            cursor: pointer;
            padding: 0.5rem;
            align-items: center;
            justify-content: center;
            border-radius: 8px;
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
        }

        .hamburger-btn:hover {
            background-color: var(--border-color);
        }

        .mobile-header-bar {
            display: none;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 1.5rem;
            background-color: var(--bg-card);
            border-bottom: 1px solid var(--border-color);
            position: sticky;
            top: 0;
            z-index: 50;
        }

        .sidebar-overlay {
            display: none;
            position: fixed;
            inset: 0;
            background-color: rgba(0, 0, 0, 0.4);
            backdrop-filter: blur(2px);
            z-index: 99;
            opacity: 0;
            transition: opacity 0.3s ease;
            pointer-events: none;
        }

        .sidebar-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        @media (max-width: 1024px) {
            body {
                flex-direction: column;
                overflow: auto;
            }

            .sidebar {
                position: fixed;
                top: 0;
                left: -280px;
                z-index: 100;
                transition: left 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                box-shadow: 10px 0 30px rgba(0, 0, 0, 0.5);
            }

            .sidebar.active {
                left: 0;
            }

            .sidebar-overlay {
                display: block;
            }

            .mobile-header-bar {
                display: flex;
            }

            .content-wrapper {
                padding: 1.5rem 1rem;
                height: auto;
                min-height: calc(100vh - 65px);
                overflow-y: visible;
            }

            .header-bar {
                margin-bottom: 1.5rem;
                flex-direction: column;
                align-items: flex-start;
                gap: 1rem;
            }

            .header-title {
                font-size: 1.75rem;
            }

            .hamburger-btn {
                display: flex;
            }
        }

        @media (max-width: 768px) {
            .form-row-layout {
                grid-template-columns: 1fr;
                gap: 0.75rem;
            }

            .gauge-container {
                flex-wrap: wrap;
                justify-content: center;
                gap: 1.5rem;
            }

            .gauge-item {
                flex: none;
                width: 80px;
            }
            
            .card-grid {
                grid-template-columns: 1fr;
            }

            .stream-grid {
                grid-template-columns: 1fr;
            }
            
            .user-grid {
                grid-template-columns: 1fr;
            }

            .folders-grid {
                grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
                gap: 1rem;
            }

            .faces-grid {
                grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
                gap: 0.75rem;
            }
        }
    </style>
</head>
<body>
    <!-- Mobile Sidebar Overlay -->
    <div class="sidebar-overlay" onclick="closeSidebar()"></div>

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
            <img src="data:image/webp;base64,UklGRsynAABXRUJQVlA4WAoAAAAQAAAA/QoA/wUAQUxQSJVMAAAB/4WobRvN4c96d/c6ABGR45fc2SCEiIhEkihJklJKKaVSpdWhJ0lt2zaM9f/j6WWLiAlgskIvC4RSfkzFJfvf/v99k/7/7o9Hkg7aAm2pICCiKCoOHE99ouJ4ufd+invvvXHrU58uRNzsPWRDd5PH4367Xv64hNBmtekP91dE/ycAbdu26f+/F/IURvR/Ah7/8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//B/ZnAr7iZd/88Pf9oV6nHPYIlod45ebdexjw+A1RcFf2/Kd+HkMG4Be4wO5Ltx4cASEw+DQO6Irbz9wuEyc0GD7DhXLHp93w9ZgQ5co+i8K4cjd9/ttxgFSyE3nU4wK4Zzw9AngzAYgy/dsucDvuPu/ZIQAPIMzAl7F3dtBW5uyHPjqAEEKY5MFEuS9F4VodD285NCJKmwEIM4/KGOh1QdqZlsVfexAGBiYQXoBAVsbXUXhW6zlXPvLrGJ6JNcDQXBeY3bZi9e+jpIbAT4ThQXweBWW1/N9BihPAg7DxEwiGz3Kh2FHHotu2eAOPF6kH/ISgoh+zgVitlz2/uZBiIKO0QEygMIyVLgQ7e8W6/aMY4AFkeEwCbALA4Fhr8FWUPfXFVB4hMCEQGIAZE5pC+rALvG476/49hgECAcaJDQQ2AYiBlqCrzMKX/+ynmu1lF3AdnXHAqGYPA73hVm0XbU2AQhXh9U0caBX1PPyHDEOoegwuc2HWcz8/4gUIEqr5UC7EKrN0d0oKKeCpavuPC66OZl3/g4cED0rBqkn52aFVUd9721I8yJAHhFURv7rA6vZfUmFUveERGLcFVcXdDyQYoiZ6AwYXhVTNf/MYXoBqgAEy25QLp+r45qg84KmJAgm9H4VStTyWJgiBUqz6hBDYv10Y9awH91PaAKMWAEqS9iCq3GMb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Ld/2heA3+b5xfxOuG0Bf6N/p+WFqI/uB+9Xtx+vr96P//7u4G9EAru19Pcae801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpvxp0ztNfwqfTipdCdYEHYlMCAe801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+erj2tihYmfFGsf+lFYrOqk53Rsfw0Pcae801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHVVmAadaI0yQ6HhCKBEN5JsS/ZVDeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHVVmDTAgGhyXdEcvhrEoQGMxP8H0hGEnTZCsn4n1YFh7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b6eH5FxsIFBAixR/FdBUXXcmPUk5HKWBFGsu437C0kHee8uCYSa0Zz6C+oduKwf14kyD3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHMwYUi+SQQ8+c/i8vDKtHmo2J/yWNE3mZWa0BBOO8OXLo/gajWuzwe1WyGJMg95prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FW/acZ1FJslgZT3enUSYjI8BcafaACAycj0/EELA6qzEyRdGuEtp9B6/O/z4gyQCq0oQIPa+nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dN9JNLB4WHj7GCdl85l/aNLMJIw3AlLLUsrP+9qReWisZHx6v+0FbLLrbMasds7iaonJdZOlD2ms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dN9UZFBMADFf7roKBBG1Ol13pfltrRm7i0Qh1RZvwpBzaAC/Y5DPxZD7wT/wbLP8oz506dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp0dFgYqeYhbLQAtGGZilm/MwvIAFLMrDRoA2XOnKkYMZuLZzY56EIy1l5Eh/46OHiOajx5nRpsrmms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FWj4tp5x52YEA95pp6BLnwDcY46whQZOGUsUAs3WMd304DJp8nn9FYnAc2PwAC51eO0ndqf4eY1X09xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5aww37/SAwC8nI4nS6+nD+uLzTWb6vGDrxQV1QQyTRQlTodAXOivdRaHnOE6MBB3TOxkUAOvg1umR23SCP9OnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHVp1d/M0BUXX08pesYi1MSBlBYHUdEYGm5MwwyOesremBAPeTVR4Hawl8UAthbHzVkH9Csz1slFMYFVRjbUPANjnEVKdIZu6Z8BbOWbxSF+y4u/7Mpaw1eqXc+1UwIB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnRzKfP0cb0IwznLJhiGubdIusZ+UJ2b6V2DDZAlloqD2azeBQLXbsTAAnTQEXHEDwjAWYEA95M3F8jKHCIE+VegLWSjinseXK3yORP/2UyiHUuSQYHgWIkHfz8+EHvSz0HLjcLbl2/xB8apH4Oy7L8sJvheaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHKAWRDb1ycCcIQzQVerSobHWHMfe2xX0PKa7nV9yu7Jx/b5hiVglvvLDjpK1HFPcK/DJAiXwkIUZ2HuNPdKwHnkPH1mnlBr1ipjb4mtSJ2gTzMQ+5EdN+jEgjDU8I9h6IskmPqC+uhbOwjfOnIx8rFM0lSDXTZDBWhTDIUJbDktg5pVEPcN5mHuNPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo7VAvPubsXf8ZYwIswJ5swja2iTbvjoYqAWA7lcDOLLQIDh6sWFqbJKnX90Qz7ACC64DYP5w36dU801nR2Fbw7qAgX14oHdhvq1WnNPg7/8pZkehjPEi0DxKR66Mo4FP9AluC/78qFotObDA+0Jb/ImUA95prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTfUPKhDqNYxw9oEJfDSt/jWpu0jxZiS0JsYn9WB+AqYZJmTCsyaibYEcyo6mI1tlD9BCcvgadr/fcae6RKoul8FLzuwBKMz5b2ADHVFoaFdMTEA7g6c3qZMdqHpCb/8Yp9Wmoqvp7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+kyOq9CheFU351HLZlAu0eb9RGL+qy7UUrk66LgVSXyj/YKLIkl8sm0aID0SVlk/3IcHMxFp0ae5i0f4If4ZUQ6veyw+nR2yX1GEUr587rwsti3+x8sgQ6lXAQnw2adX3nM6YafzoMTs4ndnnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo6wLV/6+OmIL0x/dpvEt4/82+rVT/jmmX88htq56vSz4SC9oa0rSQL1DrTH+/RP4beOApYS+DxJQ9P+rJziw885NwSO9LCw+nTo7ZLZU8l9RtCHprOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnRzbxZBGwfGfyzKWF1/7TUZsLUIezovV+b4RCjkDKuuXsThJywX/MluU06+8oN5Gak6KQ340I7HerKYVVcrV1p3agPYCUX1Fej1sILMCAe801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dN8SGnvoOFc+St//BdSE//quDEAvrLhOX+n+nKMaWj6rDINVV7L9EL1XfEvdkeF2Aj/+UVbBTNkflRKJUjMjnsBJuRlPHI3Wd6Vm9cQAVp4Peaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FWjzDftf0n4V+VWLmHX63cdAPCG5zH5A4X2XqPCMsOvSO6rM6dvpARRSXrzWEqO1RY/62q52iin4fSdOQ0vV9vmNhG9xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FWjzN3LqTFfMp9mftaCm6DWtFyEqFo1gZMt0QslyPBIcSbiXeV3t9ZbqQlNt1qrXpWKSFBDNBmXjBriqGrrp24/DzmnuNPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHCrWu5oW0G5TtawwlcPq0kD6QJinke4imHnuP2+mPAjQubbQaSGqEdtHi5yV1gKfJkGgNRyudzT9cXhxvSbRH+nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b6G7l/lYuILr/5b1IboaZNStzP6a0FO1a5xMyER40eYNOr1oCUZJ+FAPKBEf/xLqFePXn+Vaprooe01nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpvhVksOyQcdZfFehp63ii7PtPSvEdpBcXRId3XV/mBAPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5tDoW+/2ppSgwxQsoHy24qUTI885aal0PWGkK+kfmUNGZ4OnrQDdh7/KAN4Vxm3uHF5prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnRy4wQjIZU9iVvW1LWNlhKvBoJXH5HYb0sQJwJXf1p8XllCPfnS+cNKY0fiEEGcoXVStGSmFfOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp035itDiy8xVE0rFSft/jbCBSEDslbmP0fCpPSpALnAK1cQ9ZMJefTOGMreJiIPPY8WoIWnmBAPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+l9CjGaFTV0NnA0KkFAg9WABg0xzomhIy83vOZWpGRVmfrAmb5h8t5u/SvicJr9O5rdyoEgcavJB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06OGn3UZ7jLQM7WKwXRlVERDtNRcxYro9+pZVRwF1tfhqWj0wIB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpviIthDjtER5nxyEjnWsV0rUXooQfTo07xfgd7pFB8f7Zdxhp2OXnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5fxJEiADToQfzOQG0JrqkCSSZ+ishM3VVxun+bsAaWdyHAAVgzoJPpRWbfDzmnuNPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+KlFqz/A1ZgEAEs7iCHV1HS09rYKOFj0e52k5J0jd6WRM3ZL0XMMnRuNMwIB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTfE7ve3rC70uA0dTTUlhbxhS0bMGb+Q5zyXWjDPGIG/kTwSvZNNeQd0WccGcb53wmVApoe4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5EGmciTDqa857ZgEowVb8mqwXmMHlkdNwE6518qFNJqcDEC7S4Cq/lZzllsltooxdh7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+s4pmv9ogRiczuWXxnQAUk3mlzjyfpb49RyLSQ9ShbgSvS0ygIJKw5tbc6hlmBAPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHaoOmNFwetbteghC0Kynn7JjZm8oC8L3W/7MKHV/6Vl3JSj+q+e8K+7/4OuAdlDFC+scodvBX9cO9Oh0DKDOwZWz+eRlNIJy4i801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/ahPasVeUwdL0TvciUlDgRWQAwF0/ggHjLa933nsH+87mHGRUeKP5S6lgBD3u2ljDmN5bIBNgk0OAelJ/3+InM5ewMErPp5ODNi+r/HB4tgB3mhUVX09xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHaoFTeJCe7TIrRSfd2kDsoorVY5JGNcCOr+YMy96IOYC8tvpVK16bKXH7vdNKZTIWVNk5TPF7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo7ZKCiEPwz08GgrMFXpFc4t+No+f4DjOGPVR+vVHAFyWIziqFlwVMrcQySwJUFCycUqQcTdnRWmf+Q1tPEmQe801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnR2qNnb3B5aGDXUz8tn4uRVIG40TEGs8EvvW5bvQiOowMs8soB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpvpvZZO/+wjsRA78evy1ACDh/7jn4v3Y4e4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b6vLX3dhDOp+xTdt6n0zAmrHdNID7x6HuII/06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHRa5Quyq/3Dm4HYDt/hnURge01nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp03zGDdZNbMXMOMDQn0AB126oVvVA/dCfh6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp0cNPplVKmIo7SyiPgzuJ0C8f/tDNTD2ms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOjriri4swdYyshk82FGOB4zvpVotuFAA4w+lOsKW16e4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHL/68T8o30hdanKBrNdNdRhLwP63zLbOv78+63Qbxdc9aq3e3qKr6e4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp03xMKSRWKoI3Ltqw92xEFoS8TNqHfAFB/G/5L7eoqvp7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp0czaivNXiCrPqOBlyBeg+zZg+Z06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnR1VaQE5ggTgAjeWxxMxDolbmwCP9OnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTftQnqrNXFg23ECFSIRDwclrOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b4TKlMGyJmfi9xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOjtnFHtNZ06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06OAAD803CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABZn8M//utdI7q5HNR3jdVNGOfw3Ha/1xqewdkDZfUSLDmfwHU12oBEyduekc7t+eUTvqP9EraaQ2p9E7rECshhtNw3vnTCOcxvjTB+CWBOs1M5pbKOsmEQwfFI2f+LEgAAAACqO6RguGPPnNTvnRxZynwZhaGFxhjnzrx6d32tSzxj6GrV6oAo8wRsUizGF7jV/CowHNNeZVdy6t73oGO5DSP8w/rmGNK6WaH6LBqgkQYcnIPfW9tnP5347L5Pav8lkvZuGAl6Ew4K3KQ19MOKa0r8bzJS2IWlWaTnMWaEkSguMJdgAAAAAKcLQjgv0jWw1GheDm3OCMc3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" alt="Bastet" class="brand-logo"/>
            <span class="brand-name font-outfit">BASTET GATEWAY</span>
            <button class="theme-toggle" onclick="toggleTheme()" title="Basculer le thème">
                <svg id="theme-icon-dark" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
                <svg id="theme-icon-light" viewBox="0 0 24 24" style="display:none;"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            </button>
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
            <li class="nav-item" onclick="switchTab('chat')" id="nav-chat">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                <span>Chat & Contrôle IA</span>
            </li>
            <li class="nav-item" onclick="switchTab('diagnostics')" id="nav-diagnostics">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
                </svg>
                <span>Arduino & Calib</span>
            </li>
            <li class="nav-item" onclick="switchTab('map')" id="nav-map">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6"/><line x1="8" y1="2" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="22"/>
                </svg>
                <span>SLAM & Map</span>
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

    <!-- Mobile Header Bar -->
    <div class="mobile-header-bar">
        <button class="hamburger-btn" onclick="toggleSidebar()">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
            </svg>
        </button>
                <div class="mobile-brand" style="display: flex; align-items: center; gap: 0.5rem;">
            <img src="data:image/webp;base64,UklGRsynAABXRUJQVlA4WAoAAAAQAAAA/QoA/wUAQUxQSJVMAAAB/4WobRvN4c96d/c6ABGR45fc2SCEiIhEkihJklJKKaVSpdWhJ0lt2zaM9f/j6WWLiAlgskIvC4RSfkzFJfvf/v99k/7/7o9Hkg7aAm2pICCiKCoOHE99ouJ4ufd+invvvXHrU58uRNzsPWRDd5PH4367Xv64hNBmtekP91dE/ycAbdu26f+/F/IURvR/Ah7/8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//8z//B/ZnAr7iZd/88Pf9oV6nHPYIlod45ebdexjw+A1RcFf2/Kd+HkMG4Be4wO5Ltx4cASEw+DQO6Irbz9wuEyc0GD7DhXLHp93w9ZgQ5co+i8K4cjd9/ttxgFSyE3nU4wK4Zzw9AngzAYgy/dsucDvuPu/ZIQAPIMzAl7F3dtBW5uyHPjqAEEKY5MFEuS9F4VodD285NCJKmwEIM4/KGOh1QdqZlsVfexAGBiYQXoBAVsbXUXhW6zlXPvLrGJ6JNcDQXBeY3bZi9e+jpIbAT4ThQXweBWW1/N9BihPAg7DxEwiGz3Kh2FHHotu2eAOPF6kH/ISgoh+zgVitlz2/uZBiIKO0QEygMIyVLgQ7e8W6/aMY4AFkeEwCbALA4Fhr8FWUPfXFVB4hMCEQGIAZE5pC+rALvG476/49hgECAcaJDQQ2AYiBlqCrzMKX/+ynmu1lF3AdnXHAqGYPA73hVm0XbU2AQhXh9U0caBX1PPyHDEOoegwuc2HWcz8/4gUIEqr5UC7EKrN0d0oKKeCpavuPC66OZl3/g4cED0rBqkn52aFVUd9721I8yJAHhFURv7rA6vZfUmFUveERGLcFVcXdDyQYoiZ6AwYXhVTNf/MYXoBqgAEy25QLp+r45qg84KmJAgm9H4VStTyWJgiBUqz6hBDYv10Y9awH91PaAKMWAEqS9iCq3GMb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Ld/2heA3+b5xfxOuG0Bf6N/p+WFqI/uB+9Xtx+vr96P//7u4G9EAru19Pcae801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpvxp0ztNfwqfTipdCdYEHYlMCAe801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+erj2tihYmfFGsf+lFYrOqk53Rsfw0Pcae801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHVVmAadaI0yQ6HhCKBEN5JsS/ZVDeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHVVmDTAgGhyXdEcvhrEoQGMxP8H0hGEnTZCsn4n1YFh7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b6eH5FxsIFBAixR/FdBUXXcmPUk5HKWBFGsu437C0kHee8uCYSa0Zz6C+oduKwf14kyD3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHMwYUi+SQQ8+c/i8vDKtHmo2J/yWNE3mZWa0BBOO8OXLo/gajWuzwe1WyGJMg95prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FW/acZ1FJslgZT3enUSYjI8BcafaACAycj0/EELA6qzEyRdGuEtp9B6/O/z4gyQCq0oQIPa+nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dN9JNLB4WHj7GCdl85l/aNLMJIw3AlLLUsrP+9qReWisZHx6v+0FbLLrbMasds7iaonJdZOlD2ms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dN9UZFBMADFf7roKBBG1Ol13pfltrRm7i0Qh1RZvwpBzaAC/Y5DPxZD7wT/wbLP8oz506dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp0dFgYqeYhbLQAtGGZilm/MwvIAFLMrDRoA2XOnKkYMZuLZzY56EIy1l5Eh/46OHiOajx5nRpsrmms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FWj4tp5x52YEA95pp6BLnwDcY46whQZOGUsUAs3WMd304DJp8nn9FYnAc2PwAC51eO0ndqf4eY1X09xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5aww37/SAwC8nI4nS6+nD+uLzTWb6vGDrxQV1QQyTRQlTodAXOivdRaHnOE6MBB3TOxkUAOvg1umR23SCP9OnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHVp1d/M0BUXX08pesYi1MSBlBYHUdEYGm5MwwyOesremBAPeTVR4Hawl8UAthbHzVkH9Csz1slFMYFVRjbUPANjnEVKdIZu6Z8BbOWbxSF+y4u/7Mpaw1eqXc+1UwIB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnRzKfP0cb0IwznLJhiGubdIusZ+UJ2b6V2DDZAlloqD2azeBQLXbsTAAnTQEXHEDwjAWYEA95M3F8jKHCIE+VegLWSjinseXK3yORP/2UyiHUuSQYHgWIkHfz8+EHvSz0HLjcLbl2/xB8apH4Oy7L8sJvheaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHKAWRDb1ycCcIQzQVerSobHWHMfe2xX0PKa7nV9yu7Jx/b5hiVglvvLDjpK1HFPcK/DJAiXwkIUZ2HuNPdKwHnkPH1mnlBr1ipjb4mtSJ2gTzMQ+5EdN+jEgjDU8I9h6IskmPqC+uhbOwjfOnIx8rFM0lSDXTZDBWhTDIUJbDktg5pVEPcN5mHuNPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo7VAvPubsXf8ZYwIswJ5swja2iTbvjoYqAWA7lcDOLLQIDh6sWFqbJKnX90Qz7ACC64DYP5w36dU801nR2Fbw7qAgX14oHdhvq1WnNPg7/8pZkehjPEi0DxKR66Mo4FP9AluC/78qFotObDA+0Jb/ImUA95prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTfUPKhDqNYxw9oEJfDSt/jWpu0jxZiS0JsYn9WB+AqYZJmTCsyaibYEcyo6mI1tlD9BCcvgadr/fcae6RKoul8FLzuwBKMz5b2ADHVFoaFdMTEA7g6c3qZMdqHpCb/8Yp9Wmoqvp7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+kyOq9CheFU351HLZlAu0eb9RGL+qy7UUrk66LgVSXyj/YKLIkl8sm0aID0SVlk/3IcHMxFp0ae5i0f4If4ZUQ6veyw+nR2yX1GEUr587rwsti3+x8sgQ6lXAQnw2adX3nM6YafzoMTs4ndnnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo6wLV/6+OmIL0x/dpvEt4/82+rVT/jmmX88htq56vSz4SC9oa0rSQL1DrTH+/RP4beOApYS+DxJQ9P+rJziw885NwSO9LCw+nTo7ZLZU8l9RtCHprOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnRzbxZBGwfGfyzKWF1/7TUZsLUIezovV+b4RCjkDKuuXsThJywX/MluU06+8oN5Gak6KQ340I7HerKYVVcrV1p3agPYCUX1Fej1sILMCAe801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dN8SGnvoOFc+St//BdSE//quDEAvrLhOX+n+nKMaWj6rDINVV7L9EL1XfEvdkeF2Aj/+UVbBTNkflRKJUjMjnsBJuRlPHI3Wd6Vm9cQAVp4Peaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FWjzDftf0n4V+VWLmHX63cdAPCG5zH5A4X2XqPCMsOvSO6rM6dvpARRSXrzWEqO1RY/62q52iin4fSdOQ0vV9vmNhG9xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/FWjzN3LqTFfMp9mftaCm6DWtFyEqFo1gZMt0QslyPBIcSbiXeV3t9ZbqQlNt1qrXpWKSFBDNBmXjBriqGrrp24/DzmnuNPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHCrWu5oW0G5TtawwlcPq0kD6QJinke4imHnuP2+mPAjQubbQaSGqEdtHi5yV1gKfJkGgNRyudzT9cXhxvSbRH+nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b6G7l/lYuILr/5b1IboaZNStzP6a0FO1a5xMyER40eYNOr1oCUZJ+FAPKBEf/xLqFePXn+Vaprooe01nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpvhVksOyQcdZfFehp63ii7PtPSvEdpBcXRId3XV/mBAPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5tDoW+/2ppSgwxQsoHy24qUTI885aal0PWGkK+kfmUNGZ4OnrQDdh7/KAN4Vxm3uHF5prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnRy4wQjIZU9iVvW1LWNlhKvBoJXH5HYb0sQJwJXf1p8XllCPfnS+cNKY0fiEEGcoXVStGSmFfOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp035itDiy8xVE0rFSft/jbCBSEDslbmP0fCpPSpALnAK1cQ9ZMJefTOGMreJiIPPY8WoIWnmBAPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+l9CjGaFTV0NnA0KkFAg9WABg0xzomhIy83vOZWpGRVmfrAmb5h8t5u/SvicJr9O5rdyoEgcavJB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06OGn3UZ7jLQM7WKwXRlVERDtNRcxYro9+pZVRwF1tfhqWj0wIB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpviIthDjtER5nxyEjnWsV0rUXooQfTo07xfgd7pFB8f7Zdxhp2OXnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5fxJEiADToQfzOQG0JrqkCSSZ+ishM3VVxun+bsAaWdyHAAVgzoJPpRWbfDzmnuNPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+KlFqz/A1ZgEAEs7iCHV1HS09rYKOFj0e52k5J0jd6WRM3ZL0XMMnRuNMwIB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTfE7ve3rC70uA0dTTUlhbxhS0bMGb+Q5zyXWjDPGIG/kTwSvZNNeQd0WccGcb53wmVApoe4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo5EGmciTDqa857ZgEowVb8mqwXmMHlkdNwE6518qFNJqcDEC7S4Cq/lZzllsltooxdh7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm+s4pmv9ogRiczuWXxnQAUk3mlzjyfpb49RyLSQ9ShbgSvS0ygIJKw5tbc6hlmBAPeaazp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHaoOmNFwetbteghC0Kynn7JjZm8oC8L3W/7MKHV/6Vl3JSj+q+e8K+7/4OuAdlDFC+scodvBX9cO9Oh0DKDOwZWz+eRlNIJy4i801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOm/ahPasVeUwdL0TvciUlDgRWQAwF0/ggHjLa933nsH+87mHGRUeKP5S6lgBD3u2ljDmN5bIBNgk0OAelJ/3+InM5ewMErPp5ODNi+r/HB4tgB3mhUVX09xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHaoFTeJCe7TIrRSfd2kDsoorVY5JGNcCOr+YMy96IOYC8tvpVK16bKXH7vdNKZTIWVNk5TPF7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTo7ZKCiEPwz08GgrMFXpFc4t+No+f4DjOGPVR+vVHAFyWIziqFlwVMrcQySwJUFCycUqQcTdnRWmf+Q1tPEmQe801nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnR2qNnb3B5aGDXUz8tn4uRVIG40TEGs8EvvW5bvQiOowMs8soB7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTpvpvZZO/+wjsRA78evy1ACDh/7jn4v3Y4e4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b6vLX3dhDOp+xTdt6n0zAmrHdNID7x6HuII/06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHRa5Quyq/3Dm4HYDt/hnURge01nTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp03zGDdZNbMXMOMDQn0AB126oVvVA/dCfh6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp0cNPplVKmIo7SyiPgzuJ0C8f/tDNTD2ms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOjriri4swdYyshk82FGOB4zvpVotuFAA4w+lOsKW16e4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dHL/68T8o30hdanKBrNdNdRhLwP63zLbOv78+63Qbxdc9aq3e3qKr6e4095prOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp03xMKSRWKoI3Ltqw92xEFoS8TNqHfAFB/G/5L7eoqvp7jT3mms6dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp0czaivNXiCrPqOBlyBeg+zZg+Z06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnR1VaQE5ggTgAjeWxxMxDolbmwCP9OnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTftQnqrNXFg23ECFSIRDwclrOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06b4TKlMGyJmfi9xp7zTWdOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOjtnFHtNZ06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06dOnTp06OAAD803CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAABZn8M//utdI7q5HNR3jdVNGOfw3Ha/1xqewdkDZfUSLDmfwHU12oBEyduekc7t+eUTvqP9EraaQ2p9E7rECshhtNw3vnTCOcxvjTB+CWBOs1M5pbKOsmEQwfFI2f+LEgAAAACqO6RguGPPnNTvnRxZynwZhaGFxhjnzrx6d32tSzxj6GrV6oAo8wRsUizGF7jV/CowHNNeZVdy6t73oGO5DSP8w/rmGNK6WaH6LBqgkQYcnIPfW9tnP5347L5Pav8lkvZuGAl6Ew4K3KQ19MOKa0r8bzJS2IWlWaTnMWaEkSguMJdgAAAAAKcLQjgv0jWw1GheDm3OCMc3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" alt="Bastet" class="brand-logo" style="width:28px;height:28px;"/>
            <span class="brand-name font-outfit" style="font-size: 1.15rem; font-weight: 700; letter-spacing: 0.05em; background: var(--brand-gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">BASTET GATEWAY</span>
        </div>
        <button class="theme-toggle" onclick="toggleTheme()" title="Basculer le theme">
            <svg id="theme-icon-dark-m" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
            <svg id="theme-icon-light-m" viewBox="0 0 24 24" style="display:none;"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
        </button> <!-- spacer -->
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
                <div id="stream-card-1" class="stream-card">
                    <div id="stream-placeholder-1" class="stream-placeholder" onclick="toggleStream(1, true)">
                        <svg viewBox="0 0 24 24">
                            <polygon points="5 3 19 12 5 21 5 3"/>
                        </svg>
                        <span>Cliquer pour activer Caméra Gauche</span>
                    </div>
                    <div id="video-container-1" class="video-container">
                        <video id="video-cam-1" class="stream-video" autoplay playsinline muted></video>
                        <div id="stream-loader-1" class="stream-loader">
                            <div class="spinner"></div>
                            <span style="font-size: 0.9rem; font-weight: 500;">Connexion au flux WebRTC...</span>
                        </div>
                        <button id="video-fs-btn-1" class="video-fs-btn" onclick="toggleFullscreen(1)" title="Plein écran">
                            <svg viewBox="0 0 24 24">
                                <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                            </svg>
                        </button>
                    </div>
                    <div class="stream-controls">
                        <div style="display: flex; flex-direction: column; gap: 4px;">
                            <h4 style="font-size: 0.95rem; font-weight: 600;">Caméra Gauche</h4>
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span id="stream-status-1" class="status-badge">Inactif</span>
                                <label class="vslam-toggle" style="display: flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--text-secondary); cursor: pointer; user-select: none;">
                                    <input type="checkbox" id="stream-v-slam-1" style="accent-color: var(--accent); width:14px; height:14px;" onchange="handleVSlamToggleChange()">
                                    <span id="vslam-text-mode">Superposer V-SLAM Mono</span>
                                </label>
                            </div>
                        </div>
                        <button id="keep-btn-1" class="btn-keep" style="margin-right: 8px;" onclick="toggleKeepStream(1)" title="Garder le flux actif indéfiniment">📌 Keep Stream : OFF</button><button class="btn btn-secondary" onclick="toggleStream(1, true)">
                            <span id="stream-btn-text-1">Démarrer le flux</span>
                        </button>
                    </div>
                </div>

                <!-- Caméra 2 -->
                <div id="stream-card-2" class="stream-card">
                    <div id="stream-placeholder-2" class="stream-placeholder" onclick="toggleStream(2, true)">
                        <svg viewBox="0 0 24 24">
                            <polygon points="5 3 19 12 5 21 5 3"/>
                        </svg>
                        <span>Cliquer pour activer Caméra Droite</span>
                    </div>
                    <div id="video-container-2" class="video-container">
                        <video id="video-cam-2" class="stream-video" autoplay playsinline muted></video>
                        <div id="stream-loader-2" class="stream-loader">
                            <div class="spinner"></div>
                            <span style="font-size: 0.9rem; font-weight: 500;">Connexion au flux WebRTC...</span>
                        </div>
                        <button id="video-fs-btn-2" class="video-fs-btn" onclick="toggleFullscreen(2)" title="Plein écran">
                            <svg viewBox="0 0 24 24">
                                <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                            </svg>
                        </button>
                    </div>
                    <div class="stream-controls">
                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600;">Caméra Droite</h4>
                            <span id="stream-status-2" class="status-badge">Inactif</span>
                        </div>
                        <button id="keep-btn-2" class="btn-keep" style="margin-right: 8px;" onclick="toggleKeepStream(2)" title="Garder le flux actif indéfiniment">📌 Keep Stream : OFF</button><button class="btn btn-secondary" onclick="toggleStream(2, true)">
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
            <div id="faces-folders-view">
                <div class="folders-grid" id="folders-container">
                    <!-- User folders will be loaded here dynamically -->
                </div>
            </div>

            <div id="faces-details-view" class="folder-open-view">
                <div class="back-btn-wrapper">
                    <button class="btn btn-secondary" onclick="closeFolderDetails()">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 0.25rem;">
                            <line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>
                        </svg>
                        Retour aux dossiers
                    </button>
                </div>
                
                <div class="upload-box" onclick="triggerFaceUpload()">
                    <svg viewBox="0 0 24 24" width="36" height="36" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--accent); margin: 0 auto 0.75rem;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
                    </svg>
                    <h4 style="font-size: 1rem; font-weight: 600; margin-bottom: 0.25rem;">Charger une nouvelle photo pour <span id="current-folder-username-label">l'utilisateur</span></h4>
                    <p style="color: var(--text-secondary); font-size: 0.8rem;">Glissez-déposez ou cliquez ici pour ajouter une image (Max 8 photos)</p>
                    <input type="file" id="face-file-input" style="display: none;" accept="image/*" onchange="handleFaceUploadSelected(event)"/>
                </div>

                <div class="faces-section" style="border: none; padding: 0; background: transparent;">
                    <div class="face-user-header" style="border-bottom: 1px solid var(--border-color); padding-bottom: 0.75rem; margin-bottom: 1.5rem;">
                        <span id="details-folder-name" style="font-size: 1.25rem; font-weight: 700;">Nom</span>
                        <span id="details-folder-count" style="font-size: 0.85rem; color: var(--text-secondary); font-weight: 500;">0 / 8 photos</span>
                    </div>
                    <div class="faces-grid" id="details-faces-grid">
                        <!-- Loaded dynamically -->
                    </div>
                </div>
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
                    <button id="btn-update-gateway" class="btn btn-secondary" onclick="triggerUpdate('gateway')" style="width: 100%; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
                        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                        </svg>
                        <span id="btn-update-gateway-text">Lancer la mise à jour Gateway</span>
                    </button>
                </div>

                <!-- Robot Update Card -->
                <div class="card">
                    <div class="card-title">Mise à jour — Robot Pi & Arduino</div>
                    
                    <div id="update-zone-robot" style="margin: 1rem 0; transition: opacity 0.3s ease;">
                        <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 1rem; color: var(--text-primary);">Système Principal (Pi 5)</h4>
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
                        <button id="btn-update-robot" class="btn btn-secondary" onclick="triggerUpdate('robot')" style="width: 100%; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
                            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                            </svg>
                            <span id="btn-update-robot-text">Lancer la mise à jour Robot</span>
                        </button>
                    </div>

                    <!-- Partie 2 : Arduino Mega -->
                    <div id="update-zone-arduino" style="border-top: 1px solid var(--border-color); padding-top: 1.5rem; transition: opacity 0.3s ease;">
                        <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 1rem; color: var(--text-primary);">Microcontrôleur (Arduino Mega)</h4>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                            <span>Version actuelle flashée :</span>
                            <span id="arduino-current-version" style="font-weight: 600; color: var(--text-primary);">v0.0.0</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.5rem;">
                            <span>Dernière version dispo :</span>
                            <span id="arduino-latest-version" style="font-weight: 600; color: var(--success);">v0.0.0</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--text-secondary);">
                            <span>Statut du flash :</span>
                            <span id="arduino-update-status" style="font-weight: 600; color: var(--text-primary);">Prêt</span>
                        </div>
                        <div class="progress-bar-container">
                            <div id="arduino-update-bar" class="progress-bar-value progress-bar-fill"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; font-weight: 500;">
                            <span>Progression</span>
                            <span id="arduino-update-percent">0%</span>
                        </div>
                        <button id="btn-update-arduino" class="btn btn-secondary" onclick="triggerUpdate('arduino')" style="width: 100%; justify-content: center; gap: 0.5rem; margin-top: 1rem;">
                            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                            </svg>
                            <span id="btn-update-arduino-text">Reflasher l'Arduino</span>
                        </button>
                    </div>
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
                    <button id="btn-start-spotbot" class="btn btn-success" onclick="controlRobotService('start')" style="gap: 0.5rem;">
                        ▶ Démarrer SpotBot
                    </button>
                    <button id="btn-stop-spotbot" class="btn btn-danger" onclick="controlRobotService('stop')" style="gap: 0.5rem;">
                        ■ Arrêter SpotBot
                    </button>
                    <button id="btn-restart-spotbot" class="btn btn-secondary" onclick="controlRobotService('restart')" style="gap: 0.5rem;">
                        🔄 Redémarrer
                    </button>
                </div>
            </div>
        </div>

        <!-- ─────────────────── TAB 5: CHAT & CONTROL IA ─────────────────── -->
        <div id="tab-chat-content" class="tab-content">
            <div class="card-grid">
                <!-- Live Conversation Box -->
                <div class="card" style="display: flex; flex-direction: column; min-height: 450px;">
                    <div class="card-title">
                        <span>Discussion en Direct avec le LLM</span>
                        <span class="status-badge active" id="llm-status-badge">Prêt</span>
                    </div>
                    <div id="chat-tab-messages" style="flex: 1; overflow-y: auto; padding: 1rem; background-color: var(--bg-main); border: 1px solid var(--border-color); border-radius: 8px; margin-bottom: 1rem; display: flex; flex-direction: column; gap: 0.75rem; min-height: 250px; max-height: 300px;">
                        <div style="text-align: center; color: var(--text-secondary); font-size: 0.85rem; padding: 2rem 0;">Aucun message échangé. Saisissez un texte ci-dessous pour démarrer.</div>
                    </div>
                    <form onsubmit="sendChatMessage(event)" style="display: flex; gap: 0.5rem;">
                        <input type="text" id="chat-tab-input" class="form-input" placeholder="Parlez à Bastet..." autocomplete="off"/>
                        <button type="submit" class="btn btn-primary">Envoyer</button>
                    </form>
                </div>

                <!-- AI Modules Control -->
                <div class="card">
                    <div class="card-title">Contrôle des Modules d'IA</div>
                    <div style="display: flex; flex-direction: column; gap: 1.25rem; margin-top: 1rem;">
                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Contrôle de la Parole (TTS)</h4>
                            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary active-control" id="tts-ctrl-robot" onclick="setAIControl('tts', 'robot')">Robot Local</button>
                                <button class="btn btn-secondary" id="tts-ctrl-node" onclick="setAIControl('tts', 'node')">PC Node</button>
                                <button class="btn btn-secondary" id="tts-ctrl-off" onclick="setAIControl('tts', 'disabled')">Désactivé</button>
                            </div>
                        </div>
                        
                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Écoute Vocale (STT)</h4>
                            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary active-control" id="stt-ctrl-robot" onclick="setAIControl('stt', 'robot')">Robot Local</button>
                                <button class="btn btn-secondary" id="stt-ctrl-node" onclick="setAIControl('stt', 'node')">PC Node</button>
                                <button class="btn btn-secondary" id="stt-ctrl-off" onclick="setAIControl('stt', 'disabled')">Désactivé</button>
                            </div>
                        </div>

                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Moteur de Chat (LLM)</h4>
                            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary" id="chat-ctrl-robot" onclick="setAIControl('chat', 'robot')">Robot Local</button>
                                <button class="btn btn-secondary active-control" id="chat-ctrl-node" onclick="setAIControl('chat', 'node')">PC Node</button>
                                <button class="btn btn-secondary" id="chat-ctrl-off" onclick="setAIControl('chat', 'disabled')">Désactivé</button>
                            </div>
                        </div>

                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Détection d'Objets (YOLO)</h4>
                            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary active-control" id="yolo-ctrl-enabled" onclick="setAIControl('yolo', 'enabled')">Activé</button>
                                <button class="btn btn-secondary" id="yolo-ctrl-disabled" onclick="setAIControl('yolo', 'disabled')">Désactivé</button>
                            </div>
                        </div>

                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Reconnaissance Faciale</h4>
                            <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary active-control" id="face_rec-ctrl-enabled" onclick="setAIControl('face_rec', 'enabled')">Activé</button>
                                <button class="btn btn-secondary" id="face_rec-ctrl-disabled" onclick="setAIControl('face_rec', 'disabled')">Désactivé</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- WebSocket debug live JSON traffic -->
            <div class="card" style="margin-top: 1.5rem;">
                <div class="card-title">
                    <span>Console de Flux de Données WebSocket (Live JSON)</span>
                    <button class="btn btn-secondary" style="font-size:0.75rem; padding: 0.25rem 0.5rem;" onclick="clearJSONConsole()">Effacer</button>
                </div>
                <div id="json-traffic-console" style="height: 250px; overflow-y: auto; background-color: var(--bg-main); border: 1px solid var(--border-color); border-radius: 8px; font-family: monospace; font-size: 0.8rem; padding: 1rem; color: var(--success); white-space: pre-wrap; margin-top: 1rem;">
[Console démarrée - En attente de trafic WebSocket...]
                </div>
            </div>
        </div>

        <!-- ─────────────────── TAB 6: ARDUINO & CALIBRATION ─────────────────── -->
        <div id="tab-diagnostics-content" class="tab-content">
            <div class="card-grid">
                <!-- 12 Servomotor Angles -->
                <div class="card" style="grid-column: 1 / -1;">
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.75rem;">
                        <div class="card-title" style="margin:0;">Angles en Direct des 12 Servomoteurs (Arduino Mega)</div>
                        <div style="display:flex; align-items:center; gap:0.5rem; font-size:0.85rem;">
                            <input type="checkbox" id="joint-manual-toggle" onchange="toggleManualJointControl(this.checked)" style="accent-color:var(--accent); cursor:pointer; width:16px; height:16px;"/>
                            <label for="joint-manual-toggle" style="cursor:pointer; font-weight:600; color: var(--accent); user-select:none;">Activer le contrôle manuel (Mode Test)</label>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.25rem; margin-top: 1.5rem;">
                        <!-- FR -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.25rem; margin-bottom: 0.75rem; color: var(--accent);">Patte FR (Avant Droite)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.5rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Abad (Hanche)</span><span id="joint-val-0">90°</span></div>
                                    <input type="range" id="joint-slider-0" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(0, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Upper (Cuisse)</span><span id="joint-val-1">90°</span></div>
                                    <input type="range" id="joint-slider-1" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(1, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Lower (Tibia)</span><span id="joint-val-2">90°</span></div>
                                    <input type="range" id="joint-slider-2" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(2, this.value)"/>
                                </div>
                            </div>
                        </div>
                        
                        <!-- FL -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.25rem; margin-bottom: 0.75rem; color: var(--accent);">Patte FL (Avant Gauche)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.5rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Abad (Hanche)</span><span id="joint-val-3">90°</span></div>
                                    <input type="range" id="joint-slider-3" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(3, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Upper (Cuisse)</span><span id="joint-val-4">90°</span></div>
                                    <input type="range" id="joint-slider-4" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(4, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Lower (Tibia)</span><span id="joint-val-5">90°</span></div>
                                    <input type="range" id="joint-slider-5" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(5, this.value)"/>
                                </div>
                            </div>
                        </div>

                        <!-- BR -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.25rem; margin-bottom: 0.75rem; color: var(--accent);">Patte BR (Arrière Droite)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.5rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Abad (Hanche)</span><span id="joint-val-6">90°</span></div>
                                    <input type="range" id="joint-slider-6" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(6, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Upper (Cuisse)</span><span id="joint-val-7">90°</span></div>
                                    <input type="range" id="joint-slider-7" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(7, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Lower (Tibia)</span><span id="joint-val-8">90°</span></div>
                                    <input type="range" id="joint-slider-8" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(8, this.value)"/>
                                </div>
                            </div>
                        </div>

                        <!-- BL -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.25rem; margin-bottom: 0.75rem; color: var(--accent);">Patte BL (Arrière Gauche)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.5rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Abad (Hanche)</span><span id="joint-val-9">90°</span></div>
                                    <input type="range" id="joint-slider-9" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(9, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Upper (Cuisse)</span><span id="joint-val-10">90°</span></div>
                                    <input type="range" id="joint-slider-10" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(10, this.value)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Lower (Tibia)</span><span id="joint-val-11">90°</span></div>
                                    <input type="range" id="joint-slider-11" min="0" max="180" value="90" disabled style="width:100%; margin:0.25rem 0; height:6px; accent-color:var(--accent); background:rgba(255,255,255,0.08); border-radius:3px; outline:none; border:none; cursor:not-allowed;" oninput="onJointSliderInput(11, this.value)"/>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- IMU Gyro -->
                <div class="card">
                    <div class="card-title">Gyroscope & Orientation IMU</div>
                    <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height: 180px; position:relative;">
                        <div id="imu-visual-cube" style="width: 80px; height: 80px; transform-style: preserve-3d; transition: transform 0.1s linear; transform: rotateX(0deg) rotateY(0deg) rotateZ(0deg);">
                            <div style="position: absolute; width: 80px; height: 80px; background: rgba(99, 102, 241, 0.4); border: 2px solid var(--accent); transform: translateZ(40px); display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:bold;">AVANT</div>
                            <div style="position: absolute; width: 80px; height: 80px; background: rgba(255, 111, 97, 0.2); border: 2px solid var(--accent); transform: rotateY(180deg) translateZ(40px);"></div>
                            <div style="position: absolute; width: 80px; height: 80px; background: rgba(255, 111, 97, 0.2); border: 2px solid var(--accent); transform: rotateY(90deg) translateZ(40px);"></div>
                            <div style="position: absolute; width: 80px; height: 80px; background: rgba(255, 111, 97, 0.2); border: 2px solid var(--accent); transform: rotateY(-90deg) translateZ(40px);"></div>
                            <div style="position: absolute; width: 80px; height: 80px; background: rgba(255, 111, 97, 0.2); border: 2px solid var(--accent); transform: rotateX(90deg) translateZ(40px); display:flex; align-items:center; justify-content:center; font-size:10px; font-weight:bold;">HAUT</div>
                            <div style="position: absolute; width: 80px; height: 80px; background: rgba(255, 111, 97, 0.2); border: 2px solid var(--accent); transform: rotateX(-90deg) translateZ(40px);"></div>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; text-align: center; margin-top: 1rem; border-top: 1px solid var(--border-color); padding-top: 0.75rem;">
                        <div><div style="font-size:0.75rem; color:var(--text-secondary);">Roulis</div><span id="imu-val-roll" style="font-weight:600; font-size:0.9rem;">0.0°</span></div>
                        <div><div style="font-size:0.75rem; color:var(--text-secondary);">Tangage</div><span id="imu-val-pitch" style="font-weight:600; font-size:0.9rem;">0.0°</span></div>
                        <div><div style="font-size:0.75rem; color:var(--text-secondary);">Lacet</div><span id="imu-val-yaw" style="font-weight:600; font-size:0.9rem;">0.0°</span></div>
                    </div>
                </div>

                <!-- ROS 2 Topics -->
                <div class="card" style="display:flex; flex-direction:column; max-height: 310px; min-width: 0; overflow: hidden;">
                    <div class="card-title">Flux de Topics ROS 2 Actifs</div>
                    <div style="flex:1; overflow-x: auto; margin-top: 0.5rem; max-width: 100%; width: 100%;">
                        <table style="width: 100%; border-collapse: collapse; font-size: 0.8rem; text-align: left; min-width: 450px;">
                            <thead>
                                <tr style="color: var(--text-secondary); border-bottom: 1px solid var(--border-color); font-weight: 600;">
                                    <th style="padding: 0.4rem 0; white-space: nowrap;">Nom du Topic</th>
                                    <th style="padding: 0.4rem 0; white-space: nowrap; padding-left: 0.5rem; padding-right: 0.5rem;">Type</th>
                                    <th style="padding: 0.4rem 0; text-align: right; white-space: nowrap;">Hz</th>
                                </tr>
                            </thead>
                            <tbody id="ros2-topics-list">
                                <tr><td colspan="3" style="text-align: center; padding: 2rem 0; color: var(--text-secondary);">Aucun topic actif reporté.</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Calibration Section Overview -->
            <div class="card" style="margin-top: 1.5rem; display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap;">
                <div>
                    <h3 class="font-outfit" style="font-size: 1.15rem; margin-bottom: 0.25rem;">Section Calibration & Configuration WiFi</h3>
                    <p style="color: var(--text-secondary); font-size: 0.85rem;">Ajuster les angles des moteurs, configurer le WiFi et observer la minimap.</p>
                </div>
                <div style="display: flex; gap: 0.75rem; flex-wrap: wrap;">
                    <button class="btn btn-secondary" onclick="openWifiModal()">
                        📶 Configuration WiFi
                    </button>
                    <button class="btn btn-secondary" style="border-color: var(--accent); color: var(--success); background: rgba(255, 111, 97, 0.1);" onclick="openEasyConfig()">
                        ✨ Configuration Guidée (EasyConfig)
                    </button>
                    <button class="btn btn-primary" onclick="openCalibrationOverlay()">
                        ⚙️ Ouvrir la Calibration
                    </button>
                </div>
            </div>
        </div>

        <!-- ─────────────────── TAB 7: SLAM & MAP ─────────────────── -->
        <div id="tab-map-content" class="tab-content">
            <div class="card-grid" style="grid-template-columns: 2.5fr 1fr;">
                <!-- SLAM Visualizer -->
                <div class="card" style="display: flex; flex-direction: column; min-height: 500px;">
                    <div class="card-title">
                        <span>Visualiseur SLAM & Nuage de Points</span>
                        <button class="btn btn-secondary" style="font-size:0.75rem; padding: 0.25rem 0.5rem;" onclick="resetSLAMMap()">Réinitialiser Pose</button>
                    </div>
                    <div style="flex:1; border: 1px solid var(--border-color); border-radius: 8px; background-color: #07070a; overflow: hidden; display: flex; align-items: center; justify-content: center; position: relative; margin-top: 1rem;">
                        <canvas id="slam-map-canvas" style="width:100%; height:100%; display:block;"></canvas>
                        <div style="position: absolute; top: 0.75rem; right: 0.75rem; background: rgba(24,24,27,0.85); padding: 0.5rem 0.75rem; border: 1px solid var(--border-color); border-radius: 6px; font-size: 0.75rem; display:flex; flex-direction:column; gap:0.25rem; pointer-events:none;">
                            <div style="display:flex; justify-content:space-between; gap:1rem;"><span style="color:var(--text-secondary);">Résolution :</span><span id="slam-res-label">0.05 m/px</span></div>
                            <div style="display:flex; justify-content:space-between; gap:1rem;"><span style="color:var(--text-secondary);">Nuage de points :</span><span id="slam-cloud-count-label">0 pts</span></div>
                        </div>
                    </div>
                </div>
                
                <!-- SLAM Options -->
                <div style="display:flex; flex-direction:column; gap:1.5rem;">
                    <div class="card" style="margin: 0;">
                        <div class="card-title" style="font-size: 1rem;">Calques d'Affichage</div>
                        <div style="display: flex; flex-direction: column; gap: 0.75rem; margin-top: 0.75rem;">
                            <label style="display:flex; align-items:center; gap:0.5rem; font-size:0.85rem; cursor:pointer;">
                                <input type="checkbox" checked style="accent-color: var(--accent); width:16px; height:16px;" id="layer-grid" onchange="drawSLAMMap()"/>
                                Grille d'occupation 2D
                            </label>
                            <label style="display:flex; align-items:center; gap:0.5rem; font-size:0.85rem; cursor:pointer;">
                                <input type="checkbox" checked style="accent-color: var(--accent); width:16px; height:16px;" id="layer-trajectory" onchange="drawSLAMMap()"/>
                                Trajectoire du Robot (Path)
                            </label>
                            <label style="display:flex; align-items:center; gap:0.5rem; font-size:0.85rem; cursor:pointer;">
                                <input type="checkbox" checked style="accent-color: var(--accent); width:16px; height:16px;" id="layer-points" onchange="drawSLAMMap()"/>
                                Nuage de points laser
                            </label>
                            <label style="display:flex; align-items:center; gap:0.5rem; font-size:0.85rem; cursor:pointer;">
                                <input type="checkbox" style="accent-color: var(--accent); width:16px; height:16px;" id="layer-sonar" onchange="drawSLAMMap()"/>
                                Cônes Sonars & Obstacles
                            </label>
                        </div>
                    </div>
                    
                    <div class="card" style="margin: 0; flex:1;">
                        <div class="card-title" style="font-size: 1rem;">Paramètres SLAM</div>
                        <div style="display:flex; flex-direction:column; gap:1rem; margin-top: 1rem;">
                            <div>
                                <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom: 0.25rem;">
                                    <span>Résolution de la Carte</span>
                                    <span id="param-val-resolution">0.05m</span>
                                </div>
                                <input type="range" min="1" max="20" value="5" class="form-input" style="padding:0; height:4px;" id="param-slider-resolution" oninput="updateSLAMParam('resolution')"/>
                            </div>
                            
                            <div>
                                <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom: 0.25rem;">
                                    <span>Rayon d'Évitement</span>
                                    <span id="param-val-inflation">0.30m</span>
                                </div>
                                <input type="range" min="10" max="100" value="30" class="form-input" style="padding:0; height:4px;" id="param-slider-inflation" oninput="updateSLAMParam('inflation')"/>
                            </div>

                            <div>
                                <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom: 0.25rem;">
                                    <span>Seuil Détection Laser</span>
                                    <span id="param-val-laser-threshold">85%</span>
                                </div>
                                <input type="range" min="50" max="100" value="85" class="form-input" style="padding:0; height:4px;" id="param-slider-laser-threshold" oninput="updateSLAMParam('laser-threshold')"/>
                            </div>
                            
                            <button class="btn btn-primary" onclick="saveSLAMParameters()" style="width: 100%; margin-top: 0.5rem;">
                                Appliquer Paramètres
                            </button>
                        </div>
                    </div>
                    
                    <!-- Console de Test V-SLAM -->
                    <div class="card" style="margin: 1.5rem 0 0 0; display: flex; flex-direction: column; gap: 0.75rem;">
                        <div class="card-title" style="font-size: 1rem; display: flex; justify-content: space-between; align-items: center; width: 100%;">
                            <span>🔍 Console de Test V-SLAM</span>
                            <span id="vslam-badge" class="status-badge" style="font-size:0.65rem; padding: 0.1rem 0.4rem;">Inactif</span>
                        </div>
                        <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.25rem;">
                            Testez la localisation visuelle (ORB-SLAM3) en direct, même en portant le robot à la main.
                        </div>
                        
                        <!-- Mini video stream area -->
                        <div id="vslam-test-video-container" style="display: none; border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: #000; position: relative; aspect-ratio: 4/3; margin-bottom: 0.5rem;">
                            <video id="vslam-test-video" autoplay muted playsinline style="width: 100%; height: 100%; object-fit: cover;"></video>
                            <div id="vslam-test-loader" style="position: absolute; inset: 0; background: rgba(0,0,0,0.75); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.5rem; font-size: 0.75rem;">
                                <div style="width: 20px; height: 20px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                                <span>Initialisation flux WebRTC...</span>
                            </div>
                        </div>
                        
                        <!-- Controls -->
                        <button id="btn-vslam-test-toggle" class="btn btn-primary" style="width: 100%; justify-content: center; font-size: 0.85rem; padding: 0.5rem;" onclick="toggleVSlamTest()">
                            🚀 Lancer le Test V-SLAM
                        </button>
                        
                        <!-- Telemetry Stats -->
                        <div style="display: flex; flex-direction: column; gap: 0.5rem; border-top: 1px solid var(--border-color); padding-top: 0.75rem; font-size: 0.75rem; margin-top: 0.25rem;">
                            <div style="display: flex; justify-content: space-between;">
                                <span style="color: var(--text-secondary);">État du Tracking :</span>
                                <span id="vslam-status-val" style="font-weight: bold; color: var(--text-secondary);">Non démarré</span>
                            </div>
                            <div style="display: flex; justify-content: space-between;">
                                <span style="color: var(--text-secondary);">Vitesse de Pose :</span>
                                <span id="vslam-rate-val" style="font-weight: bold;">0.0 Hz</span>
                            </div>
                            <div style="display: flex; justify-content: space-between;">
                                <span style="color: var(--text-secondary);">Qualité Environnement :</span>
                                <span id="vslam-quality-val" style="font-weight: bold; color: var(--success);">Optimale</span>
                            </div>
                            <div id="vslam-warning-box" style="display: none; padding: 0.4rem; background: rgba(239, 68, 68, 0.1); border: 1px solid var(--danger); border-radius: 4px; color: var(--danger); font-size: 0.7rem; line-height: 1.2;">
                                ⚠️ Attention: Mouvements trop brusques ou luminosité faible détectée.
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Full page Calibration Overlay -->
    <div id="calibration-overlay" class="modal-overlay" style="justify-content: center; align-items: center; background-color: rgba(9, 9, 11, 0.95); backdrop-filter: blur(8px);">
        <div style="width: 95vw; height: 95vh; background-color: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);">
            <div style="padding: 1.5rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; background-color: var(--bg-card);">
                <div>
                    <h2 class="font-outfit" style="font-size: 1.5rem; color: var(--text-primary);">Console de Calibration Système</h2>
                    <p style="color: var(--text-secondary); font-size: 0.8rem; margin-top: 0.15rem;">Ajustez les offsets des moteurs, testez les caméras et observez la minimap.</p>
                </div>
                <button class="btn btn-secondary" onclick="closeCalibrationOverlay()">&times; Fermer la Console</button>
            </div>
            
            <div style="flex: 1; display: grid; grid-template-columns: 1.5fr 1fr; overflow: hidden;">
                <!-- Offsets sliders -->
                <div style="padding: 1.5rem; overflow-y: auto; border-right: 1px solid var(--border-color);">
                    <h3 class="font-outfit" style="font-size: 1.1rem; border-bottom: 1px solid var(--border-color); padding-bottom: 0.5rem; margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: center;">
                        <span>Offsets des Angles Moteurs (-30° à +30°)</span>
                        <button class="btn btn-secondary" style="font-size:0.75rem; padding: 0.25rem 0.5rem;" onclick="resetMotorCalibration()">Réinitialiser</button>
                    </h3>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
                        <!-- FR -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">FR (Avant Droite)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Abad Offset</span><span id="calib-val-0">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-0" oninput="updateCalibSliderVal(0)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Upper Offset</span><span id="calib-val-1">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-1" oninput="updateCalibSliderVal(1)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Lower Offset</span><span id="calib-val-2">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-2" oninput="updateCalibSliderVal(2)"/>
                                </div>
                            </div>
                        </div>
                        
                        <!-- FL -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">FL (Avant Gauche)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Abad Offset</span><span id="calib-val-3">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-3" oninput="updateCalibSliderVal(3)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Upper Offset</span><span id="calib-val-4">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-4" oninput="updateCalibSliderVal(4)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Lower Offset</span><span id="calib-val-5">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-5" oninput="updateCalibSliderVal(5)"/>
                                </div>
                            </div>
                        </div>

                        <!-- BR -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">BR (Arrière Droite)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Abad Offset</span><span id="calib-val-6">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-6" oninput="updateCalibSliderVal(6)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Upper Offset</span><span id="calib-val-7">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-7" oninput="updateCalibSliderVal(7)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Lower Offset</span><span id="calib-val-8">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-8" oninput="updateCalibSliderVal(8)"/>
                                </div>
                            </div>
                        </div>

                        <!-- BL -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">BL (Arrière Gauche)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Abad Offset</span><span id="calib-val-9">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-9" oninput="updateCalibSliderVal(9)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Upper Offset</span><span id="calib-val-10">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-10" oninput="updateCalibSliderVal(10)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Lower Offset</span><span id="calib-val-11">0</span></div>
                                    <input type="range" min="-30" max="30" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-11" oninput="updateCalibSliderVal(11)"/>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div style="margin-top: 2rem; display: flex; gap: 1rem;">
                        <button class="btn btn-primary" onclick="saveCalibrationOffsets()" style="flex:1;">
                            💾 Sauvegarder & Transmettre les Offsets
                        </button>
                        <button class="btn btn-secondary" onclick="sendCalibrationOffsets()" style="flex:1;">
                            ⚡ Tester Directement
                        </button>
                    </div>
                </div>
                
                <!-- Camera and Minimap -->
                <div style="padding: 1.5rem; display: flex; flex-direction: column; gap: 1.5rem; overflow-y: auto;">
                    <div class="card" style="margin: 0; background-color: rgba(255,255,255,0.01);">
                        <div class="card-title" style="font-size:0.95rem;">Configuration des Caméras</div>
                        
                        <!-- Mapping Selection -->
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin-top: 0.5rem; padding-bottom: 0.75rem; border-bottom: 1px solid var(--border-color);">
                            <div>
                                <label class="form-label" style="font-size:0.75rem; margin-bottom:0.25rem;">Port Gauche</label>
                                <select id="cam-port-left" class="form-input" style="padding: 0.35rem; font-size: 0.8rem;" onchange="saveCameraPortsMapping()">
                                    <option value="/dev/video0">/dev/video0</option>
                                    <option value="/dev/video1">/dev/video1</option>
                                    <option value="/dev/video2">/dev/video2</option>
                                    <option value="/dev/video3">/dev/video3</option>
                                    <option value="/dev/video4">/dev/video4</option>
                                </select>
                            </div>
                            <div>
                                <label class="form-label" style="font-size:0.75rem; margin-bottom:0.25rem;">Port Droite</label>
                                <select id="cam-port-right" class="form-input" style="padding: 0.35rem; font-size: 0.8rem;" onchange="saveCameraPortsMapping()">
                                    <option value="/dev/video0">/dev/video0</option>
                                    <option value="/dev/video1">/dev/video1</option>
                                    <option value="/dev/video2">/dev/video2</option>
                                    <option value="/dev/video3">/dev/video3</option>
                                    <option value="/dev/video4">/dev/video4</option>
                                </select>
                            </div>
                        </div>

                        <div style="display:flex; flex-direction:column; gap:0.75rem; margin-top: 0.75rem;">
                            <div id="calib-cam-container-1" style="display:flex; justify-content:space-between; align-items:center;">
                                <div>
                                    <span style="font-size: 0.85rem; font-weight:600; display:block;">Caméra Gauche</span>
                                    <div style="display:flex; align-items:center; gap:0.5rem; margin-top:0.1rem;">
                                        <span style="font-size:0.75rem; color:var(--text-secondary);">Statut: <span id="calib-cam-status-1" style="color:var(--success);">Connectée</span></span>
                                        <button class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.7rem; border-radius:4px; height:auto; margin:0;" onclick="openCameraCalibModal(1)">📷 Calibrer</button>
                                    </div>
                                </div>
                                <input type="checkbox" checked style="accent-color: var(--accent); width:18px; height:18px;" id="calib-cam-enable-1" onchange="toggleCalibCamera(1)"/>
                            </div>
                            
                            <div id="calib-cam-container-2" style="display:flex; justify-content:space-between; align-items:center; border-top: 1px solid var(--border-color); padding-top: 0.75rem;">
                                <div>
                                    <span style="font-size: 0.85rem; font-weight:600; display:block;">Caméra Droite</span>
                                    <div style="display:flex; align-items:center; gap:0.5rem; margin-top:0.1rem;">
                                        <span style="font-size:0.75rem; color:var(--text-secondary);">Statut: <span id="calib-cam-status-2" style="color:var(--text-secondary);">Déconnectée</span></span>
                                        <button class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.7rem; border-radius:4px; height:auto; margin:0;" onclick="openCameraCalibModal(2)">📷 Calibrer</button>
                                    </div>
                                </div>
                                <input type="checkbox" style="accent-color: var(--accent); width:18px; height:18px;" id="calib-cam-enable-2" onchange="toggleCalibCamera(2)"/>
                            </div>
                        </div>
                    </div>
                    
                    <div class="card" style="margin: 0; flex: 1; display: flex; flex-direction: column; background-color: rgba(255,255,255,0.01); min-height: 280px;">
                        <div class="card-title" style="font-size:0.95rem;">Minimap de Localisation (Position XY)</div>
                        <div style="flex:1; border: 1px solid var(--border-color); border-radius: 8px; background-color: var(--bg-main); overflow: hidden; display: flex; align-items: center; justify-content: center; position: relative;">
                            <canvas id="minimap-canvas" style="width:100%; height:100%; min-height:200px; display:block;"></canvas>
                            <div style="position: absolute; bottom: 0.5rem; left: 0.5rem; font-size: 0.7rem; color: var(--text-secondary); background: rgba(0,0,0,0.7); padding: 0.2rem 0.4rem; border-radius: 4px;">
                                Pose: <span id="minimap-pose-text">x: 0.00, y: 0.00, θ: 0°</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- EasyConfig Overlay -->
    <div id="easyconfig-overlay" class="modal-overlay" style="justify-content: center; align-items: center; background-color: rgba(9, 9, 11, 0.95); backdrop-filter: blur(8px);">
        <div style="width: 800px; max-width: 95vw; max-height: 90vh; background-color: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);">
            <!-- Header -->
            <div style="padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; background-color: var(--bg-card);">
                <div>
                    <h2 class="font-outfit" style="font-size: 1.3rem; color: var(--text-primary); display: flex; align-items: center; gap: 0.5rem;">
                        <span>✨ Assistant de Configuration Guidée</span>
                        <span style="font-size: 0.75rem; background: var(--accent); color: white; padding: 0.1rem 0.4rem; border-radius: 4px; font-weight: normal;">EasyConfig</span>
                    </h2>
                </div>
                <button class="modal-close" style="position: static;" onclick="closeEasyConfig()">&times;</button>
            </div>
            
            <!-- Steps Indicator -->
            <div style="display: flex; background: var(--bg-main); border-bottom: 1px solid var(--border-color); padding: 0.75rem 1.5rem; justify-content: space-between; font-size: 0.8rem;">
                <div id="step-dot-1" style="display:flex; align-items:center; gap:0.5rem; color: var(--accent); font-weight: 600;">
                    <span style="width:20px; height:20px; border-radius:50%; background:var(--accent); color:white; display:flex; align-items:center; justify-content:center; font-size:0.7rem;">1</span>
                    <span>Offsets Moteurs</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-2" style="display:flex; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">2</span>
                    <span>Caméra Gauche</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-3" style="display:flex; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">3</span>
                    <span>Caméra Droite</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-4" style="display:flex; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">4</span>
                    <span>Finalisation</span>
                </div>
            </div>
            
            <!-- Content Area -->
            <div style="flex: 1; padding: 1.5rem; overflow-y: auto; display: flex; flex-direction: column; min-height: 350px;">
                
                <!-- STEP 1 CONTENT -->
                <div id="ec-step-1" style="display: flex; flex-direction: column; gap: 1rem; height: 100%;">
                    <div style="line-height:1.5; font-size:0.9rem;">
                        <p style="font-weight:600; color:var(--text-primary); font-size:1rem; margin-bottom:0.5rem;">Étape 1 : Alignement Physique & Niveau 0 des Moteurs</p>
                        Avant de démarrer le robot, veuillez le positionner manuellement dans la position de calibration :
                        <ul style="margin: 0.5rem 0; padding-left: 1.25rem; display:flex; flex-direction:column; gap:0.25rem;">
                            <li>• Positionnez les <strong>hanches droites</strong>, perpendiculaires et parallèles au sol.</li>
                            <li>• Mettez les <strong>cuisses parallèles au sol</strong> (angle de 90°).</li>
                            <li>• Pliez le <strong>tibia à 100%</strong> contre la cuisse (angle de 0°).</li>
                        </ul>
                    </div>
                    
                    <!-- Motor angles feedback grid -->
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border-color); border-radius: 8px; padding: 1rem; display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.75rem; font-size:0.75rem;">
                        <div style="border-right: 1px solid var(--border-color); padding-right: 0.5rem;">
                            <span style="font-weight:bold; color:var(--accent); display:block; margin-bottom:0.25rem;">Patte FR</span>
                            <span>Hanche: <strong id="ec-j0">90°</strong></span><br/>
                            <span>Cuisse: <strong id="ec-j1">90°</strong></span><br/>
                            <span>Tibia: <strong id="ec-j2">0°</strong></span>
                        </div>
                        <div style="border-right: 1px solid var(--border-color); padding-right: 0.5rem; padding-left: 0.25rem;">
                            <span style="font-weight:bold; color:var(--accent); display:block; margin-bottom:0.25rem;">Patte FL</span>
                            <span>Hanche: <strong id="ec-j3">90°</strong></span><br/>
                            <span>Cuisse: <strong id="ec-j4">90°</strong></span><br/>
                            <span>Tibia: <strong id="ec-j5">0°</strong></span>
                        </div>
                        <div style="border-right: 1px solid var(--border-color); padding-right: 0.5rem; padding-left: 0.25rem;">
                            <span style="font-weight:bold; color:var(--accent); display:block; margin-bottom:0.25rem;">Patte BR</span>
                            <span>Hanche: <strong id="ec-j6">90°</strong></span><br/>
                            <span>Cuisse: <strong id="ec-j7">90°</strong></span><br/>
                            <span>Tibia: <strong id="ec-j8">0°</strong></span>
                        </div>
                        <div style="padding-left: 0.25rem;">
                            <span style="font-weight:bold; color:var(--accent); display:block; margin-bottom:0.25rem;">Patte BL</span>
                            <span>Hanche: <strong id="ec-j9">90°</strong></span><br/>
                            <span>Cuisse: <strong id="ec-j10">90°</strong></span><br/>
                            <span>Tibia: <strong id="ec-j11">0°</strong></span>
                        </div>
                    </div>
                    
                    <div style="margin-top: auto; display: flex; flex-direction:column; gap:0.75rem;">
                        <button class="btn btn-primary" onclick="ecCalculateOffsets()" style="width: 100%; justify-content: center; padding: 0.75rem;">
                            📐 Calculer et Définir le Niveau 0 (Offsets)
                        </button>
                        <div id="ec-motor-success-anim" style="display:none; text-align:center; color:var(--success); font-size:0.85rem; font-weight:bold; animation: fadeIn 0.3s ease;">
                            ✅ Offsets calculés et transmis au robot avec succès !
                        </div>
                    </div>
                </div>
                
                <!-- STEP 2 CONTENT (CAMERA GAUCHE) -->
                <div id="ec-step-2" style="display: none; flex-direction: column; gap: 1rem; height: 100%;">
                    <div style="line-height:1.5; font-size:0.9rem;">
                        <p style="font-weight:600; color:var(--text-primary); font-size:1rem; margin-bottom:0.5rem;">Étape 2 : Alignement & Calibration Caméra Gauche (1)</p>
                        Placez la feuille de calibration (damier noir et blanc) bien à plat devant la caméra gauche.
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 1.5fr 1fr; gap: 1.5rem; flex: 1; min-height: 220px;">
                        <!-- Live stream frame -->
                        <div style="border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: #000; position: relative;">
                            <video id="ec-cam-video-1" autoplay muted playsinline style="width: 100%; height: 100%; object-fit: cover; display: none;"></video>
                            
                            <!-- Scanner HUD Overlay -->
                            <div id="ec-cam-hud-1" style="display: none; position: absolute; inset: 0; pointer-events: none; border: 2px solid var(--accent); animation: pulse 2s infinite;">
                                <div style="position: absolute; top: 10%; left: 10%; right: 10%; height: 2px; background: var(--accent); box-shadow: 0 0 10px var(--accent); animation: scanLine 2s linear infinite;"></div>
                                <div style="position: absolute; bottom: 0.5rem; right: 0.5rem; font-size: 0.65rem; color: var(--accent); background: rgba(0,0,0,0.8); padding: 0.2rem 0.4rem; border-radius: 4px;">
                                    MIRE DE CALIBRATION EN COURS D'ANALYSE
                                </div>
                            </div>
                            
                            <!-- Initial state / loader / Success / Failure overlay -->
                            <div id="ec-cam-status-overlay-1" style="position: absolute; inset: 0; background: rgba(0,0,0,0.85); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.5rem; font-size: 0.8rem; text-align: center; padding: 1rem;">
                                <span id="ec-cam-status-text-1" style="color: var(--text-secondary);">Le flux vidéo de la caméra s'affiche dès le lancement.</span>
                            </div>
                        </div>
                        
                        <!-- Calibration details and buttons -->
                        <div style="display: flex; flex-direction: column; gap: 1rem; justify-content: center;">
                            <button id="btn-ec-run-calib-1" class="btn btn-primary" onclick="ecRunCameraCalib(1)" style="width:100%; justify-content:center;">
                                📷 Lancer la Calibration Cam1
                            </button>
                            <button id="btn-ec-skip-1" class="btn btn-secondary" onclick="ecNextStep(3)" style="width:100%; justify-content:center;">
                                Passer cette étape (Conserver l'ancienne)
                            </button>
                        </div>
                    </div>
                </div>

                <!-- STEP 3 CONTENT (CAMERA DROITE) -->
                <div id="ec-step-3" style="display: none; flex-direction: column; gap: 1rem; height: 100%;">
                    <div style="line-height:1.5; font-size:0.9rem;">
                        <p style="font-weight:600; color:var(--text-primary); font-size:1rem; margin-bottom:0.5rem;">Étape 3 : Alignement & Calibration Caméra Droite (2)</p>
                        Placez la feuille de calibration devant la caméra droite.
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 1.5fr 1fr; gap: 1.5rem; flex: 1; min-height: 220px;">
                        <!-- Live stream frame -->
                        <div style="border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; background: #000; position: relative;">
                            <video id="ec-cam-video-2" autoplay muted playsinline style="width: 100%; height: 100%; object-fit: cover; display: none;"></video>
                            
                            <!-- Scanner HUD Overlay -->
                            <div id="ec-cam-hud-2" style="display: none; position: absolute; inset: 0; pointer-events: none; border: 2px solid var(--accent); animation: pulse 2s infinite;">
                                <div style="position: absolute; top: 10%; left: 10%; right: 10%; height: 2px; background: var(--accent); box-shadow: 0 0 10px var(--accent); animation: scanLine 2s linear infinite;"></div>
                                <div style="position: absolute; bottom: 0.5rem; right: 0.5rem; font-size: 0.65rem; color: var(--accent); background: rgba(0,0,0,0.8); padding: 0.2rem 0.4rem; border-radius: 4px;">
                                    MIRE DE CALIBRATION EN COURS D'ANALYSE
                                </div>
                            </div>
                            
                            <!-- Initial state / loader / Success / Failure overlay -->
                            <div id="ec-cam-status-overlay-2" style="position: absolute; inset: 0; background: rgba(0,0,0,0.85); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.5rem; font-size: 0.8rem; text-align: center; padding: 1rem;">
                                <span id="ec-cam-status-text-2" style="color: var(--text-secondary);">Le flux vidéo de la caméra s'affiche dès le lancement.</span>
                            </div>
                        </div>
                        
                        <!-- Calibration details and buttons -->
                        <div style="display: flex; flex-direction: column; gap: 1rem; justify-content: center;">
                            <button id="btn-ec-run-calib-2" class="btn btn-primary" onclick="ecRunCameraCalib(2)" style="width:100%; justify-content:center;">
                                📷 Lancer la Calibration Cam2
                            </button>
                            <button id="btn-ec-skip-2" class="btn btn-secondary" onclick="ecNextStep(4)" style="width:100%; justify-content:center;">
                                Passer cette étape
                            </button>
                        </div>
                    </div>
                </div>

                <!-- STEP 4 CONTENT (FINALISATION) -->
                <div id="ec-step-4" style="display: none; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 1.5rem; height: 100%; padding: 2rem 0;">
                    <div style="width: 80px; height: 80px; border-radius: 50%; background: rgba(72, 209, 204, 0.1); border: 2px solid var(--success); display: flex; align-items: center; justify-content: center; font-size: 2.5rem; color: var(--success); animation: scaleUp 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);">
                        ✓
                    </div>
                    <div>
                        <h3 class="font-outfit" style="font-size: 1.4rem; color: var(--text-primary); margin-bottom: 0.5rem;">Configuration Guidée Réussie !</h3>
                        <p style="color: var(--text-secondary); max-width: 500px; line-height: 1.5; font-size: 0.9rem;">
                            Tous les offsets des moteurs et les réglages des caméras ont été enregistrés avec succès. Le robot Bastet est parfaitement initialisé et prêt à démarrer.
                        </p>
                    </div>
                    
                    <div style="display: flex; gap: 1rem; width: 100%; max-width: 500px; margin-top: 1rem;">
                        <button class="btn btn-primary" onclick="ecStartRobotAndClose()" style="flex: 1; justify-content: center; padding: 0.75rem;">
                            🚀 Démarrer & Allumer le Robot
                        </button>
                        <button class="btn btn-secondary" onclick="closeEasyConfig()" style="flex: 1; justify-content: center; padding: 0.75rem;">
                            Fermer l'Assistant
                        </button>
                    </div>
                </div>
            </div>
            
            <!-- Footer -->
            <div style="padding: 1rem 1.5rem; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; background-color: var(--bg-main);">
                <button id="ec-btn-prev" class="btn btn-secondary" disabled onclick="ecPrevStep()" style="font-size: 0.8rem; padding: 0.4rem 0.75rem;">
                    ← Précédent
                </button>
                <span id="ec-progress-text" style="font-size:0.75rem; color: var(--text-secondary);">Étape 1 sur 4</span>
                <button id="ec-btn-next" class="btn btn-primary" disabled onclick="ecNextStep()" style="font-size: 0.8rem; padding: 0.4rem 0.75rem;">
                    Suivant →
                </button>
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

    <!-- Modal : WiFi -->
    <div id="wifiModal" class="modal-overlay" onclick="closeWifiModalOnClick(event)">
        <div class="modal-content" style="max-width: 600px;">
            <div class="modal-header">
                <h3 class="font-outfit" style="font-size: 1.25rem; font-weight: 700;">Réseaux WiFi aux Alentours</h3>
                <button class="modal-close" onclick="closeWifiModal()">&times;</button>
            </div>
            
            <div style="margin-bottom: 1.5rem; display:flex; justify-content:space-between; align-items:center;">
                <span style="font-size:0.85rem; color:var(--text-secondary);">Sélectionnez un réseau à configurer sur le robot.</span>
                <button class="btn btn-secondary" onclick="scanWifiNetworks()" id="btn-wifi-scan">
                    🔄 Rafraîchir
                </button>
            </div>
            
            <div style="display:flex; flex-direction:column; gap:0.5rem; margin-bottom: 1.25rem;">
                <h4 style="font-size:0.8rem; font-weight:600; color:var(--accent);">Réseaux WiFi Enregistrés (Connus)</h4>
                <div id="wifi-known-container" style="max-height: 150px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: 8px; background-color: var(--bg-main); padding: 0.5rem;">
                    <div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.8rem;">Aucun réseau enregistré configuré.</div>
                </div>
            </div>
            
            <div style="display:flex; flex-direction:column; gap:0.5rem; margin-bottom: 1.5rem;">
                <h4 style="font-size:0.8rem; font-weight:600; color:var(--text-primary);">Réseaux à Proximité Scannés</h4>
                <div id="wifi-list-container" style="max-height: 200px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: 8px; background-color: var(--bg-main); padding: 0.5rem;">
                    <div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.8rem;">Aucun réseau scanné. Cliquez sur Rafraîchir.</div>
                </div>
            </div>
            
            <form id="wifi-connect-form" onsubmit="handleWifiConnectSubmit(event)" style="display: none; border-top: 1px solid var(--border-color); padding-top: 1.5rem;">
                <input type="hidden" id="form-wifi-ssid"/>
                <div class="form-group">
                    <label class="form-label" style="font-weight: 600;">Se connecter à : <span id="wifi-selected-ssid-label" style="color:var(--accent);">SSID</span></label>
                </div>
                <div class="form-group" id="wifi-password-group">
                    <label class="form-label" for="form-wifi-password">Mot de passe du réseau</label>
                    <input type="password" id="form-wifi-password" class="form-input" placeholder="••••••••" autocomplete="current-password"/>
                </div>
                <div style="display:flex; gap:0.5rem;">
                    <button type="submit" class="btn btn-primary" style="flex:1;">Se connecter au WiFi</button>
                    <button type="button" class="btn btn-secondary" onclick="cancelWifiConnection()">Annuler</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Modal : Camera Calibration -->
    <div id="cameraCalibModal" class="modal-overlay" onclick="closeCameraCalibModalOnClick(event)">
        <div class="modal-content" style="max-width: 650px; background-color: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px;">
            <div class="modal-header">
                <h3 class="font-outfit" style="font-size: 1.25rem; font-weight: 700;" id="mcc-modal-title">Calibration Caméra</h3>
                <button class="modal-close" onclick="closeCameraCalibModal()">&times;</button>
            </div>
            
            <div style="margin-bottom: 1rem;">
                <p style="font-size:0.85rem; color:var(--text-secondary);">Placez la feuille de calibration (damier noir et blanc) bien à plat devant la caméra.</p>
            </div>
            
            <!-- Video Container -->
            <div style="width: 100%; height: 320px; border: 1px solid var(--border-color); border-radius: 8px; background-color: var(--bg-main); overflow: hidden; display: flex; align-items: center; justify-content: center; position: relative; margin-bottom: 1.25rem;">
                <video id="mcc-cam-video" autoplay muted playsinline style="width: 100%; height: 100%; object-fit: cover; display: none;"></video>
                
                <div id="mcc-cam-hud" style="position: absolute; top:0; left:0; width:100%; height:100%; border: 2px dashed rgba(99, 102, 241, 0.4); box-sizing: border-box; display:none; pointer-events:none;">
                    <div style="position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); color:rgba(99, 102, 241, 0.6); font-family:monospace; font-size:0.75rem; border:1px solid rgba(99,102,241,0.6); padding:0.25rem 0.5rem; background:rgba(0,0,0,0.5);">MIRE DE CALIBRATION EN COURS D'ANALYSE</div>
                </div>
                
                <div id="mcc-cam-status-overlay" style="position: absolute; top:0; left:0; width:100%; height:100%; display: flex; flex-direction: column; align-items: center; justify-content: center; background: rgba(9,9,11,0.85); padding:1rem; text-align:center;">
                    <div id="mcc-cam-status-text" style="color: var(--text-primary); font-size: 0.85rem;">
                        <span>Cliquez sur Lancer pour vous connecter à la caméra.</span>
                    </div>
                </div>
            </div>
            
            <div style="display:flex; gap:0.5rem;">
                <button id="btn-mcc-run-calib" class="btn btn-primary" style="flex:2;" onclick="runIndividualCameraCalib()">
                    📷 Lancer la Caméra
                </button>
                <button class="btn btn-secondary" style="flex:1;" onclick="closeCameraCalibModal()">
                    Fermer
                </button>
            </div>
        </div>
    </div>

    <!-- Screen lightbox overlay -->
    <div id="lightbox" class="modal-overlay" onclick="closeLightbox()" style="background-color: rgba(0,0,0,0.95); cursor: zoom-out;">
        <img id="lightbox-img" style="max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 4px; box-shadow: 0 10px 40px rgba(0,0,0,0.8);"/>
    </div>

    <script>
        let apiToken = localStorage.getItem('bastet_api_token') || '';
        let activeTab = 'dashboard';
        let telemetryInterval = null;
        let updateInterval = null;
        let accountsCached = {};
        let activeFolderName = null;
        let facesCached = [];
        let appWs = null;
        window.activeStreams = { 1: false, 2: false };
        window.localViewing = { 1: false, 2: false };
        window.userClosedStream = { 1: false, 2: false };
        let peerConnections = { 1: null, 2: null };
        window.manualJointControlActive = false;
        
        // SLAM / Map variables
        window.slamGrid = null;
        window.slamPath = [];
        window.slamPoints = [];
        window.robotPose = {x: 0, y: 0, theta: 0};

        // ─── THEME ──────────────────────────────────────────────────────────────
        function getCookie(name) {
            const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
            return v ? v.pop() : null;
        }
        function setCookie(name, value, days) {
            const d = new Date();
            d.setTime(d.getTime() + days * 86400000);
            document.cookie = name + '=' + value + ';expires=' + d.toUTCString() + ';path=/;SameSite=Lax';
        }
        function applyTheme(theme) {
            document.documentElement.setAttribute('data-theme', theme);
            const isDark = theme === 'dark';
            document.querySelectorAll('#theme-icon-dark, #theme-icon-dark-m').forEach(el => el.style.display = isDark ? '' : 'none');
            document.querySelectorAll('#theme-icon-light, #theme-icon-light-m').forEach(el => el.style.display = isDark ? 'none' : '');
        }
        function toggleTheme() {
            const current = document.documentElement.getAttribute('data-theme') || 'dark';
            const next = current === 'dark' ? 'light' : 'dark';
            setCookie('bastet_theme', next, 365);
            applyTheme(next);
        }
        (function initTheme() {
            const saved = getCookie('bastet_theme');
            applyTheme(saved || 'dark');
        })();

        // ─── INIT ─────────────────────────────────────────────────────────────
        
        async function checkAuth() {
            if (!apiToken) {
                showLogin();
                return;
            }
            try {
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
                stopStreamUI(id);
            }
            if (appWs) {
                appWs.close();
                appWs = null;
            }
            apiToken = '';
            localStorage.removeItem('bastet_api_token');
            showLogin();
        }

        function initDashboard() {
            switchTab(activeTab);
            startIntervals();
            initDragAndDrop();
            connectGlobalWebSocket();
        }

        // --- INTERVALS ---
        function startIntervals() {
            clearIntervals();
            fetchTelemetry();
            fetchUpdatesProgress(true);
            telemetryInterval = setInterval(fetchTelemetry, 2000);
            updateInterval = setInterval(() => fetchUpdatesProgress(false), 2000);
        }

        function clearIntervals() {
            if (telemetryInterval) clearInterval(telemetryInterval);
            if (updateInterval) clearInterval(updateInterval);
        }

        // ─── WEBSOCKET CLIENT ─────────────────────────────────────────────────
        
        function connectGlobalWebSocket() {
            if (appWs && (appWs.readyState === WebSocket.OPEN || appWs.readyState === WebSocket.CONNECTING)) return;
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws/app?token=${apiToken}`;
            appWs = new WebSocket(wsUrl);
            
            appWs.onopen = () => {
                console.log("Global WebSocket connecté.");
                const consoleEl = document.getElementById('json-traffic-console');
                if (consoleEl) consoleEl.textContent = '[WebSocket connecté - En attente de trafic...]';
            };
            
            appWs.onmessage = (event) => {
                handleIncomingWebSocketMessage(event.data);
            };
            
            appWs.onclose = () => {
                console.log("Global WebSocket déconnecté. Reconnexion...");
                setTimeout(connectGlobalWebSocket, 3000);
            };
            
            appWs.onerror = (e) => {
                console.error("Global WebSocket erreur:", e);
            };
        }

        function logToJSONConsole(data) {
            const consoleEl = document.getElementById('json-traffic-console');
            if (!consoleEl) return;
            
            if (consoleEl.textContent.length > 20000) {
                consoleEl.textContent = consoleEl.textContent.slice(-10000);
            }
            
            const timeStr = new Date().toLocaleTimeString();
            consoleEl.textContent += `\n[${timeStr}] ${data}`;
            consoleEl.scrollTop = consoleEl.scrollHeight;
        }

        function handleIncomingWebSocketMessage(data) {
            try {
                const payload = JSON.parse(data);
                
                // Print all JSON traffic to the Console
                logToJSONConsole(JSON.stringify(payload, null, 2));
                
                if (payload.type === "telemetry_diagnostics") {
                    if (payload.cameras) {
                        updateCameraModularity(payload.cameras.cam1 === true, payload.cameras.cam2 === true);
                    }
                    if (payload.ai_state) {
                        updateAIControlUI('tts', payload.ai_state.tts);
                        updateAIControlUI('stt', payload.ai_state.stt);
                        updateAIControlUI('chat', payload.ai_state.chat);
                        updateAIControlUI('yolo', payload.ai_state.yolo);
                        updateAIControlUI('face_rec', payload.ai_state.face_rec);
                    }
                    
                    // Update joint angles (0 to 11)
                    if (payload.joints && payload.joints.length === 12) {
                        for (let i = 0; i < 12; i++) {
                            const angle = payload.joints[i];
                            const valEl = document.getElementById(`joint-val-${i}`);
                            const sliderEl = document.getElementById(`joint-slider-${i}`);
                            if (!window.manualJointControlActive) {
                                if (valEl) valEl.textContent = `${Math.round(angle)}°`;
                                if (sliderEl) sliderEl.value = Math.round(angle);
                            }
                        }
                    }
                    
                    // Update IMU
                    if (payload.imu) {
                        const roll = payload.imu.roll || 0;
                        const pitch = payload.imu.pitch || 0;
                        const yaw = payload.imu.yaw || 0;
                        
                        document.getElementById('imu-val-roll').textContent = `${roll.toFixed(1)}°`;
                        document.getElementById('imu-val-pitch').textContent = `${pitch.toFixed(1)}°`;
                        document.getElementById('imu-val-yaw').textContent = `${yaw.toFixed(1)}°`;
                        
                        // Rotate 3D IMU CSS Cube
                        const cube = document.getElementById('imu-visual-cube');
                        if (cube) {
                            cube.style.transform = `rotateX(${pitch}deg) rotateY(${roll}deg) rotateZ(${-yaw}deg)`;
                        }
                    }
                    
                    // Update Active ROS 2 Topics
                    if (payload.topics) {
                        const tbody = document.getElementById('ros2-topics-list');
                        if (tbody) {
                            tbody.innerHTML = '';
                            payload.topics.forEach(t => {
                                const tr = document.createElement('tr');
                                tr.style.borderBottom = '1px solid var(--border-color)';
                                tr.innerHTML = `
                                    <td style="padding: 0.4rem 0; font-family:monospace; color:var(--accent); white-space: nowrap;">${t.name}</td>
                                    <td style="padding: 0.4rem 0; color:var(--text-secondary); white-space: nowrap; padding-left: 0.5rem; padding-right: 0.5rem;">${t.type}</td>
                                    <td style="padding: 0.4rem 0; text-align:right; font-weight:bold; white-space: nowrap;">${t.hz.toFixed(1)}</td>
                                `;
                                tbody.appendChild(tr);
                            });
                        }
                    }
                    
                    // Update path & pose from diagnostics if present
                    if (payload.pose) {
                        window.robotPose = payload.pose;
                        const rx = payload.pose.x || 0;
                        const ry = payload.pose.y || 0;
                        const rtheta = payload.pose.theta || 0;
                        document.getElementById('minimap-pose-text').textContent = `x: ${rx.toFixed(2)}, y: ${ry.toFixed(2)}, θ: ${Math.round(rtheta * 180 / Math.PI)}°`;
                    }
                    if (payload.path) {
                        window.slamPath = payload.path;
                    }
                }
                else if (payload.type === "stream_status") {
                    const camId = parseInt(payload.camera);
                    const isActive = payload.active === true;
                    if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };
                    
                    const wasActive = window.activeStreams[camId];
                    window.activeStreams[camId] = isActive;
                    
                    if (isActive && !wasActive) {
                        if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                        window.userClosedStream[camId] = false;
                    }
                    
                    const statusEl = document.getElementById(`stream-status-${camId}`);
                    const btnText = document.getElementById(`stream-btn-text-${camId}`);
                    
                    if (!isActive && window.localViewing && window.localViewing[camId]) {
                        window.localViewing[camId] = false;
                        stopStreamUI(camId);
                    }
                    
                    if (!window.localViewing || !window.localViewing[camId]) {
                        if (statusEl) {
                            statusEl.textContent = isActive ? 'En direct' : 'Inactif';
                            statusEl.className = isActive ? 'status-badge active' : 'status-badge';
                        }
                        if (btnText) {
                            btnText.textContent = isActive ? 'Rejoindre le flux' : 'Démarrer le flux';
                        }
                        
                        if (isActive && (!window.userClosedStream || !window.userClosedStream[camId])) {
                            toggleStream(camId);
                        }
                    }
                }
                else if (payload.type === "keep_stream_status") {
                    const camId = parseInt(payload.camera);
                    const isKeep = payload.keep === true;
                    if (!window.keepStreams) window.keepStreams = { 1: false, 2: false };
                    window.keepStreams[camId] = isKeep;
                    const keepBtn = document.getElementById("keep-btn-" + camId);
                    if (keepBtn) {
                        if (isKeep) {
                            keepBtn.classList.add("active");
                            keepBtn.innerHTML = "📌 Keep Stream : ON";
                        } else {
                            keepBtn.classList.remove("active");
                            keepBtn.innerHTML = "📌 Keep Stream : OFF";
                        }
                    }
                
                    }
                }
                else if (payload.type === "wifi_list") {
                    displayWifiNetworks(payload.networks, payload.known_ssids);
                } 
                else if (payload.type === "wifi_connect_result") {
                    handleWifiConnectResult(payload);
                } 
                else if (payload.type === "chat_response" || payload.type === "chat") {
                    appendLLMMessage(payload.sender || 'LLM', payload.text || '');
                }
            } catch(e) {
                // not json or parsing error
            }
        }

        // ─── CHAT TAB IA FUNCTIONS ────────────────────────────────────────────
        
        function sendChatMessage(e) {
            e.preventDefault();
            const input = document.getElementById('chat-tab-input');
            const text = input.value.trim();
            if (!text) return;
            
            appendLLMMessage('Moi', text);
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "chat", text: text }));
            } else {
                appendLLMMessage('Système', 'Erreur : WebSocket déconnecté.');
            }
            
            input.value = '';
        }

        function appendLLMMessage(sender, text) {
            const box = document.getElementById('chat-tab-messages');
            if (!box) return;
            
            if (box.textContent.includes("Aucun message échangé")) {
                box.innerHTML = '';
            }
            
            const msgEl = document.createElement('div');
            msgEl.style.padding = '0.5rem 0.75rem';
            msgEl.style.borderRadius = '6px';
            msgEl.style.fontSize = '0.9rem';
            msgEl.style.maxWidth = '80%';
            msgEl.style.marginBottom = '0.25rem';
            
            if (sender === 'Moi') {
                msgEl.style.alignSelf = 'flex-end';
                msgEl.style.backgroundColor = 'rgba(255, 111, 97, 0.2)';
                msgEl.style.border = '1px solid var(--accent)';
                msgEl.innerHTML = `<span style="font-weight:bold;color: var(--accent);display:block;font-size:0.75rem;">Moi</span>${text}`;
            } else if (sender === 'Système') {
                msgEl.style.alignSelf = 'center';
                msgEl.style.backgroundColor = 'rgba(225, 29, 72, 0.1)';
                msgEl.style.border = '1px solid var(--danger)';
                msgEl.innerHTML = `<span style="font-style:italic;color:#f87171;font-size:0.8rem;">${text}</span>`;
            } else {
                msgEl.style.alignSelf = 'flex-start';
                msgEl.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
                msgEl.style.border = '1px solid var(--border-color)';
                msgEl.innerHTML = `<span style="font-weight:bold;color:var(--text-primary);display:block;font-size:0.75rem;">${sender}</span>${text}`;
            }
            
            box.appendChild(msgEl);
            box.scrollTop = box.scrollHeight;
        }

        function clearJSONConsole() {
            const consoleEl = document.getElementById('json-traffic-console');
            if (consoleEl) consoleEl.textContent = '[Console effacée]';
        }

        function setAIControl(feature, target) {
            const buttons = {
                'tts': ['robot', 'node', 'disabled'],
                'stt': ['robot', 'node', 'disabled'],
                'chat': ['robot', 'node', 'disabled'],
                'yolo': ['enabled', 'disabled'],
                'face_rec': ['enabled', 'disabled']
            };
            
            buttons[feature].forEach(t => {
                const btnId = `${feature}-ctrl-${t}`;
                const btn = document.getElementById(btnId);
                if (btn) {
                    if (t === target) {
                        btn.classList.add('active-control');
                    } else {
                        btn.classList.remove('active-control');
                    }
                }
            });
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "ai_control", feature: feature, target: target }));
            }
        }

        function updateAIControlUI(feature, target) {
            const list = (feature === 'yolo' || feature === 'face_rec') ? ['enabled', 'disabled'] : ['robot', 'node', 'disabled'];
            list.forEach(t => {
                const suffix = (t === 'disabled' && feature !== 'yolo' && feature !== 'face_rec') ? 'off' : t;
                const btnId = `${feature}-ctrl-${suffix}`;
                const btn = document.getElementById(btnId);
                if (btn) {
                    if (t === target) {
                        btn.classList.add('active-control');
                    } else {
                        btn.classList.remove('active-control');
                    }
                }
            });
        }

        // ─── CALIBRATION WINDOW FUNCTIONS ──────────────────────────────────────
        
        function openCalibrationOverlay() {
            document.getElementById('calibration-overlay').classList.add('active');
            setTimeout(drawMinimap, 100);
        }

        function closeCalibrationOverlay() {
            document.getElementById('calibration-overlay').classList.remove('active');
        }

        function updateCalibSliderVal(index) {
            const slider = document.getElementById(`calib-slider-${index}`);
            const label = document.getElementById(`calib-val-${index}`);
            if (slider && label) {
                label.textContent = slider.value >= 0 ? `+${slider.value}` : slider.value;
            }
        }

        function resetMotorCalibration() {
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`calib-slider-${i}`);
                if (slider) {
                    slider.value = 0;
                    updateCalibSliderVal(i);
                }
            }
        }

        async function sendCalibrationOffsets() {
            const offsets = [];
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`calib-slider-${i}`);
                offsets.push(slider ? parseInt(slider.value) : 0);
            }
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));
            } else {
                alert("WebSocket déconnecté.");
            }
        }

        function toggleManualJointControl(checked) {
            window.manualJointControlActive = checked;
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`joint-slider-${i}`);
                if (slider) {
                    slider.disabled = !checked;
                    slider.style.cursor = checked ? 'pointer' : 'not-allowed';
                }
            }
            if (checked) {
                sendManualJointAngles();
            }
        }

        function onJointSliderInput(index, val) {
            const valEl = document.getElementById(`joint-val-${index}`);
            if (valEl) valEl.textContent = `${Math.round(val)}°`;
            sendManualJointAngles();
        }

        function sendManualJointAngles() {
            const angles = [];
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`joint-slider-${i}`);
                angles.push(slider ? parseFloat(slider.value) : 90.0);
            }
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "manual_joint_control", angles: angles }));
            }
        }

        async function saveCalibrationOffsets() {
            const offsets = [];
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`calib-slider-${i}`);
                offsets.push(slider ? parseInt(slider.value) : 0);
            }
            
            try {
                const res = await fetch('/core/calibration', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Token': apiToken
                    },
                    body: JSON.stringify({ offsets: offsets })
                });
                if (res.ok) {
                    alert("Offsets sauvegardés avec succès sur la Gateway.");
                    if (appWs && appWs.readyState === WebSocket.OPEN) {
                        appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));
                    }
                } else {
                    alert("Erreur lors de la sauvegarde.");
                }
            } catch(e) {
                alert("Erreur réseau.");
            }
        }

        function toggleCalibCamera(camId) {
            const checkbox = document.getElementById(`calib-cam-enable-${camId}`);
            const statusEl = document.getElementById(`calib-cam-status-${camId}`);
            if (checkbox && statusEl) {
                statusEl.textContent = checkbox.checked ? 'Activée' : 'Désactivée';
                statusEl.style.color = checkbox.checked ? 'var(--success)' : 'var(--text-secondary)';
            }
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "camera_setup", camera: camId, enable: checkbox.checked }));
            }
        }

        // ─── WIFI POPUP FUNCTIONS ─────────────────────────────────────────────
        
        function openWifiModal() {
            document.getElementById('wifiModal').classList.add('active');
            scanWifiNetworks();
        }

        function closeWifiModal() {
            document.getElementById('wifiModal').classList.remove('active');
            cancelWifiConnection();
        }

        function closeWifiModalOnClick(e) {
            if (e.target === document.getElementById('wifiModal')) closeWifiModal();
        }

        function scanWifiNetworks() {
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            
            listContainer.innerHTML = `
                <div style="text-align: center; color: var(--text-secondary); padding: 2.5rem 0; font-size: 0.85rem; display: flex; flex-direction: column; align-items: center; gap: 0.75rem;">
                    <div style="width: 24px; height: 24px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                    <span>Recherche des réseaux à proximité (nmcli)...</span>
                </div>`;
                
            knownContainer.innerHTML = `
                <div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.85rem; display: flex; flex-direction: column; align-items: center; gap: 0.5rem;">
                    <div style="width: 16px; height: 16px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                    <span>Actualisation...</span>
                </div>`;
                
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = true;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "scan_wifi" }));
            } else {
                listContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 2rem 0; font-size: 0.85rem;">Erreur : WebSocket déconnecté.</div>`;
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 1rem 0; font-size: 0.85rem;">Erreur.</div>`;
                if (btn) btn.disabled = false;
            }
        }

        function displayWifiNetworks(networks, knownSsids = []) {
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = false;
            
            listContainer.innerHTML = '';
            knownContainer.innerHTML = '';
            
            if (!knownSsids) knownSsids = [];
            if (!networks) networks = [];
            
            // Sort by signal strength (highest first)
            networks.sort((a, b) => {
                const sigA = parseInt(a.signal) || 0;
                const sigB = parseInt(b.signal) || 0;
                return sigB - sigA;
            });
            
            // Display known networks
            if (knownSsids.length === 0) {
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.8rem;">Aucun réseau enregistré configuré sur le robot.</div>`;
            } else {
                knownSsids.forEach(ssid => {
                    const scannedNet = networks.find(n => n.ssid === ssid);
                    const inRange = !!scannedNet;
                    
                    const item = document.createElement('div');
                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease; margin-bottom: 0.25rem; border-radius: 6px;';
                    item.style.backgroundColor = 'rgba(255, 111, 97, 0.05)';
                    item.style.border = '1px solid rgba(255, 111, 97, 0.2)';
                    
                    const signalText = inRange ? `${scannedNet.signal}%` : 'Hors de portée';
                    const signalColor = inRange ? 'var(--success)' : 'var(--text-secondary)';
                    
                    item.innerHTML = `
                        <div>
                            <span style="font-weight: 600; font-size: 0.9rem; display: block; color: var(--accent);">${ssid} <span style="font-size:0.65rem; background:rgba(255,111,97,0.2); color: var(--success); padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem;">Enregistré</span></span>
                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${inRange ? (scannedNet.bssid + ' • ' + scannedNet.security) : 'Profil de connexion sauvegardé'}</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <span style="font-size: 0.85rem; font-weight: bold; color: ${signalColor};">${signalText}</span>
                        </div>
                    `;
                    
                    const isSecureNet = scannedNet ? (scannedNet.security && scannedNet.security.trim() !== "" && scannedNet.security !== "--" && scannedNet.security.toLowerCase() !== "open") : true;
                    item.onclick = () => selectWifiNetwork(ssid, isSecureNet, true);
                    knownContainer.appendChild(item);
                });
            }
            
            // Display other scanned networks (excluding the known ones)
            const otherNetworks = networks.filter(n => !knownSsids.includes(n.ssid));
            
            if (otherNetworks.length === 0) {
                listContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1.5rem 0; font-size: 0.8rem;">Aucun autre réseau WiFi à proximité.</div>`;
            } else {
                otherNetworks.forEach(net => {
                    const item = document.createElement('div');
                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease;';
                    
                    const isSecure = net.security && net.security.trim() !== "" && net.security !== "--" && net.security.toLowerCase() !== "open";
                    const lockIcon = isSecure ? '🔒' : '🔓';
                    
                    item.innerHTML = `
                        <div>
                            <span style="font-weight: 600; font-size: 0.9rem; display: block;">${net.ssid}</span>
                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${net.bssid} • ${net.security}</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <span style="font-size: 0.8rem;">${lockIcon}</span>
                            <span style="font-size: 0.85rem; font-weight: bold; color: var(--accent);">${net.signal}%</span>
                        </div>
                    `;
                    
                    item.onclick = () => selectWifiNetwork(net.ssid, isSecure);
                    listContainer.appendChild(item);
                });
            }
        }

        function selectWifiNetwork(ssid, isSecure, isKnown = false) {
            document.getElementById('form-wifi-ssid').value = ssid;
            document.getElementById('wifi-selected-ssid-label').textContent = ssid;
            
            const pwdGroup = document.getElementById('wifi-password-group');
            const pwdInput = document.getElementById('form-wifi-password');
            if (isSecure) {
                pwdGroup.style.display = 'block';
                if (isKnown) {
                    pwdInput.placeholder = 'Laisser vide pour utiliser le mot de passe enregistré';
                } else {
                    pwdInput.placeholder = 'Mot de passe';
                }
            } else {
                pwdGroup.style.display = 'none';
                pwdInput.placeholder = '';
            }
            
            pwdInput.value = '';
            document.getElementById('wifi-connect-form').style.display = 'block';
        }

        function cancelWifiConnection() {
            document.getElementById('wifi-connect-form').style.display = 'none';
        }

        function handleWifiConnectSubmit(e) {
            e.preventDefault();
            const ssid = document.getElementById('form-wifi-ssid').value;
            const password = document.getElementById('form-wifi-password').value;
            
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Connexion en cours...';
            }
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({
                    type: "connect_wifi",
                    ssid: ssid,
                    password: password
                }));
            } else {
                alert("WebSocket déconnecté.");
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Se connecter au WiFi';
                }
            }
        }

        function handleWifiConnectResult(res) {
            const submitBtn = document.querySelector('#wifi-connect-form button[type="submit"]');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Se connecter au WiFi';
            }
            
            if (res.status === 'success') {
                alert("Succès : " + res.message);
                closeWifiModal();
            } else {
                alert("Erreur de connexion : " + res.message);
            }
        }

        // ─── CANVASES RENDER CODES ───────────────────────────────────────────
        
        function drawMinimap() {
            const canvas = document.getElementById('minimap-canvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            
            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;
            ctx.scale(dpr, dpr);
            
            const w = rect.width;
            const h = rect.height;
            
            ctx.clearRect(0, 0, w, h);
            
            ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim();
            ctx.lineWidth = 1;
            const step = 20;
            for (let x = 0; x < w; x += step) {
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
            }
            for (let y = 0; y < h; y += step) {
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
            }
            
            ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--border-color').trim();
            ctx.beginPath();
            ctx.moveTo(w/2, 0); ctx.lineTo(w/2, h);
            ctx.moveTo(0, h/2); ctx.lineTo(w, h/2);
            ctx.stroke();
            
            const scale = 30; // px/m
            const cx = w / 2;
            const cy = h / 2;
            
            // Path
            if (window.slamPath && window.slamPath.length > 0) {
                ctx.strokeStyle = 'rgba(99, 102, 241, 0.6)';
                ctx.lineWidth = 2;
                ctx.beginPath();
                window.slamPath.forEach((pt, idx) => {
                    const px = cx + pt.x * scale;
                    const py = cy - pt.y * scale;
                    if (idx === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                });
                ctx.stroke();
            }
            
            // Robot
            const rx = cx + window.robotPose.x * scale;
            const ry = cy - window.robotPose.y * scale;
            const rtheta = -window.robotPose.theta;
            
            ctx.save();
            ctx.translate(rx, ry);
            ctx.rotate(rtheta);
            
            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
            ctx.beginPath();
            ctx.moveTo(12, 0);
            ctx.lineTo(-8, -8);
            ctx.lineTo(-4, 0);
            ctx.lineTo(-8, 8);
            ctx.closePath();
            ctx.fill();
            
            ctx.strokeStyle = 'rgba(99, 102, 241, 0.4)';
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.arc(0, 0, 10 + (Date.now() % 1000) / 100, 0, Math.PI * 2);
            ctx.stroke();
            
            ctx.restore();
        }

        function drawSLAMMap() {
            const canvas = document.getElementById('slam-map-canvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            
            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;
            ctx.scale(dpr, dpr);
            
            const w = rect.width;
            const h = rect.height;
            
            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = '#07070a';
            ctx.fillRect(0, 0, w, h);
            
            const scale = 40;
            const cx = w / 2;
            const cy = h / 2;
            
            // Grid
            if (document.getElementById('layer-grid').checked) {
                ctx.strokeStyle = '#101015';
                ctx.lineWidth = 0.5;
                const gridStep = scale * 0.5;
                for (let x = cx % gridStep; x < w; x += gridStep) {
                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
                }
                for (let y = cy % gridStep; y < h; y += gridStep) {
                    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
                }
                
                ctx.fillStyle = 'rgba(255, 255, 255, 0.05)';
                const walls = [
                    {x: -1.5, y: -2, w: 3, h: 0.1},
                    {x: -1.5, y: 2, w: 3, h: 0.1},
                    {x: -1.5, y: -2, w: 0.1, h: 4},
                    {x: 1.5, y: -2, w: 0.1, h: 4},
                    {x: 0.5, y: -0.5, w: 0.5, h: 1}
                ];
                walls.forEach(wall => {
                    ctx.fillRect(cx + wall.x * scale, cy - (wall.y + wall.h) * scale, wall.w * scale, wall.h * scale);
                });
            }
            
            // Points (laser)
            if (document.getElementById('layer-points').checked) {
                ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--success').trim();
                if (window.slamPoints && window.slamPoints.length > 0) {
                    window.slamPoints.forEach(pt => {
                        ctx.beginPath();
                        ctx.arc(cx + pt.x * scale, cy - pt.y * scale, 1.5, 0, Math.PI * 2);
                        ctx.fill();
                    });
                } else {
                    for (let angle = 0; angle < Math.PI * 2; angle += 0.05) {
                        const dist = 1.8 + Math.sin(angle * 4) * 0.1;
                        const px = cx + Math.cos(angle) * dist * scale;
                        const py = cy - Math.sin(angle) * dist * scale;
                        ctx.beginPath();
                        ctx.arc(px, py, 1.5, 0, Math.PI*2);
                        ctx.fill();
                    }
                }
            }
            
            // Sonar
            if (document.getElementById('layer-sonar').checked) {
                ctx.fillStyle = 'rgba(245, 158, 11, 0.15)';
                ctx.strokeStyle = '#f59e0b';
                ctx.lineWidth = 1;
                
                const rx = cx + window.robotPose.x * scale;
                const ry = cy - window.robotPose.y * scale;
                const rtheta = -window.robotPose.theta;
                
                ctx.save();
                ctx.translate(rx, ry);
                ctx.rotate(rtheta);
                ctx.beginPath();
                ctx.moveTo(0, 0);
                ctx.arc(0, 0, 1.2 * scale, -Math.PI / 12, Math.PI / 12);
                ctx.closePath();
                ctx.fill();
                ctx.stroke();
                ctx.restore();
            }
            
            // Trajectory Path
            if (document.getElementById('layer-trajectory').checked && window.slamPath && window.slamPath.length > 0) {
                ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
                ctx.lineWidth = 2.5;
                ctx.beginPath();
                window.slamPath.forEach((pt, idx) => {
                    const px = cx + pt.x * scale;
                    const py = cy - pt.y * scale;
                    if (idx === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                });
                ctx.stroke();
            }
            
            // Robot Outline
            const rx = cx + window.robotPose.x * scale;
            const ry = cy - window.robotPose.y * scale;
            const rtheta = -window.robotPose.theta;
            
            ctx.save();
            ctx.translate(rx, ry);
            ctx.rotate(rtheta);
            
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 2;
            ctx.strokeRect(-12, -8, 24, 16);
            
            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
            ctx.beginPath();
            ctx.moveTo(12, 0);
            ctx.lineTo(6, -5);
            ctx.lineTo(6, 5);
            ctx.closePath();
            ctx.fill();
            
            ctx.restore();
        }

        function resetSLAMMap() {
            window.robotPose = {x: 0, y: 0, theta: 0};
            window.slamPath = [];
            window.slamPoints = [];
            drawSLAMMap();
        }

        function updateSLAMParam(param) {
            const slider = document.getElementById(`param-slider-${param}`);
            const label = document.getElementById(`param-val-${param}`);
            if (slider && label) {
                if (param === 'resolution') {
                    label.textContent = `${(slider.value / 100).toFixed(2)}m`;
                } else if (param === 'inflation') {
                    label.textContent = `${(slider.value / 100).toFixed(2)}m`;
                } else {
                    label.textContent = `${slider.value}%`;
                }
            }
        }

        function saveSLAMParameters() {
            alert("Paramètres SLAM appliqués temporairement au visualiseur.");
            drawSLAMMap();
        }

        // Periodic drawing loop
        setInterval(() => {
            if (activeTab === 'diagnostics') {
                drawMinimap();
            } else if (activeTab === 'map') {
                drawSLAMMap();
            }
        }, 250);

        // ─── MOBILE SIDEBAR ACTIONS ───────────────────────────────────────────

        function toggleSidebar() {
            const sidebar = document.querySelector('.sidebar');
            const overlay = document.querySelector('.sidebar-overlay');
            if (sidebar && overlay) {
                sidebar.classList.toggle('active');
                overlay.classList.toggle('active');
            }
        }

        function closeSidebar() {
            const sidebar = document.querySelector('.sidebar');
            const overlay = document.querySelector('.sidebar-overlay');
            if (sidebar && overlay) {
                sidebar.classList.remove('active');
                overlay.classList.remove('active');
            }
        }

        // ─── TABS SWITCHING ───────────────────────────────────────────────────

        function switchTab(tabId) {
            closeSidebar();
            
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
                'system': { title: "Système & Updates", subtitle: "Suivi des mises à jour logicielles et des services ROS." },
                'chat': { title: "Chat & Contrôle IA", subtitle: "Dialogue temps réel avec le robot et supervision de l'IA." },
                'diagnostics': { title: "Arduino & Calib", subtitle: "Télémétrie des moteurs, gyroscope de l'IMU et calibrages." },
                'map': { title: "SLAM & Map", subtitle: "Navigation cartographique, nuage de points et paramètres d'évitement." }
            };

            const headerInfo = titles[tabId] || titles['dashboard'];
            document.getElementById('tab-title').textContent = headerInfo.title;
            document.getElementById('tab-subtitle').textContent = headerInfo.subtitle;

            if (tabId === 'users') {
                loadAccounts();
            } else if (tabId === 'faces') {
                closeFolderDetails();
                loadFacesGallery();
            } else if (tabId === 'diagnostics') {
                setTimeout(drawMinimap, 100);
            } else if (tabId === 'map') {
                setTimeout(drawSLAMMap, 100);
            }
        }

        // ─── TELEMETRY ────────────────────────────────────────────────────────

        async function fetchTelemetry() {
            try {
                const res = await fetch('/core/state', { headers: { 'X-API-Token': apiToken } });
                if (res.status === 403) { logout(); return; }
                if (res.ok) {
                    const state = await res.json();
                    window.lastTelemetryState = state;
                    
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

                    const sensors = state.sensors || {};
                    const cpu = sensors.cpu_percent || 0;
                    const ram = sensors.ram_percent || 0;
                    const temp = sensors.temp_c || 0;

                    updateGaugeCircle('gauge-cpu', cpu);
                    document.getElementById('gauge-cpu-val').textContent = `${Math.round(cpu)}%`;

                    updateGaugeCircle('gauge-ram', ram);
                    document.getElementById('gauge-ram-val').textContent = `${Math.round(ram)}%`;

                    updateGaugeCircle('gauge-temp', (temp / 100) * 100);
                    document.getElementById('gauge-temp-val').textContent = `${Math.round(temp)}°C`;

                    document.getElementById('sensor-seen-person').textContent = state.seen_person || 'Personne';
                    document.getElementById('sensor-seen-objects').textContent = (state.seen_objects && state.seen_objects.length > 0) ? state.seen_objects.join(', ') : 'Aucun';
                    document.getElementById('sensor-version').textContent = state.robot_version || 'v0.0.0';
                    
                    if (state.camera_mapping) {
                        const selectLeft = document.getElementById('cam-port-left');
                        const selectRight = document.getElementById('cam-port-right');
                        if (selectLeft && state.camera_mapping.left && selectLeft.value !== state.camera_mapping.left) {
                            selectLeft.value = state.camera_mapping.left;
                        }
                        if (selectRight && state.camera_mapping.right && selectRight.value !== state.camera_mapping.right) {
                            selectRight.value = state.camera_mapping.right;
                        }
                    }
                    
                    if (state.ai_state) {
                        updateAIControlUI('tts', state.ai_state.tts);
                        updateAIControlUI('stt', state.ai_state.stt);
                        updateAIControlUI('chat', state.ai_state.chat);
                        updateAIControlUI('yolo', state.ai_state.yolo);
                        updateAIControlUI('face_rec', state.ai_state.face_rec);
                    }

                    // Cameras connection & auto-enable
                    const cam1Status = document.getElementById('calib-cam-status-1');
                    const cam1Enable = document.getElementById('calib-cam-enable-1');
                    const cam2Status = document.getElementById('calib-cam-status-2');
                    const cam2Enable = document.getElementById('calib-cam-enable-2');

                    const activeStreams = state.active_streams || { "1": false, "2": false };
                    for (let camId of [1, 2]) {
                        const isActive = activeStreams[camId] === true;
                        
                        const wasActive = window.activeStreams ? window.activeStreams[camId] : false;
                        if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };
                        window.activeStreams[camId] = isActive;
                        
                        if (isActive && !wasActive) {
                            if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                            window.userClosedStream[camId] = false;
                        }

                        const statusEl = document.getElementById(`stream-status-${camId}`);
                        const btnText = document.getElementById(`stream-btn-text-${camId}`);
                        
                        if (!isActive && window.localViewing && window.localViewing[camId]) {
                            window.localViewing[camId] = false;
                            stopStreamUI(camId);
                        }
                        
                        if (!window.localViewing || !window.localViewing[camId]) {
                            if (statusEl) {
                                statusEl.textContent = isActive ? 'En direct' : 'Inactif';
                                statusEl.className = isActive ? 'status-badge active' : 'status-badge';
                            }
                            if (btnText) {
                                btnText.textContent = isActive ? 'Rejoindre le flux' : 'Démarrer le flux';
                            }
                            
                            if (isActive && (!window.userClosedStream || !window.userClosedStream[camId])) {
                                toggleStream(camId);
                            }
                        }
                    }

                    const cam1Connected = sensors.cam1_connected === true;
                    const cam2Connected = sensors.cam2_connected === true;

                    updateCameraModularity(cam1Connected, cam2Connected);

                    if (cam1Status) {
                        cam1Status.textContent = cam1Connected ? 'Connectée' : 'Déconnectée';
                        cam1Status.style.color = cam1Connected ? 'var(--success)' : 'var(--text-secondary)';
                    }
                    if (cam2Status) {
                        cam2Status.textContent = cam2Connected ? 'Connectée' : 'Déconnectée';
                        cam2Status.style.color = cam2Connected ? 'var(--success)' : 'var(--text-secondary)';
                    }

                    if (window.lastCam1Connected === undefined) window.lastCam1Connected = false;
                    if (window.lastCam2Connected === undefined) window.lastCam2Connected = false;

                    if (cam1Connected && !window.lastCam1Connected) {
                        if (cam1Enable && !cam1Enable.checked) {
                            cam1Enable.checked = true;
                            toggleCalibCamera(1);
                        }
                    }
                    if (cam2Connected && !window.lastCam2Connected) {
                        if (cam2Enable && !cam2Enable.checked) {
                            cam2Enable.checked = true;
                            toggleCalibCamera(2);
                        }
                    }

                    window.lastCam1Connected = cam1Connected;
                    window.lastCam2Connected = cam2Connected;
                    
                    if (state.updated_at) {
                        const date = new Date(state.updated_at * 1000);
                        document.getElementById('sensor-last-seen').textContent = date.toLocaleTimeString();
                    } else {
                        document.getElementById('sensor-last-seen').textContent = '--';
                    }

                    const serviceBadge = document.getElementById('spotbot-service-badge');
                    const isSpotbotActive = sensors.spotbot_service_active;
                    const btnStart = document.getElementById('btn-start-spotbot');
                    const btnStop = document.getElementById('btn-stop-spotbot');
                    const btnRestart = document.getElementById('btn-restart-spotbot');

                    if (isSpotbotActive === true) {
                        serviceBadge.textContent = 'Actif';
                        serviceBadge.className = 'status-badge active';
                        if (btnStart) btnStart.style.display = 'none';
                        if (btnStop) btnStop.style.display = '';
                        if (btnRestart) btnRestart.style.display = '';
                    } else if (isSpotbotActive === false) {
                        serviceBadge.textContent = 'Arrêté';
                        serviceBadge.className = 'status-badge offline';
                        if (btnStart) btnStart.style.display = '';
                        if (btnStop) btnStop.style.display = 'none';
                        if (btnRestart) btnRestart.style.display = 'none';
                    } else {
                        serviceBadge.textContent = 'Inconnu';
                        serviceBadge.className = 'status-badge';
                    }
                }
            } catch (e) {
                console.error("Telemetry fetch error:", e);
            }
        }

        function updateGaugeCircle(id, val) {
            const el = document.getElementById(id);
            if (!el) return;
            const cappedVal = Math.max(0, Math.min(100, val));
            el.setAttribute('stroke-dasharray', `${cappedVal}, 100`);
        }

        // ─── CAMERA STREAM ON-DEMAND ─────────────────────────────────────────

        function updateCameraModularity(cam1Connected, cam2Connected) {
            const card1 = document.getElementById('stream-card-1');
            const card2 = document.getElementById('stream-card-2');
            if (card1) card1.style.display = cam1Connected ? 'flex' : 'none';
            if (card2) card2.style.display = cam2Connected ? 'flex' : 'none';
            
            const calibCam1Container = document.getElementById('calib-cam-container-1');
            const calibCam2Container = document.getElementById('calib-cam-container-2');
            if (calibCam1Container) calibCam1Container.style.display = cam1Connected ? 'flex' : 'none';
            if (calibCam2Container) calibCam2Container.style.display = cam2Connected ? 'flex' : 'none';
            
            const vslamSpan = document.getElementById('vslam-text-mode');
            if (vslamSpan) {
                vslamSpan.textContent = cam2Connected ? 'Superposer V-SLAM Stéréo' : 'Superposer V-SLAM Mono';
            }
        }

        function stopStreamUI(camId) {
            const placeholder = document.getElementById(`stream-placeholder-${camId}`);
            const statusEl = document.getElementById(`stream-status-${camId}`);
            const btnText = document.getElementById(`stream-btn-text-${camId}`);
            const videoContainer = document.getElementById(`video-container-${camId}`);
            const videoEl = document.getElementById(`video-cam-${camId}`);
            const loaderEl = document.getElementById(`stream-loader-${camId}`);
            const fsBtn = document.getElementById(`video-fs-btn-${camId}`);

            if (window.hlsInstances && window.hlsInstances[camId]) {
                try { window.hlsInstances[camId].destroy(); } catch(e) {}
                delete window.hlsInstances[camId];
            }

            if (peerConnections[camId]) {
                try {
                    peerConnections[camId].close();
                } catch(e) {}
                peerConnections[camId] = null;
            }

            if (videoEl) {
                videoEl.srcObject = null;
                videoEl.src = '';
                videoEl.removeAttribute('src');
                videoEl.style.display = 'none';
            }
            if (videoContainer) {
                videoContainer.style.display = 'none';
            }
            if (loaderEl) {
                loaderEl.style.display = 'none';
            }
            if (fsBtn) {
                fsBtn.style.display = 'none';
            }

            placeholder.style.display = 'flex';
            const isActive = window.activeStreams && window.activeStreams[camId];
            statusEl.textContent = isActive ? 'En direct' : 'Inactif';
            statusEl.className = isActive ? 'status-badge active' : 'status-badge';
            btnText.textContent = isActive ? 'Rejoindre le flux' : 'Démarrer le flux';
            window.localViewing[camId] = false;
        }

        function playHLSStream(videoEl, camId, onPlay, onError, customKey) {
            const hlsUrl = `${window.location.protocol}//${window.location.hostname}:48888/robot/cam${camId}/index.m3u8`;
            const hlsKey = customKey || camId;
            
            if (!window.hlsInstances) window.hlsInstances = {};
            if (window.hlsInstances[hlsKey]) {
                try { window.hlsInstances[hlsKey].destroy(); } catch(e) {}
                delete window.hlsInstances[hlsKey];
            }
            
            if (Hls.isSupported()) {
                const hls = new Hls({
                    maxBufferSize: 0,
                    maxBufferLength: 0.5,
                    liveSyncDuration: 0.5,
                    liveMaxLatencyDuration: 1.5,
                    enableWorker: true,
                    lowLatencyMode: true
                });
                window.hlsInstances[hlsKey] = hls;
                hls.loadSource(hlsUrl);
                hls.attachMedia(videoEl);
                hls.on(Hls.Events.MANIFEST_PARSED, function() {
                    videoEl.play().then(onPlay).catch(e => {
                        console.warn(e);
                        onPlay();
                    });
                });
                hls.on(Hls.Events.ERROR, function(event, data) {
                    if (data.fatal) {
                        switch(data.type) {
                            case Hls.ErrorTypes.NETWORK_ERROR:
                                console.warn("HLS Network error, retrying...", data);
                                hls.startLoad();
                                break;
                            case Hls.ErrorTypes.MEDIA_ERROR:
                                console.warn("HLS Media error, recovering...", data);
                                hls.recoverMediaError();
                                break;
                            default:
                                console.error("HLS Fatal error:", data);
                                if (onError) onError(data);
                                break;
                        }
                    }
                });
            } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
                videoEl.src = hlsUrl;
                videoEl.addEventListener('loadedmetadata', function() {
                    videoEl.play().then(onPlay).catch(e => {
                        console.warn(e);
                        onPlay();
                    });
                });
            } else {
                if (onError) onError("HLS not supported in this browser");
            }
        }

        function startStreamHLS(camId) {
            const placeholder = document.getElementById(`stream-placeholder-${camId}`);
            const statusEl = document.getElementById(`stream-status-${camId}`);
            const btnText = document.getElementById(`stream-btn-text-${camId}`);
            const videoContainer = document.getElementById(`video-container-${camId}`);
            const videoEl = document.getElementById(`video-cam-${camId}`);
            const loaderEl = document.getElementById(`stream-loader-${camId}`);
            const fsBtn = document.getElementById(`video-fs-btn-${camId}`);

            statusEl.textContent = 'Connexion (HLS)…';
            statusEl.className = 'status-badge';
            placeholder.style.display = 'none';
            videoContainer.style.display = 'block';
            videoEl.style.display = 'none';
            fsBtn.style.display = 'none';
            loaderEl.style.display = 'flex';

            playHLSStream(
                videoEl,
                camId,
                () => {
                    loaderEl.style.display = 'none';
                    videoEl.style.display = 'block';
                    fsBtn.style.display = 'block';
                    statusEl.textContent = 'En direct (HLS)';
                    statusEl.className = 'status-badge active';
                    btnText.textContent = 'Couper Caméra';
                },
                (err) => {
                    loaderEl.style.display = 'none';
                    statusEl.textContent = 'Erreur connexion';
                    statusEl.className = 'status-badge error';
                    btnText.textContent = 'Réessayer';
                    window.activeStreams[camId] = false;
                    placeholder.style.display = 'flex';
                    videoContainer.style.display = 'none';
                }
            );
        }

        async function startStreamWebRTC(camId) {
            const placeholder = document.getElementById(`stream-placeholder-${camId}`);
            const statusEl = document.getElementById(`stream-status-${camId}`);
            const btnText = document.getElementById(`stream-btn-text-${camId}`);
            const videoContainer = document.getElementById(`video-container-${camId}`);
            const videoEl = document.getElementById(`video-cam-${camId}`);
            const loaderEl = document.getElementById(`stream-loader-${camId}`);
            const fsBtn = document.getElementById(`video-fs-btn-${camId}`);

            if (peerConnections[camId]) {
                try {
                    peerConnections[camId].close();
                } catch(e) {}
                peerConnections[camId] = null;
            }

            statusEl.textContent = 'Démarrage…';
            statusEl.className = 'status-badge';
            placeholder.style.display = 'none';
            videoContainer.style.display = 'block';
            videoEl.style.display = 'none';
            fsBtn.style.display = 'none';
            loaderEl.style.display = 'flex';

            let pc = null;
            let fallbackTriggered = false;
            const triggerHLSFallback = () => {
                if (fallbackTriggered) return;
                fallbackTriggered = true;
                console.warn(`WebRTC failed or timed out for cam${camId}, trying HLS fallback...`);
                if (pc) {
                    if (peerConnections[camId] === pc) {
                        peerConnections[camId] = null;
                    }
                    try { pc.close(); } catch(e) {}
                }
                startStreamHLS(camId);
            };

            try {
                pc = new RTCPeerConnection({
                    iceServers: []
                });
                peerConnections[camId] = pc;

                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    triggerHLSFallback();
                }, 4000);

                pc.oniceconnectionstatechange = () => {
                    console.log(`WebRTC ICE state cam${camId}: ${pc.iceConnectionState}`);
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        triggerHLSFallback();
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
                    if (fallbackTriggered) return;

                    if (event.streams && event.streams[0]) {
                        videoEl.srcObject = event.streams[0];
                    } else {
                        const inboundStream = new MediaStream();
                        inboundStream.addTrack(event.track);
                        videoEl.srcObject = inboundStream;
                    }
                    videoEl.play().catch(e => console.warn("Video play failed:", e));
                    
                    loaderEl.style.display = 'none';
                    videoEl.style.display = 'block';
                    fsBtn.style.display = 'block';
                    statusEl.textContent = 'En direct';
                    statusEl.className = 'status-badge active';
                };

                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;
                
                let response = null;
                let retries = 15;
                while (retries > 0 && window.localViewing[camId]) {
                    try {
                        response = await fetch(webrtcUrl, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/sdp' },
                            body: pc.localDescription.sdp
                        });
                        if (response.ok) break;
                    } catch (e) {
                        console.warn(`Signaling failed: ${e.message}`);
                    }
                    retries--;
                    if (retries > 0) {
                        await new Promise(r => setTimeout(r, 200));
                    }
                }

                if (!response || !response.ok) {
                    throw new Error(`MediaMTX WHEP stream cam${camId} not ready/reachable.`);
                }

                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({
                    type: 'answer',
                    sdp: answerSdp
                }));

            } catch (err) {
                console.error("WHEP error, falling back to HLS:", err);
                triggerHLSFallback();
            }
        }

        function saveCameraPortsMapping() {
            const left = document.getElementById('cam-port-left').value;
            const right = document.getElementById('cam-port-right').value;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({
                    type: "save_camera_mapping",
                    left: left,
                    right: right
                }));
                showToast("Configuration des ports caméra envoyée au robot (Redémarrage ROS en cours)...");
            }
        }

        window.mccCurrentCamId = 1;
        let mccPeerConnection = null;

        function openCameraCalibModal(camId) {
            window.mccCurrentCamId = camId;
            document.getElementById('mcc-modal-title').textContent = `Calibration Caméra ${camId === 1 ? 'Gauche (1)' : 'Droite (2)'}`;
            
            const videoEl = document.getElementById('mcc-cam-video');
            const hudEl = document.getElementById('mcc-cam-hud');
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const btnRun = document.getElementById('btn-mcc-run-calib');
            
            if (videoEl.srcObject) {
                videoEl.srcObject.getTracks().forEach(t => t.stop());
                videoEl.srcObject = null;
            }
            videoEl.style.display = 'none';
            hudEl.style.display = 'none';
            overlayEl.style.display = 'flex';
            overlayEl.style.backgroundColor = 'rgba(9,9,11,0.85)';
            statusText.innerHTML = `<span>Cliquez sur Lancer pour vous connecter à la caméra.</span>`;
            
            btnRun.disabled = false;
            btnRun.innerHTML = `<span>📷 Lancer la Caméra</span>`;
            btnRun.onclick = () => runIndividualCameraCalib();
            
            document.getElementById('cameraCalibModal').classList.add('active');
        }

        function closeCameraCalibModal() {
            document.getElementById('cameraCalibModal').classList.remove('active');
            
            const videoEl = document.getElementById('mcc-cam-video');
            if (window.hlsInstances && window.hlsInstances['calib']) {
                try { window.hlsInstances['calib'].destroy(); } catch(e) {}
                delete window.hlsInstances['calib'];
            }
            if (videoEl) {
                if (videoEl.srcObject) {
                    videoEl.srcObject.getTracks().forEach(t => t.stop());
                    videoEl.srcObject = null;
                }
                videoEl.src = '';
                videoEl.removeAttribute('src');
            }
            if (mccPeerConnection) {
                mccPeerConnection.close();
                mccPeerConnection = null;
            }
            
            const camId = window.mccCurrentCamId;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "stop_camera", camera: camId }));
            }
        }

        function closeCameraCalibModalOnClick(e) {
            if (e.target === document.getElementById('cameraCalibModal')) {
                closeCameraCalibModal();
            }
        }

        async function runIndividualCameraCalib() {
            const camId = window.mccCurrentCamId;
            const videoEl = document.getElementById('mcc-cam-video');
            const hudEl = document.getElementById('mcc-cam-hud');
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const btnRun = document.getElementById('btn-mcc-run-calib');
            
            btnRun.disabled = true;
            btnRun.innerHTML = `<span>📷 Connexion...</span>`;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "request_camera", camera: camId, v_slam: false }));
            }
            
            statusText.innerHTML = `
                <div style="width:20px; height:20px; border:2px solid var(--accent); border-top-color:transparent; border-radius:50%; animation:spin 1s linear infinite; margin:0 auto 0.5rem;"></div>
                <span>Initialisation flux WebRTC caméra...</span>
            `;
            
            let pc = null;
            let fallbackTriggered = false;
            const triggerHLSFallback = () => {
                if (fallbackTriggered) return;
                fallbackTriggered = true;
                console.warn(`WebRTC failed or timed out for calibration cam${camId}, trying HLS fallback...`);
                if (pc) {
                    if (mccPeerConnection === pc) {
                        mccPeerConnection = null;
                    }
                    try { pc.close(); } catch(e) {}
                }
                
                playHLSStream(
                    videoEl,
                    camId,
                    () => {
                        overlayEl.style.display = 'none';
                        videoEl.style.display = 'block';
                        hudEl.style.display = 'block';
                        
                        btnRun.disabled = false;
                        btnRun.innerHTML = `<span>📷 Capturer & Calibrer</span>`;
                        btnRun.onclick = () => confirmIndividualCameraCalib();
                    },
                    (err) => {
                        videoEl.style.display = 'none';
                        hudEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        statusText.innerHTML = `
                            <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
                            <span style="color:var(--danger); font-weight:bold;">Échec : Flux vidéo indisponible.</span><br/>
                            <span style="font-size:0.75rem; color:var(--text-secondary);">Vérifiez le branchement ou que le service spotbot est démarré.</span>
                        `;
                        btnRun.disabled = false;
                        btnRun.innerHTML = `<span>📷 Lancer la Caméra</span>`;
                        btnRun.onclick = () => runIndividualCameraCalib();
                    },
                    'calib'
                );
            };

            try {
                if (mccPeerConnection) {
                    mccPeerConnection.close();
                }
                pc = new RTCPeerConnection({ iceServers: [] });
                mccPeerConnection = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });
                
                let trackTimeout = setTimeout(() => {
                    triggerHLSFallback();
                }, 4000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        triggerHLSFallback();
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
                    if (fallbackTriggered) return;

                    if (event.streams && event.streams[0]) {
                        videoEl.srcObject = event.streams[0];
                    } else {
                        const inboundStream = new MediaStream();
                        inboundStream.addTrack(event.track);
                        videoEl.srcObject = inboundStream;
                    }
                    videoEl.play().catch(e => console.warn(e));
                    
                    overlayEl.style.display = 'none';
                    videoEl.style.display = 'block';
                    hudEl.style.display = 'block';
                    
                    btnRun.disabled = false;
                    btnRun.innerHTML = `<span>📷 Capturer & Calibrer</span>`;
                    btnRun.onclick = () => confirmIndividualCameraCalib();
                };
                
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                
                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;
                
                let response = null;
                let retries = 15;
                while (retries > 0 && document.getElementById('cameraCalibModal').classList.contains('active')) {
                    try {
                        response = await fetch(webrtcUrl, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/sdp' },
                            body: pc.localDescription.sdp
                        });
                        if (response.ok) break;
                    } catch (e) {
                        console.warn(e);
                    }
                    retries--;
                    if (retries > 0) {
                        await new Promise(r => setTimeout(r, 200));
                    }
                }
                
                if (!response || !response.ok) {
                    throw new Error("WHEP stream not ready");
                }
                
                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({
                    type: 'answer',
                    sdp: answerSdp
                }));
                
            } catch (err) {
                console.error(err);
                triggerHLSFallback();
            }
        }
        
        function confirmIndividualCameraCalib() {
            const btnRun = document.getElementById('btn-mcc-run-calib');
            btnRun.disabled = true;
            btnRun.innerHTML = `<span>📷 Analyse...</span>`;
            
            const overlayEl = document.getElementById('mcc-cam-status-overlay');
            const statusText = document.getElementById('mcc-cam-status-text');
            const hudEl = document.getElementById('mcc-cam-hud');
            const videoEl = document.getElementById('mcc-cam-video');
            
            let progress = 0;
            const progressInterval = setInterval(() => {
                progress += 25;
                if (progress >= 100) {
                    clearInterval(progressInterval);
                    
                    const camId = window.mccCurrentCamId;
                    const isCameraConnected = window.lastTelemetryState && window.lastTelemetryState.sensors && 
                        window.lastTelemetryState.sensors[`cam${camId}_connected`] === true;
                        
                    if (isCameraConnected) {
                        hudEl.style.display = 'none';
                        videoEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        overlayEl.style.backgroundColor = 'rgba(9,9,11,0.9)';
                        statusText.innerHTML = `
                            <span style="font-size: 2rem; color: var(--success); display:block; margin-bottom:0.5rem;">✓</span>
                            <span style="color:var(--success); font-weight:bold; font-size:1.05rem;">Calibration réussie !</span><br/>
                            <span style="font-size:0.8rem; color:var(--text-secondary); margin-top:0.25rem; display:block;">Les paramètres intrinsèques ont été sauvegardés.</span>
                        `;
                        btnRun.disabled = false;
                        btnRun.innerHTML = `<span>Fermer la Calibration</span>`;
                        btnRun.onclick = () => closeCameraCalibModal();
                    } else {
                        hudEl.style.display = 'none';
                        videoEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        statusText.innerHTML = `
                            <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
                            <span style="color:var(--danger); font-weight:bold;">Échec de l'analyse</span><br/>
                            <span style="font-size:0.75rem; color:var(--text-secondary);">Mire de calibration introuvable ou illisible.</span>
                        `;
                        btnRun.disabled = false;
                        btnRun.innerHTML = `<span>📷 Réessayer la Calibration</span>`;
                        btnRun.onclick = () => runIndividualCameraCalib();
                    }
                }
            }, 500);
        }

        function toggleKeepStream(camId) {
            if (!window.keepStreams) window.keepStreams = { 1: false, 2: false };
            const current = window.keepStreams[camId];
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({
                    type: "toggle_keep_stream",
                    camera: camId,
                    keep: !current
                }));
            } else {
                alert("WebSocket déconnecté.");
            }
        }

        function toggleStream(camId, isExplicit = false) {
            if (!window.activeStreams) window.activeStreams = { 1: false, 2: false };
            if (!window.localViewing) window.localViewing = { 1: false, 2: false };
            
            if (!window.localViewing[camId]) {
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    let vSlamVal = false;
                    if (camId === 1) {
                        const vSlamCheck = document.getElementById('stream-v-slam-1');
                        if (vSlamCheck) vSlamVal = vSlamCheck.checked;
                    }
                    appWs.send(JSON.stringify({type: "request_camera", camera: camId, v_slam: vSlamVal}));
                    window.localViewing[camId] = true;
                    
                    const statusEl = document.getElementById(`stream-status-${camId}`);
                    const btnText = document.getElementById(`stream-btn-text-${camId}`);
                    statusEl.textContent = 'Démarrage…';
                    statusEl.className = 'status-badge';
                    btnText.textContent = 'Couper Caméra';
                    
                    startStreamWebRTC(camId);
                } else {
                    if (isExplicit) {
                        alert("WebSocket déconnecté. Impossible d'activer la caméra.");
                    } else {
                        console.warn("[Auto] WebSocket not open, deferring stream startup.");
                    }
                }
            } else {
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({type: "release_camera", camera: camId}));
                }
                window.localViewing[camId] = false;
                if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                window.userClosedStream[camId] = true;
                stopStreamUI(camId);
            }
        }

        function toggleFullscreen(camId) {
            const container = document.getElementById(`video-container-${camId}`);
            if (!container) return;
            if (!document.fullscreenElement) {
                if (container.requestFullscreen) {
                    container.requestFullscreen();
                } else if (container.webkitRequestFullscreen) {
                    container.webkitRequestFullscreen();
                } else if (container.msRequestFullscreen) {
                    container.msRequestFullscreen();
                }
            } else {
                if (document.exitFullscreen) {
                    document.exitFullscreen();
                }
            }
        }

        function handleVSlamToggleChange() {
            if (window.localViewing && window.localViewing[1]) {
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    const vSlamCheck = document.getElementById('stream-v-slam-1');
                    const vSlamVal = vSlamCheck ? vSlamCheck.checked : false;
                    
                    const loaderEl = document.getElementById('stream-loader-1');
                    const videoEl = document.getElementById('video-cam-1');
                    const fsBtn = document.getElementById('video-fs-btn-1');
                    const statusEl = document.getElementById('stream-status-1');
                    
                    if (loaderEl) loaderEl.style.display = 'flex';
                    if (videoEl) videoEl.style.display = 'none';
                    if (fsBtn) fsBtn.style.display = 'none';
                    if (statusEl) {
                        statusEl.textContent = 'Reconfiguration…';
                        statusEl.className = 'status-badge';
                    }
                    
                    appWs.send(JSON.stringify({type: "request_camera", camera: 1, v_slam: vSlamVal}));
                    startStreamWebRTC(1);
                }
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
                            : `<span class="status-badge" style="font-size: 0.75rem; background-color: rgba(225, 29, 72, 0.05); color: var(--danger); border: 1px solid rgba(225, 29, 72, 0.15)">❌ MyGES non configuré</span>`;

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
            if (!confirm(`Voulez-vous vraiment supprimer le compte de ${fullName} ?\n(Cela supprimera également ses identifiants MyGES et ses photos de visage)`)) return;
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
            document.getElementById('form-firstname').disabled = true;
            document.getElementById('form-lastname').disabled = true;
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
                const facesRes = await fetch('/faces', { headers: { 'X-API-Token': apiToken } });
                const accountsRes = await fetch('/accounts', { headers: { 'X-API-Token': apiToken } });
                
                if (facesRes.ok && accountsRes.ok) {
                    const data = await facesRes.json();
                    const accounts = await accountsRes.json();
                    
                    const faces = data.faces || [];
                    facesCached = faces;
                    
                    const usersList = Object.keys(accounts);
                    const grouped = {};
                    usersList.forEach(name => {
                        grouped[name] = [];
                    });

                    faces.forEach(f => {
                        const matchedName = usersList.find(u => u && f.name && u.toLowerCase() === f.name.toLowerCase()) || f.name;
                        if (!grouped[matchedName]) grouped[matchedName] = [];
                        grouped[matchedName].push(f);
                    });

                    const foldersContainer = document.getElementById('folders-container');
                    foldersContainer.innerHTML = '';

                    const keys = Object.keys(grouped);
                    if (keys.length === 0) {
                        foldersContainer.innerHTML = `
                            <div style="grid-column: 1/-1; text-align: center; padding: 4rem; color: var(--text-secondary); border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card);">
                                Aucun dossier utilisateur disponible. Créez un compte d'abord.
                            </div>`;
                        return;
                    }

                    keys.forEach(name => {
                        const userFaces = grouped[name];
                        const count = userFaces.length;
                        const initials = name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2) || 'U';
                        
                        const card = document.createElement('div');
                        card.className = 'folder-card';
                        card.onclick = () => openFolderDetails(name, userFaces);
                        
                        card.innerHTML = `
                            <div class="folder-icon-wrapper">
                                <svg viewBox="0 0 24 24" width="64" height="64" fill="currentColor" style="opacity: 0.85;">
                                    <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2z"/>
                                </svg>
                                <div class="folder-avatar-badge">${initials}</div>
                            </div>
                            <div class="folder-name font-outfit">${name}</div>
                            <div class="folder-count">${count} photo${count > 1 ? 's' : ''}</div>
                        `;
                        foldersContainer.appendChild(card);
                    });

                    if (activeFolderName) {
                        const activeUserFaces = grouped[activeFolderName] || [];
                        renderFolderDetails(activeFolderName, activeUserFaces);
                    }
                }
            } catch (e) {
                console.error("Gallery loading error:", e);
            }
        }

        function openFolderDetails(name, userFaces) {
            activeFolderName = name;
            document.getElementById('faces-folders-view').style.display = 'none';
            document.getElementById('faces-details-view').classList.add('active');
            document.getElementById('current-folder-username-label').textContent = name;
            renderFolderDetails(name, userFaces);
        }

        function closeFolderDetails() {
            activeFolderName = null;
            document.getElementById('faces-details-view').classList.remove('active');
            document.getElementById('faces-folders-view').style.display = 'block';
            loadFacesGallery();
        }

        function renderFolderDetails(name, userFaces) {
            document.getElementById('details-folder-name').textContent = name;
            document.getElementById('details-folder-count').textContent = `${userFaces.length} / 8 photos`;
            
            const grid = document.getElementById('details-faces-grid');
            grid.innerHTML = '';
            
            if (userFaces.length === 0) {
                grid.innerHTML = `
                    <div style="grid-column: 1/-1; text-align: center; padding: 4rem; color: var(--text-secondary); border: 1px dashed var(--border-color); border-radius: 8px;">
                        Aucune photo pour cet utilisateur. Utilisez la zone ci-dessus pour en ajouter.
                    </div>`;
                return;
            }
            
            userFaces.forEach(f => {
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
                
                fetch(`/faces/${f.id}/image`, { headers: { 'X-API-Token': apiToken } })
                    .then(res => res.blob())
                    .then(blob => {
                        const img = document.getElementById(`face-img-${f.id}`);
                        if (img) img.src = URL.createObjectURL(blob);
                    })
                    .catch(err => console.error("Error loading face image file:", err));
            });
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

        let currentUploadFile = null;
        
        function triggerFaceUpload() {
            document.getElementById('face-file-input').click();
        }

        function handleFaceUploadSelected(e) {
            const files = e.target.files;
            if (!files || files.length === 0) return;
            currentUploadFile = files[0];
            
            if (activeFolderName) {
                executeFaceUploadDirect(activeFolderName);
            }
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
                        if (activeFolderName) {
                            executeFaceUploadDirect(activeFolderName);
                        }
                    }
                }, false);
            }
        }

        async function executeFaceUploadDirect(userName) {
            if (!currentUploadFile || !userName) return;

            const fd = new FormData();
            fd.append('file', currentUploadFile);

            try {
                const res = await fetch(`/faces/upload?name=${encodeURIComponent(userName)}`, {
                    method: 'POST',
                    headers: { 'X-API-Token': apiToken },
                    body: fd
                });

                if (res.ok) {
                    const json = await res.json();
                    if (json.status === 'already_exists') {
                        alert(json.msg);
                    }
                    currentUploadFile = null;
                    document.getElementById('face-file-input').value = '';
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

        async function fetchUpdatesProgress(force = false) {
            let rbInProgress = false;
            let ardInProgress = false;
            try {
                const forceParam = force ? '?force=true' : '';
                const gatewayRes = await fetch(`/system/update/gateway/progress${forceParam}`, { headers: { 'X-API-Token': apiToken } });
                const robotRes = await fetch(`/system/update/robot/progress${forceParam}`, { headers: { 'X-API-Token': apiToken } });
                const arduinoRes = await fetch(`/system/update/arduino/progress${forceParam}`, { headers: { 'X-API-Token': apiToken } });

                if (gatewayRes.ok) {
                    const gw = await gatewayRes.json();
                    const gwUpToDate = gw.current_version && gw.latest_version && gw.current_version === gw.latest_version;
                    const gwStatusLower = (gw.status || '').toLowerCase();
                    let gwDisplayStatus = gw.status || 'Prêt';
                    if (gwStatusLower.includes('failed') && gwUpToDate) gwDisplayStatus = 'À jour';

                    document.getElementById('gateway-update-status').textContent = gwDisplayStatus;
                    document.getElementById('gateway-update-bar').style.width = `${gw.percent}%`;
                    document.getElementById('gateway-update-percent').textContent = `${gw.percent}%`;
                    document.getElementById('gateway-current-version').textContent = gw.current_version || 'Inconnu';
                    document.getElementById('gateway-latest-version').textContent = gw.latest_version || 'Inconnu';

                    const gwInProgress = gw.status &&
                        !gwStatusLower.includes('idle') &&
                        !gwStatusLower.includes('prêt') &&
                        !gwStatusLower.includes('done') &&
                        !gwStatusLower.includes('failed') &&
                        gw.percent < 100;

                    const gwBtn = document.getElementById('btn-update-gateway');
                    const gwBtnText = document.getElementById('btn-update-gateway-text');
                    if (gwBtn) {
                        gwBtn.disabled = gwInProgress;
                        gwBtn.style.opacity = gwInProgress ? '0.5' : '1';
                    }
                    if (gwBtnText) {
                        gwBtnText.textContent = gwUpToDate ? 'Réinstaller la Gateway' : 'Lancer la mise à jour Gateway';
                    }
                }

                if (robotRes.ok) {
                    const rb = await robotRes.json();
                    const rbUpToDate = rb.current_version && rb.latest_version && rb.current_version === rb.latest_version;
                    const rbStatusLower = (rb.status || '').toLowerCase();
                    let rbDisplayStatus = rb.status || 'Prêt';
                    if (rbStatusLower.includes('failed') && rbUpToDate) rbDisplayStatus = 'À jour';

                    document.getElementById('robot-update-status').textContent = rbDisplayStatus;
                    document.getElementById('robot-update-bar').style.width = `${rb.percent}%`;
                    document.getElementById('robot-update-percent').textContent = `${rb.percent}%`;
                    document.getElementById('robot-current-version').textContent = rb.current_version || 'Inconnu';
                    document.getElementById('robot-latest-version').textContent = rb.latest_version || 'Inconnu';

                    rbInProgress = rb.status &&
                        !rbStatusLower.includes('idle') &&
                        !rbStatusLower.includes('prêt') &&
                        !rbStatusLower.includes('done') &&
                        !rbStatusLower.includes('failed') &&
                        rb.percent < 100;

                    const rbBtn = document.getElementById('btn-update-robot');
                    const rbBtnText = document.getElementById('btn-update-robot-text');
                    if (rbBtn) {
                        rbBtn.disabled = rbInProgress;
                        rbBtn.style.opacity = rbInProgress ? '0.5' : '1';
                    }
                    if (rbBtnText) {
                        rbBtnText.textContent = rbUpToDate ? 'Réinstaller le Robot' : 'Lancer la mise à jour Robot';
                    }
                }

                if (arduinoRes.ok) {
                    const ard = await arduinoRes.json();
                    const ardUpToDate = ard.current_version && ard.latest_version && ard.current_version === ard.latest_version;
                    const ardStatusLower = (ard.status || '').toLowerCase();
                    let ardDisplayStatus = ard.status || 'Prêt';
                    if (ardStatusLower.includes('failed') && ardUpToDate) ardDisplayStatus = 'À jour';

                    document.getElementById('arduino-update-status').textContent = ardDisplayStatus;
                    document.getElementById('arduino-update-bar').style.width = `${ard.percent}%`;
                    document.getElementById('arduino-update-percent').textContent = `${ard.percent}%`;
                    document.getElementById('arduino-current-version').textContent = ard.current_version || 'Inconnu';
                    document.getElementById('arduino-latest-version').textContent = ard.latest_version || 'Inconnu';

                    ardInProgress = ard.status &&
                        !ardStatusLower.includes('idle') &&
                        !ardStatusLower.includes('prêt') &&
                        !ardStatusLower.includes('done') &&
                        !ardStatusLower.includes('failed') &&
                        ard.percent < 100;

                    const telemetryState = window.lastTelemetryState || {};
                    const robotOnline = telemetryState.robot_status === 'online';
                    const telemetrySensors = telemetryState.sensors || {};
                    const arduinoConnected = telemetrySensors.arduino_connected === true;

                    const ardBtn = document.getElementById('btn-update-arduino');
                    const ardBtnText = document.getElementById('btn-update-arduino-text');
                    if (ardBtn) {
                        if (!robotOnline) {
                            ardBtn.disabled = true;
                            ardBtn.style.opacity = '0.5';
                            if (ardBtnText) ardBtnText.textContent = "Robot Hors-ligne";
                        } else if (!arduinoConnected) {
                            ardBtn.disabled = true;
                            ardBtn.style.opacity = '0.5';
                            if (ardBtnText) ardBtnText.textContent = "Arduino non connecté";
                        } else {
                            ardBtn.disabled = ardInProgress;
                            ardBtn.style.opacity = ardInProgress ? '0.5' : '1';
                            if (ardBtnText) {
                                ardBtnText.textContent = ardUpToDate ? "Réinstaller le Code Arduino" : "Reflasher l'Arduino";
                            }
                        }
                    }
                }

                // Update zone opacity & interaction based on connection status
                const telemetryState = window.lastTelemetryState || {};
                const robotOnline = telemetryState.robot_status === 'online';
                const telemetrySensors = telemetryState.sensors || {};
                const arduinoConnected = telemetrySensors.arduino_connected === true;

                const robotZone = document.getElementById('update-zone-robot');
                if (robotZone) {
                    if (!robotOnline && !rbInProgress) {
                        robotZone.style.opacity = '0.4';
                        robotZone.style.pointerEvents = 'none';
                    } else {
                        robotZone.style.opacity = '1';
                        robotZone.style.pointerEvents = 'auto';
                    }
                }

                const arduinoZone = document.getElementById('update-zone-arduino');
                if (arduinoZone) {
                    if ((!robotOnline || !arduinoConnected) && !ardInProgress) {
                        arduinoZone.style.opacity = '0.4';
                        arduinoZone.style.pointerEvents = 'none';
                    } else {
                        arduinoZone.style.opacity = '1';
                        arduinoZone.style.pointerEvents = 'auto';
                    }
                }
            } catch (e) {
                console.error("Updates progress fetch error:", e);
            }
        }

        async function triggerUpdate(target) {
            const btnText = document.getElementById(`btn-update-${target}-text`);
            const isReinstall = btnText && btnText.textContent.toLowerCase().includes('réinstaller');
            const label = target === 'gateway' ? 'Gateway' : target === 'arduino' ? 'Arduino Mega' : 'Robot Pi';
            const action = isReinstall ? 'réinstaller' : 'mettre à jour';
            if (!confirm(`Voulez-vous vraiment ${action} la ${label} ?`)) return;
            try {
                const res = await fetch(`/system/update/${target}`, {
                    method: 'POST',
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    fetchUpdatesProgress(true);
                } else {
                    alert('Impossible de démarrer la mise à jour.');
                }
            } catch (e) {
                alert('Erreur réseau.');
            }
        }

        // ─── EASYCONFIG FUNCTIONS ──────────────────────────────────────────────
        let ecCurrentStep = 1;
        let ecCalibratedMotors = false;
        let ecCalibratedCam1 = false;
        let ecCalibratedCam2 = false;
        let ecPeerConnections = { 1: null, 2: null };

        function openEasyConfig() {
            ecCurrentStep = 1;
            ecCalibratedMotors = false;
            ecCalibratedCam1 = false;
            ecCalibratedCam2 = false;
            
            for (let id of [1, 2]) {
                if (ecPeerConnections[id]) {
                    try { ecPeerConnections[id].close(); } catch(e) {}
                    ecPeerConnections[id] = null;
                }
            }
            
            document.getElementById('easyconfig-overlay').classList.add('active');
            ecShowStep(1);
            
            ecUpdateMotorFeedback();
            window.ecFeedbackInterval = setInterval(ecUpdateMotorFeedback, 500);
        }

        function closeEasyConfig() {
            clearInterval(window.ecFeedbackInterval);
            document.getElementById('easyconfig-overlay').classList.remove('active');
            
            for (let id of [1, 2]) {
                const videoEl = document.getElementById(`ec-cam-video-${id}`);
                if (window.hlsInstances && window.hlsInstances[`ec-${id}`]) {
                    try { window.hlsInstances[`ec-${id}`].destroy(); } catch(e) {}
                    delete window.hlsInstances[`ec-${id}`];
                }
                if (videoEl) {
                    videoEl.srcObject = null;
                    videoEl.src = '';
                    videoEl.removeAttribute('src');
                }
                if (ecPeerConnections[id]) {
                    try { ecPeerConnections[id].close(); } catch(e) {}
                    ecPeerConnections[id] = null;
                }
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "release_camera", camera: id }));
                }
            }
        }

        function ecUpdateMotorFeedback() {
            if (window.lastTelemetryState && window.lastTelemetryState.joints) {
                const j = window.lastTelemetryState.joints;
                for (let i = 0; i < 12; i++) {
                    const el2 = document.getElementById(`ec-j${i}`);
                    if (el2) {
                        el2.textContent = `${Math.round(j[i])}°`;
                    }
                }
            }
        }

        function ecShowStep(step) {
            ecCurrentStep = step;
            
            for (let i = 1; i <= 4; i++) {
                const div = document.getElementById(`ec-step-${i}`);
                if (div) div.style.display = 'none';
                
                const dot = document.getElementById(`step-dot-${i}`);
                if (dot) {
                    dot.style.color = 'var(--text-secondary)';
                    const numSpan = dot.querySelector('span');
                    if (numSpan) {
                        numSpan.style.background = getComputedStyle(document.documentElement).getPropertyValue('--border-color').trim();
                        numSpan.style.color = 'var(--text-secondary)';
                    }
                }
            }
            
            const currentDiv = document.getElementById(`ec-step-${step}`);
            if (currentDiv) {
                currentDiv.style.display = 'flex';
            }
            
            for (let i = 1; i <= step; i++) {
                const dot = document.getElementById(`step-dot-${i}`);
                if (dot) {
                    dot.style.color = i === step ? 'var(--accent)' : 'var(--success)';
                    dot.style.fontWeight = i === step ? '600' : 'normal';
                    const numSpan = dot.querySelector('span');
                    if (numSpan) {
                        numSpan.style.background = i === step ? 'var(--accent)' : 'var(--success)';
                        numSpan.style.color = 'white';
                        if (i < step) numSpan.textContent = '✓';
                        else numSpan.textContent = i;
                    }
                }
            }
            
            document.getElementById('ec-progress-text').textContent = `Étape ${step} sur 4`;
            document.getElementById('ec-btn-prev').disabled = (step === 1);
            
            if (step === 2 || step === 3) {
                const camId = step === 2 ? 1 : 2;
                const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);
                const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);
                const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);
                const statusText = document.getElementById(`ec-cam-status-text-${camId}`);
                const videoEl = document.getElementById(`ec-cam-video-${camId}`);
                const hudEl = document.getElementById(`ec-cam-hud-${camId}`);
                
                if (btnRun) {
                    btnRun.disabled = false;
                    btnRun.innerHTML = `📷 Lancer la Calibration Cam${camId}`;
                    btnRun.onclick = () => ecRunCameraCalib(camId);
                }
                if (btnSkip) btnSkip.disabled = false;
                if (overlayEl) {
                    overlayEl.style.display = 'flex';
                    overlayEl.style.backgroundColor = 'rgba(0,0,0,0.85)';
                }
                if (statusText) statusText.innerHTML = `Le flux vidéo de la caméra s'affiche dès le lancement.`;
                if (videoEl) videoEl.style.display = 'none';
                if (hudEl) hudEl.style.display = 'none';
            }
            
            let canGoNext = false;
            if (step === 1 && ecCalibratedMotors) canGoNext = true;
            if (step === 2 && ecCalibratedCam1) canGoNext = true;
            if (step === 3 && ecCalibratedCam2) canGoNext = true;
            if (step === 4) canGoNext = false;
            
            document.getElementById('ec-btn-next').disabled = !canGoNext;
        }

        function ecPrevStep() {
            if (ecCurrentStep > 1) {
                if (ecCurrentStep === 2 || ecCurrentStep === 3) {
                    for (let id of [1, 2]) {
                        if (ecPeerConnections[id]) {
                            try { ecPeerConnections[id].close(); } catch(e) {}
                            ecPeerConnections[id] = null;
                        }
                        if (appWs && appWs.readyState === WebSocket.OPEN) {
                            appWs.send(JSON.stringify({ type: "release_camera", camera: id }));
                        }
                    }
                }
                ecShowStep(ecCurrentStep - 1);
            }
        }

        function ecNextStep(targetStep = null) {
            let next = targetStep !== null ? targetStep : ecCurrentStep + 1;
            
            if (next === 3) {
                const cam2Connected = window.lastTelemetryState && window.lastTelemetryState.sensors && window.lastTelemetryState.sensors.cam2_connected === true;
                if (!cam2Connected) {
                    next = 4;
                }
            }
            
            if (ecCurrentStep === 2 || ecCurrentStep === 3) {
                for (let id of [1, 2]) {
                    if (ecPeerConnections[id]) {
                        try { ecPeerConnections[id].close(); } catch(e) {}
                        ecPeerConnections[id] = null;
                    }
                    if (appWs && appWs.readyState === WebSocket.OPEN) {
                        appWs.send(JSON.stringify({ type: "release_camera", camera: id }));
                    }
                }
            }
            
            if (next <= 4) {
                ecShowStep(next);
            }
        }

        function ecCalculateOffsets() {
            let currentJoints = window.lastTelemetryState && window.lastTelemetryState.joints ? window.lastTelemetryState.joints : [90, 90, 90, 90, 90, 90, 90, 90, 90, 90, 90, 90];
            let targetAngles = [
                90, 90, 0,
                90, 90, 0,
                90, 90, 0,
                90, 90, 0
            ];
            
            const offsets = [];
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`calib-slider-${i}`);
                let currentOffset = slider ? parseInt(slider.value) : 0;
                let delta = targetAngles[i] - currentJoints[i];
                let newOffset = Math.max(-30, Math.min(30, Math.round(currentOffset + delta)));
                
                if (slider) {
                    slider.value = newOffset;
                    updateCalibSliderVal(i);
                }
                offsets.push(newOffset);
            }
            
            fetch('/core/calibration', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Token': apiToken
                },
                body: JSON.stringify({ offsets: offsets })
            }).catch(err => console.error(err));
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));
            }
            
            ecCalibratedMotors = true;
            document.getElementById('ec-motor-success-anim').style.display = 'block';
            document.getElementById('ec-btn-next').disabled = false;
        }

        async function ecRunCameraCalib(camId) {
            const videoEl = document.getElementById(`ec-cam-video-${camId}`);
            const hudEl = document.getElementById(`ec-cam-hud-${camId}`);
            const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);
            const statusText = document.getElementById(`ec-cam-status-text-${camId}`);
            const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);
            const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);
            
            btnRun.disabled = true;
            btnSkip.disabled = true;
            btnRun.innerHTML = `<span>📷 Connexion...</span>`;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "request_camera", camera: camId, v_slam: false }));
            }
            
            statusText.innerHTML = `
                <div style="width:20px; height:20px; border:2px solid var(--accent); border-top-color:transparent; border-radius:50%; animation:spin 1s linear infinite; margin:0 auto 0.5rem;"></div>
                <span>Initialisation flux WebRTC caméra...</span>
            `;
            
            let pc = null;
            let fallbackTriggered = false;
            const triggerHLSFallback = () => {
                if (fallbackTriggered) return;
                fallbackTriggered = true;
                console.warn(`WebRTC failed or timed out for EasyConfig camera ${camId}, trying HLS fallback...`);
                if (pc) {
                    if (ecPeerConnections[camId] === pc) {
                        ecPeerConnections[camId] = null;
                    }
                    try { pc.close(); } catch(e) {}
                }
                
                playHLSStream(
                    videoEl,
                    camId,
                    () => {
                        overlayEl.style.display = 'none';
                        videoEl.style.display = 'block';
                        hudEl.style.display = 'block';
                        
                        btnRun.disabled = false;
                        btnSkip.disabled = false;
                        btnRun.innerHTML = `<span>📷 Capturer & Calibrer</span>`;
                        btnRun.onclick = () => ecConfirmCalibration(camId);
                    },
                    (err) => {
                        videoEl.style.display = 'none';
                        hudEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        statusText.innerHTML = `
                            <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
                            <span style="color:var(--danger); font-weight:bold;">Échec : Flux vidéo indisponible.</span><br/>
                            <span style="font-size:0.75rem; color:var(--text-secondary);">Vérifiez que la caméra est bien branchée sur le robot.</span>
                        `;
                        btnRun.disabled = false;
                        btnSkip.disabled = false;
                        btnRun.innerHTML = `<span>📷 Lancer la Calibration Cam${camId}</span>`;
                        btnRun.onclick = () => ecRunCameraCalib(camId);
                    },
                    `ec-${camId}`
                );
            };

            try {
                pc = new RTCPeerConnection({ iceServers: [] });
                ecPeerConnections[camId] = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });
                
                let trackTimeout = setTimeout(() => {
                    triggerHLSFallback();
                }, 4000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        triggerHLSFallback();
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
                    if (fallbackTriggered) return;

                    if (event.streams && event.streams[0]) {
                        videoEl.srcObject = event.streams[0];
                    } else {
                        const inboundStream = new MediaStream();
                        inboundStream.addTrack(event.track);
                        videoEl.srcObject = inboundStream;
                    }
                    videoEl.play().catch(e => console.warn(e));
                    
                    overlayEl.style.display = 'none';
                    videoEl.style.display = 'block';
                    hudEl.style.display = 'block';
                    
                    btnRun.disabled = false;
                    btnSkip.disabled = false;
                    btnRun.innerHTML = `<span>📷 Capturer & Calibrer</span>`;
                    btnRun.onclick = () => ecConfirmCalibration(camId);
                };
                
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                
                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;
                
                let response = null;
                let retries = 15;
                while (retries > 0 && ecCurrentStep === (camId === 1 ? 2 : 3)) {
                    try {
                        response = await fetch(webrtcUrl, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/sdp' },
                            body: pc.localDescription.sdp
                        });
                        if (response.ok) break;
                    } catch (e) {
                        console.warn(e);
                    }
                    retries--;
                    if (retries > 0) {
                        await new Promise(r => setTimeout(r, 200));
                    }
                }
                
                if (!response || !response.ok) {
                    throw new Error("WHEP stream not ready");
                }
                
                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({
                    type: 'answer',
                    sdp: answerSdp
                }));
                
            } catch (err) {
                console.error(err);
                triggerHLSFallback();
            }
        }

        function ecConfirmCalibration(camId) {
            const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);
            const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);
            
            btnRun.disabled = true;
            btnSkip.disabled = true;
            btnRun.innerHTML = `<span>📷 Analyse...</span>`;
            
            ecStartScanningSim(camId);
        }

        function ecStartScanningSim(camId) {
            const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);
            const statusText = document.getElementById(`ec-cam-status-text-${camId}`);
            const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);
            const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);
            const hudEl = document.getElementById(`ec-cam-hud-${camId}`);
            const videoEl = document.getElementById(`ec-cam-video-${camId}`);
            
            let progress = 0;
            const progressInterval = setInterval(() => {
                progress += 25;
                if (progress >= 100) {
                    clearInterval(progressInterval);
                    
                    const isCameraConnected = window.lastTelemetryState && window.lastTelemetryState.sensors && 
                        window.lastTelemetryState.sensors[`cam${camId}_connected`] === true;
                        
                    if (isCameraConnected) {
                        hudEl.style.display = 'none';
                        videoEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        overlayEl.style.backgroundColor = 'rgba(9,9,11,0.9)';
                        statusText.innerHTML = `
                            <div style="width: 50px; height: 50px; border-radius: 50%; background: rgba(72, 209, 204, 0.1); border: 2px solid var(--success); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--success); margin: 0 auto 0.5rem;">✓</div>
                            <span style="color:var(--success); font-weight:bold; font-size: 0.95rem;">Calibration réussie !</span><br/>
                            <span style="font-size:0.75rem; color:var(--text-secondary);">Mire détectée et paramètres intrinsèques enregistrés.</span>
                        `;
                        
                        if (camId === 1) ecCalibratedCam1 = true;
                        if (camId === 2) ecCalibratedCam2 = true;
                        
                        document.getElementById('ec-btn-next').disabled = false;
                        btnSkip.disabled = false;
                    } else {
                        hudEl.style.display = 'none';
                        videoEl.style.display = 'none';
                        overlayEl.style.display = 'flex';
                        overlayEl.style.backgroundColor = 'rgba(9,9,11,0.9)';
                        statusText.innerHTML = `
                            <div style="width: 50px; height: 50px; border-radius: 50%; background: rgba(239, 68, 68, 0.1); border: 2px solid var(--danger); display: flex; align-items: center; justify-content: center; font-size: 1.5rem; color: var(--danger); margin: 0 auto 0.5rem;">✗</div>
                            <span style="color:var(--danger); font-weight:bold; font-size: 0.95rem;">Échec de la calibration</span><br/>
                            <span style="font-size:0.75rem; color:var(--text-secondary);">Aucune mire de calibration détectée ou flux caméra instable.</span>
                        `;
                        btnRun.disabled = false;
                        btnSkip.disabled = false;
                        btnRun.innerHTML = `<span>📷 Lancer la Calibration Cam${camId}</span>`;
                        btnRun.onclick = () => ecRunCameraCalib(camId);
                    }
                }
            }, 500);
        }

        function ecStartRobotAndClose() {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "start_robot" }));
            }
            closeEasyConfig();
        }

        // ─── VSLAM TEST FUNCTIONS ──────────────────────────────────────────────
        window.vslamTesting = false;
        let vslamPeerConnection = null;
        let lastPoseTime = 0;
        let poseUpdateCount = 0;
        let vslamHzInterval = null;

        function toggleVSlamTest() {
            const btn = document.getElementById('btn-vslam-test-toggle');
            const container = document.getElementById('vslam-test-video-container');
            const statusVal = document.getElementById('vslam-status-val');
            const badgeEl = document.getElementById('vslam-badge');
            const rateVal = document.getElementById('vslam-rate-val');
            
            if (!window.vslamTesting) {
                window.vslamTesting = true;
                btn.textContent = '⏹️ Arrêter le Test V-SLAM';
                btn.className = 'btn btn-secondary';
                container.style.display = 'block';
                badgeEl.textContent = 'Actif';
                badgeEl.className = 'status-badge active';
                statusVal.textContent = 'Initialisation...';
                statusVal.style.color = 'var(--accent)';
                
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "request_camera", camera: 1, v_slam: true }));
                }
                
                startVSlamTestWebRTC();
                
                lastPoseTime = Date.now();
                poseUpdateCount = 0;
                vslamHzInterval = setInterval(() => {
                    const hz = poseUpdateCount / 2;
                    rateVal.textContent = `${hz.toFixed(1)} Hz`;
                    poseUpdateCount = 0;
                    
                    const qualVal = document.getElementById('vslam-quality-val');
                    const warningBox = document.getElementById('vslam-warning-box');
                    
                    if (window.lastTelemetryState && window.lastTelemetryState.imu) {
                        const imu = window.lastTelemetryState.imu;
                        if (!window.lastVSlamImu) window.lastVSlamImu = imu;
                        const delta = Math.abs(imu.roll - window.lastVSlamImu.roll) + Math.abs(imu.pitch - window.lastVSlamImu.pitch);
                        window.lastVSlamImu = imu;
                        
                        if (delta > 12) {
                            qualVal.textContent = 'Dégradée';
                            qualVal.style.color = 'var(--danger)';
                            warningBox.style.display = 'block';
                        } else {
                            qualVal.textContent = 'Optimale';
                            qualVal.style.color = 'var(--success)';
                            warningBox.style.display = 'none';
                        }
                    }
                    
                    if (hz > 0.1) {
                        statusVal.textContent = 'Localisé / Tracking';
                        statusVal.style.color = 'var(--success)';
                    } else {
                        let odomTopicActive = false;
                        if (window.lastTelemetryState && window.lastTelemetryState.topics) {
                            odomTopicActive = window.lastTelemetryState.topics.some(t => t.name === '/odom' && t.hz > 0);
                        }
                        if (odomTopicActive) {
                            statusVal.textContent = 'Recherche de repères...';
                            statusVal.style.color = 'var(--accent)';
                        } else {
                            statusVal.textContent = 'Attente du nœud ROS 2...';
                            statusVal.style.color = 'var(--text-secondary)';
                        }
                    }
                }, 2000);
                
            } else {
                window.vslamTesting = false;
                btn.textContent = '🚀 Lancer le Test V-SLAM';
                btn.className = 'btn btn-primary';
                container.style.display = 'none';
                badgeEl.textContent = 'Inactif';
                badgeEl.className = 'status-badge';
                statusVal.textContent = 'Non démarré';
                statusVal.style.color = 'var(--text-secondary)';
                rateVal.textContent = '0.0 Hz';
                document.getElementById('vslam-quality-val').textContent = 'Optimale';
                document.getElementById('vslam-quality-val').style.color = 'var(--success)';
                document.getElementById('vslam-warning-box').style.display = 'none';
                
                clearInterval(vslamHzInterval);
                
                if (window.hlsInstances && window.hlsInstances['vslam']) {
                    try { window.hlsInstances['vslam'].destroy(); } catch(e) {}
                    delete window.hlsInstances['vslam'];
                }
                const videoEl = document.getElementById('vslam-test-video');
                if (videoEl) {
                    videoEl.srcObject = null;
                    videoEl.src = '';
                    videoEl.removeAttribute('src');
                }

                if (vslamPeerConnection) {
                    try { vslamPeerConnection.close(); } catch(e) {}
                    vslamPeerConnection = null;
                }
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "release_camera", camera: 1 }));
                }
            }
        }

        async function startVSlamTestWebRTC() {
            const videoEl = document.getElementById('vslam-test-video');
            const loaderEl = document.getElementById('vslam-test-loader');
            const statusVal = document.getElementById('vslam-status-val');
            
            if (vslamPeerConnection) {
                try { vslamPeerConnection.close(); } catch(e) {}
                vslamPeerConnection = null;
            }
            
            loaderEl.style.display = 'flex';
            videoEl.style.display = 'none';
            
            let pc = null;
            let fallbackTriggered = false;
            const triggerHLSFallback = () => {
                if (fallbackTriggered) return;
                fallbackTriggered = true;
                console.warn("WebRTC failed or timed out for VSLAM test, trying HLS fallback...");
                if (pc) {
                    if (vslamPeerConnection === pc) {
                        vslamPeerConnection = null;
                    }
                    try { pc.close(); } catch(e) {}
                }
                
                playHLSStream(
                    videoEl,
                    1,
                    () => {
                        loaderEl.style.display = 'none';
                        videoEl.style.display = 'block';
                    },
                    (err) => {
                        loaderEl.style.display = 'none';
                        statusVal.textContent = "Erreur HLS";
                        statusVal.style.color = "var(--danger)";
                    },
                    'vslam'
                );
            };

            try {
                pc = new RTCPeerConnection({ iceServers: [] });
                vslamPeerConnection = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });
                
                let trackTimeout = setTimeout(() => {
                    triggerHLSFallback();
                }, 4000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        triggerHLSFallback();
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
                    if (fallbackTriggered) return;

                    if (event.streams && event.streams[0]) {
                        videoEl.srcObject = event.streams[0];
                    } else {
                        const inboundStream = new MediaStream();
                        inboundStream.addTrack(event.track);
                        videoEl.srcObject = inboundStream;
                    }
                    videoEl.play().catch(e => console.warn(e));
                    loaderEl.style.display = 'none';
                    videoEl.style.display = 'block';
                };
                
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                
                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam1/whep`;
                
                let response = null;
                let retries = 15;
                while (retries > 0 && window.vslamTesting) {
                    try {
                        response = await fetch(webrtcUrl, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/sdp' },
                            body: pc.localDescription.sdp
                        });
                        if (response.ok) break;
                    } catch (e) {
                        console.warn(e);
                    }
                    retries--;
                    if (retries > 0) {
                        await new Promise(r => setTimeout(r, 200));
                    }
                }
                
                if (!response || !response.ok) {
                    throw new Error("WHEP stream not ready");
                }
                
                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({
                    type: 'answer',
                    sdp: answerSdp
                }));
                
            } catch(err) {
                console.error(err);
                triggerHLSFallback();
            }
        }

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
    state = load_json(STATE_FILE, default={"robot_status": "offline"})
    state["active_streams"] = {
        "1": stream_active[1],
        "2": stream_active[2]
    }
    return state

# ─── System Updates API ───────────────────────────────────────────────────────
GITHUB_RELEASES_CACHE = {} # repo_name -> (tag_name, timestamp)

def get_cached_latest_release(repo: str, force: bool = False) -> str:
    now = time.time()
    if not force and repo in GITHUB_RELEASES_CACHE:
        tag, cached_time = GITHUB_RELEASES_CACHE[repo]
        if now - cached_time < 300: # 5 minutes cache
            return tag
            
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"
        resp = requests.get(url, timeout=3, headers=headers)
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
ARDUINO_UPDATE_FILE = DATA_DIR / "arduino_update_state.json"

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
def get_gateway_update_progress(force: bool = False):
    progress = load_json(GATEWAY_UPDATE_FILE, default={"status": "idle", "percent": 100})
    if progress.get("status") not in ["idle", "failed"] and "failed" not in progress.get("status", "") and GATEWAY_UPDATE_FILE.exists():
        mtime = GATEWAY_UPDATE_FILE.stat().st_mtime
        if time.time() - mtime > 600:
            progress = {"status": "failed", "percent": 0, "error": "Timeout (10 min sans réponse)"}
            save_json(GATEWAY_UPDATE_FILE, progress)
    from updater import get_current_version
    progress["current_version"] = get_current_version()
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE-Gateway", force=force)
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
def get_robot_update_progress(force: bool = False):
    progress = load_json(ROBOT_UPDATE_FILE, default={"status": "idle", "percent": 100})
    if progress.get("status") not in ["idle", "failed"] and "failed" not in progress.get("status", "") and ROBOT_UPDATE_FILE.exists():
        mtime = ROBOT_UPDATE_FILE.stat().st_mtime
        if time.time() - mtime > 600:
            progress = {"status": "failed", "percent": 0, "error": "Timeout (10 min sans réponse)"}
            save_json(ROBOT_UPDATE_FILE, progress)
    state = load_json(STATE_FILE, default={})
    progress["current_version"] = state.get("robot_version", "v0.0.0")
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE", force=force)
    return progress

@app.post("/system/update/arduino", tags=["System Update"], summary="Lancer la mise à jour de l'Arduino", dependencies=[Depends(verify_token)])
async def trigger_arduino_update():
    """Lancer instantanément le flashage de l'Arduino."""
    save_json(ARDUINO_UPDATE_FILE, {"status": "starting", "percent": 0})
    await manager.broadcast(json.dumps({"type": "trigger_arduino_flash"}), "robot")
    await manager.broadcast(json.dumps({"type": "arduino_update_progress", "status": "starting", "percent": 0}), "app")
    return {"status": "triggered"}

@app.post("/system/update/arduino/progress", tags=["System Update"], summary="Mettre à jour le progrès de l'Arduino", dependencies=[Depends(verify_token)])
async def update_arduino_progress(progress: UpdateProgress):
    data = progress.model_dump()
    save_json(ARDUINO_UPDATE_FILE, data)
    await manager.broadcast(json.dumps({"type": "arduino_update_progress", **data}), "app")
    return {"status": "ok"}

@app.get("/system/update/arduino/progress", tags=["System Update"], summary="Récupérer le progrès de mise à jour Arduino", dependencies=[Depends(verify_token)])
def get_arduino_update_progress(force: bool = False):
    progress = load_json(ARDUINO_UPDATE_FILE, default={"status": "idle", "percent": 100})
    if progress.get("status") not in ["idle", "failed"] and "failed" not in progress.get("status", "") and ARDUINO_UPDATE_FILE.exists():
        mtime = ARDUINO_UPDATE_FILE.stat().st_mtime
        if time.time() - mtime > 600:
            progress = {"status": "failed", "percent": 0, "error": "Timeout (10 min sans réponse)"}
            save_json(ARDUINO_UPDATE_FILE, progress)
    state = load_json(STATE_FILE, default={})
    progress["current_version"] = state.get("arduino_version", "v0.0.0")
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE", force=force)
    return progress

# ─── System ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok", "https": True}
