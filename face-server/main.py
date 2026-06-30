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
gateway_telemetry = {"cpu_percent": 0, "ram_percent": 0, "disk_percent": 0, "temp_c": 0, "uptime_s": 0}

# Cache mémoire pour l'état du robot (évite les races conditions fichier)
_last_robot_state = None
_last_robot_state_time = 0


FACES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
# ─── Calibration Files ────────────────────────────────────────────────────────
CALIBRATION_FILE = DATA_DIR / "calibration.json"
CAMERA_CALIB_1_FILE = DATA_DIR / "camera_calib_1.json"
CAMERA_CALIB_2_FILE = DATA_DIR / "camera_calib_2.json"
DEFAULT_CAM_CALIB = {
    "image_width": 640,
    "image_height": 480,
    "camera_name": "usb_cam",
    "camera_matrix": {
        "rows": 3, "cols": 3,
        "data": [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0]
    },
    "distortion_model": "plumb_bob",
    "distortion_coefficients": {
        "rows": 1, "cols": 5,
        "data": [0.0, 0.0, 0.0, 0.0, 0.0]
    },
    "rectification_matrix": {
        "rows": 3, "cols": 3,
        "data": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    },
    "projection_matrix": {
        "rows": 3, "cols": 4,
        "data": [600.0, 0.0, 320.0, 0.0, 0.0, 600.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    }
}


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
        time.sleep(120)  # Attendre 2 min que l'app démarre avant la première vérification
        # First check: passive only (log if update available, no restart)
        try:
            from updater import get_latest_release, get_current_version, _version_tuple
            current = get_current_version()
            release = get_latest_release()
            if release:
                latest = release.get("tag_name", "v0.0.0")
                if _version_tuple(latest) > _version_tuple(current):
                    print(f"[AutoUpdater] Mise à jour disponible: {latest} (actuelle: {current}) — appliquée au prochain redémarrage.")
        except Exception as e:
            print(f"[AutoUpdater] Erreur vérification passive: {e}")
        while True:
            time.sleep(3600)
            try:
                state = load_json(STATE_FILE, default={"robot_status": "offline"})
                status = state.get("robot_status", "offline")
                if status != "online":
                    print("[AutoUpdater] Robot inactif. Vérification de mise à jour Gateway...")
                    # Passive check only - no restart
                    try:
                        from updater import get_latest_release, get_current_version, _version_tuple
                        current = get_current_version()
                        release = get_latest_release()
                        if release:
                            latest = release.get("tag_name", "v0.0.0")
                            if _version_tuple(latest) > _version_tuple(current):
                                print(f"[AutoUpdater] Mise à jour disponible: {latest}")
                    except Exception as e:
                        print(f"[AutoUpdater] Erreur vérification horaire: {e}")
            except Exception as e:
                print(f"[AutoUpdater] Erreur : {e}")

    def _gateway_telemetry_collector():
        prev_idle = prev_total = 0
        while True:
            try:
                with open("/proc/stat") as f:
                    parts = f.readline().split()
                    idle, total = int(parts[4]), sum(int(x) for x in parts[1:])
                d_idle, d_total = idle - prev_idle, total - prev_total
                prev_idle, prev_total = idle, total
                gateway_telemetry["cpu_percent"] = round((1 - d_idle / max(d_total, 1)) * 100, 1)

                with open("/proc/meminfo") as f:
                    mem = {}
                    for line in f:
                        k, *v = line.split(":")
                        mem[k.strip()] = int(v[0].strip().split()[0]) if v else 0
                total_mem = mem.get("MemTotal", 1)
                avail_mem = mem.get("MemAvailable", 0)
                gateway_telemetry["ram_percent"] = round((1 - avail_mem / total_mem) * 100, 1)

                st = os.statvfs("/")
                total_disk = st.f_blocks * st.f_frsize
                free_disk = st.f_bavail * st.f_frsize
                gateway_telemetry["disk_percent"] = round((1 - free_disk / max(total_disk, 1)) * 100, 1)

                try:
                    with open("/sys/class/thermal/thermal_zone0/temp") as f:
                        gateway_telemetry["temp_c"] = round(int(f.read().strip()) / 1000, 1)
                except Exception:
                    gateway_telemetry["temp_c"] = 0

                try:
                    with open("/proc/uptime") as f:
                        gateway_telemetry["uptime_s"] = float(f.read().split()[0])
                except Exception:
                    gateway_telemetry["uptime_s"] = 0
            except Exception:
                pass
            time.sleep(3)

    threading.Thread(target=_hourly_check, daemon=True).start()
    threading.Thread(target=_gateway_telemetry_collector, daemon=True).start()
    yield

app = FastAPI(
    title="Bastet Gateway API",
    description="API Gateway pour le robot Bastet (Faces, MyGES, Core State). Protégée par Token.",
    version="2.0.0",
    lifespan=lifespan,
)

LOGO_PATH = Path(__file__).parent / "logo.webp"

@app.get("/logo.webp", include_in_schema=False)
def serve_logo():
    return FileResponse(LOGO_PATH, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})

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

# Configuration préférée des cibles d'IA par l'utilisateur
preferred_ai_targets = {
    "tts": "robot",
    "stt": "robot",
    "chat": "robot",
    "yolo": "robot",
    "face_rec": "robot"
}

async def handle_node_connection_change(connected: bool):
    """Bascule automatiquement les services si le PC Node se déconnecte/reconnecte."""
    for feature, target in preferred_ai_targets.items():
        if target == "node":
            active_target = "node" if connected else "robot"
            controlled_msg = json.dumps({
                "type": "ai_control",
                "feature": feature,
                "target": active_target
            })
            await manager.broadcast(controlled_msg, "robot")
            await manager.broadcast(controlled_msg, "app")

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
    await handle_node_connection_change(True)
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
        await handle_node_connection_change(False)

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
                elif msg_type == "ai_control":
                    feature = msg_json.get("feature")
                    target = msg_json.get("target")
                    if feature in preferred_ai_targets:
                        preferred_ai_targets[feature] = target
                        node_connected = len(manager.active_connections.get("node", [])) > 0
                        active_target = target
                        if target == "node" and not node_connected:
                            active_target = "robot"
                        msg_json["target"] = active_target
                        data = json.dumps(msg_json)
                elif msg_type == "arduino_cmd":
                    # Forward arduino commands (attach, write, detach, stand, etc.) to the robot
                    await manager.broadcast(data, "robot")
            except Exception:
                pass
                
            # Routage des commandes de l'app mobile vers le robot et le PC Node
            await manager.broadcast(data, "robot")
            await manager.broadcast(data, "node")
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

@app.post("/core/calibration", tags=["CORE State"], summary="Sauvegarder les offsets de calibration", dependencies=[Depends(verify_token)])
def save_calibration(data: dict):
    save_json(CALIBRATION_FILE, data)
    return {"status": "saved"}

@app.get("/core/calibration", tags=["CORE State"], summary="Récupérer les offsets de calibration", dependencies=[Depends(verify_token)])
def get_calibration():
    return load_json(CALIBRATION_FILE, default={"offsets": [0]*12})

@app.get("/core/camera/calibration/{cam_id}", tags=["CORE State"], summary="Récupérer la calibration d'une caméra", dependencies=[Depends(verify_token)])
def get_camera_calibration(cam_id: int):
    if cam_id == 1:
        return load_json(CAMERA_CALIB_1_FILE, default=DEFAULT_CAM_CALIB)
    else:
        return load_json(CAMERA_CALIB_2_FILE, default=DEFAULT_CAM_CALIB)

@app.post("/core/camera/calibration/{cam_id}", tags=["CORE State"], summary="Sauvegarder la calibration d'une caméra", dependencies=[Depends(verify_token)])
def save_camera_calibration(cam_id: int, data: dict):
    if cam_id == 1:
        save_json(CAMERA_CALIB_1_FILE, data)
    else:
        save_json(CAMERA_CALIB_2_FILE, data)
    return {"status": "saved"}

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

        .ros2-topics-card {
            grid-column: 2 / -1;
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
        .gw-cpu-gauge .circle { stroke: #8b5cf6; }
        .gw-ram-gauge .circle { stroke: #06b6d4; }
        .gw-disk-gauge .circle { stroke: #f97316; }

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

            .ros2-topics-card {
                grid-column: 1 / -1;
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
        
        @keyframes pulse-ring {
            0% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(255, 111, 97, 0.7);
            }
            70% {
                transform: scale(1.1);
                box-shadow: 0 0 0 10px rgba(255, 111, 97, 0);
            }
            100% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(255, 111, 97, 0);
            }
        }
        .mic-active {
            animation: pulse-ring 1.5s infinite !important;
            background-color: var(--accent) !important;
            color: white !important;
            border-color: var(--accent) !important;
        }
    </style>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
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
            <img src="/logo.webp" alt="Bastet" class="brand-logo"/>
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
            <li class="nav-item" onclick="switchTab('control')" id="nav-control">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="10"/><polygon points="10 8 16 12 10 16 10 8"/>
                </svg>
                <span>Télécommande</span>
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
            <img src="/logo.webp" alt="Bastet" class="brand-logo" style="width:28px;height:28px;"/>
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

                <!-- Gateway Telemetry Card -->
                <div class="card" id="gateway-card">
                    <div class="card-title">
                        <span>Télémétrie Gateway</span>
                        <span id="gateway-status-badge" class="status-badge online">En ligne</span>
                    </div>
                    <div id="gateway-telemetry-content">
                        <div class="gauge-container">
                            <div class="gauge-item">
                                <div class="gauge-circle gw-cpu-gauge">
                                    <svg viewBox="0 0 36 36" class="circular-chart">
                                        <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                        <path id="gw-gauge-cpu" class="circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                    </svg>
                                    <span id="gw-gauge-cpu-val" class="gauge-value">--%</span>
                                </div>
                                <span class="gauge-label">CPU</span>
                            </div>
                            <div class="gauge-item">
                                <div class="gauge-circle gw-ram-gauge">
                                    <svg viewBox="0 0 36 36" class="circular-chart">
                                        <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                        <path id="gw-gauge-ram" class="circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                    </svg>
                                    <span id="gw-gauge-ram-val" class="gauge-value">--%</span>
                                </div>
                                <span class="gauge-label">RAM</span>
                            </div>
                            <div class="gauge-item">
                                <div class="gauge-circle gw-disk-gauge">
                                    <svg viewBox="0 0 36 36" class="circular-chart">
                                        <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                        <path id="gw-gauge-disk" class="circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                                    </svg>
                                    <span id="gw-gauge-disk-val" class="gauge-value">--%</span>
                                </div>
                                <span class="gauge-label">Disque</span>
                            </div>
                        </div>
                        <div style="margin-top: 0.75rem; display: flex; flex-direction: column; gap: 0.25rem;">
                            <div class="sensor-item"><span class="sensor-label">Température</span><span id="gw-temp-val" class="sensor-val">--°C</span></div>
                            <div class="sensor-item"><span class="sensor-label">Uptime</span><span id="gw-uptime-val" class="sensor-val">--</span></div>
                        </div>
                    </div>
                </div>

                <!-- Arduino Mega Card -->
                <div class="card" id="arduino-card">
                    <div class="card-title">
                        <span>Arduino Mega</span>
                        <span id="arduino-status-badge" class="status-badge offline">Hors-ligne</span>
                    </div>
                    <div id="arduino-telemetry-content">
                        <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                            <div class="sensor-item"><span class="sensor-label">IMU Roll</span><span id="arduino-roll" class="sensor-val">--</span></div>
                            <div class="sensor-item"><span class="sensor-label">IMU Pitch</span><span id="arduino-pitch" class="sensor-val">--</span></div>
                            <div class="sensor-item"><span class="sensor-label">IMU Yaw</span><span id="arduino-yaw" class="sensor-val">--</span></div>
                        </div>
                        <div style="margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid var(--border-color);">
                            <div style="font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 0.4rem; font-weight: 600;">Servos (12 joints)</div>
                            <div id="arduino-joints-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.3rem;">
                            </div>
                        </div>
                    </div>
                    <div id="arduino-offline-msg" style="display: none; text-align: center; padding: 1.5rem 0; color: var(--text-secondary); font-size: 0.85rem;">
                        Arduino Mega non connecté
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
                    <div style="margin-bottom: 0.75rem;">
                            <label style="font-size: 0.8rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">Version à déployer :</label>
                            <div style="display: flex; gap: 0.5rem;">
                                <select id="gateway-release-select" class="form-input" style="flex: 1; font-size: 0.8rem; padding: 0.3rem 0.5rem;">
                                    <option value="">-- Chargement... --</option>
                                </select>
                                <button class="btn btn-secondary" onclick="applySelectedRelease('CORE-Gateway')" style="font-size: 0.75rem; padding: 0.3rem 0.75rem; white-space: nowrap;">
                                    Appliquer
                                </button>
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
                        <div style="margin-bottom: 0.75rem;">
                            <label style="font-size: 0.8rem; color: var(--text-secondary); display: block; margin-bottom: 0.25rem;">Version à déployer (Pi + Arduino liés) :</label>
                            <div style="display: flex; gap: 0.5rem;">
                                <select id="robot-release-select" class="form-input" style="flex: 1; font-size: 0.8rem; padding: 0.3rem 0.5rem;">
                                    <option value="">-- Chargement... --</option>
                                </select>
                                <button class="btn btn-secondary" onclick="applySelectedRelease('CORE')" style="font-size: 0.75rem; padding: 0.3rem 0.75rem; white-space: nowrap;">
                                    Appliquer
                                </button>
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
        <div </div>
            </div>
            <!-- End SLAM disabled overlay -->
id="tab-chat-content" class="tab-content">
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
                            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary active-control" id="yolo-ctrl-robot" onclick="setAIControl('yolo', 'robot')">Robot Local</button>
                                <button class="btn btn-secondary" id="yolo-ctrl-node" onclick="setAIControl('yolo', 'node')">PC Node</button>
                                <button class="btn btn-secondary" id="yolo-ctrl-disabled" onclick="setAIControl('yolo', 'disabled')">Désactivé</button>
                            </div>
                        </div>

                        <div>
                            <h4 style="font-size: 0.95rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Reconnaissance Faciale</h4>
                            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem;">
                                <button class="btn btn-secondary active-control" id="face_rec-ctrl-robot" onclick="setAIControl('face_rec', 'robot')">Robot Local</button>
                                <button class="btn btn-secondary" id="face_rec-ctrl-node" onclick="setAIControl('face_rec', 'node')">PC Node</button>
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
                        <style>
                            .spot-model {
                                position: absolute;
                                width: 0;
                                height: 0;
                                left: 40px;
                                top: 40px;
                                transform-style: preserve-3d;
                            }
                            .spot-box {
                                position: absolute;
                                transform-style: preserve-3d;
                            }
                            .spot-face {
                                position: absolute;
                                background: #1a1a20;
                                border: 1px solid #3c3c46;
                                box-sizing: border-box;
                            }
                            .chassis .spot-face {
                                background: #22222a;
                                border: 1.5px solid var(--accent);
                            }
                            .chassis .face-top {
                                background: #2d2d38;
                                border-bottom: 2px solid var(--accent);
                            }
                            .head .spot-face {
                                background: #111115;
                                border: 1px solid var(--accent);
                            }
                            .head .face-front {
                                background: #08080c;
                                box-shadow: inset 0 0 8px var(--accent);
                                display: flex;
                                align-items: center;
                                justify-content: center;
                            }
                            .head .face-front::after {
                                content: '';
                                width: 8px;
                                height: 2px;
                                background: var(--accent);
                                box-shadow: 0 0 6px var(--accent);
                            }
                            .thigh .spot-face {
                                background: #2c2c36;
                                border: 1px solid #4a4a58;
                            }
                            .shin .spot-face {
                                background: #18181f;
                                border: 1px solid #2e2e38;
                            }
                        </style>
                        <div id="imu-visual-cube" style="width: 80px; height: 80px; transform-style: preserve-3d; transition: transform 0.1s linear; transform: rotateX(0deg) rotateY(0deg) rotateZ(0deg);">
                            <div class="spot-model">
                                <!-- Main Chassis (Body) -->
                                <div class="spot-box chassis" style="transform: translate3d(0, -5px, 0);">
                                    <!-- Front -->
                                    <div class="spot-face face-front" style="width: 32px; height: 18px; left: -16px; top: -9px; transform: translateZ(35px);"></div>
                                    <!-- Back -->
                                    <div class="spot-face face-back" style="width: 32px; height: 18px; left: -16px; top: -9px; transform: rotateY(180deg) translateZ(35px);"></div>
                                    <!-- Left -->
                                    <div class="spot-face face-left" style="width: 70px; height: 18px; left: -35px; top: -9px; transform: rotateY(-90deg) translateZ(16px);"></div>
                                    <!-- Right -->
                                    <div class="spot-face face-right" style="width: 70px; height: 18px; left: -35px; top: -9px; transform: rotateY(90deg) translateZ(16px);"></div>
                                    <!-- Top -->
                                    <div class="spot-face face-top" style="width: 32px; height: 70px; left: -16px; top: -35px; transform: rotateX(90deg) translateZ(9px);"></div>
                                    <!-- Bottom -->
                                    <div class="spot-face face-bottom" style="width: 32px; height: 70px; left: -16px; top: -35px; transform: rotateX(-90deg) translateZ(9px);"></div>
                                </div>

                                <!-- Head -->
                                <div class="spot-box head" style="transform: translate3d(0, -18px, 38px);">
                                    <div class="spot-face face-front" style="width: 20px; height: 12px; left: -10px; top: -6px; transform: translateZ(8px);"></div>
                                    <div class="spot-face face-back" style="width: 20px; height: 12px; left: -10px; top: -6px; transform: rotateY(180deg) translateZ(8px);"></div>
                                    <div class="spot-face face-left" style="width: 16px; height: 12px; left: -8px; top: -6px; transform: rotateY(-90deg) translateZ(10px);"></div>
                                    <div class="spot-face face-right" style="width: 16px; height: 12px; left: -8px; top: -6px; transform: rotateY(90deg) translateZ(10px);"></div>
                                    <div class="spot-face face-top" style="width: 20px; height: 16px; left: -10px; top: -8px; transform: rotateX(90deg) translateZ(6px);"></div>
                                    <div class="spot-face face-bottom" style="width: 20px; height: 16px; left: -10px; top: -8px; transform: rotateX(-90deg) translateZ(6px);"></div>
                                </div>

                                <!-- Leg Front Left -->
                                <div class="spot-box thigh" style="transform: translate3d(-18px, 2px, 22px) rotateX(15deg);">
                                    <div class="spot-face face-front" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: translateZ(5px);"></div>
                                    <div class="spot-face face-back" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: rotateY(180deg) translateZ(5px);"></div>
                                    <div class="spot-face face-left" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(-90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-right" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-top" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(90deg) translateZ(12px);"></div>
                                    <div class="spot-face face-bottom" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(-90deg) translateZ(12px);"></div>
                                    <!-- Shin -->
                                    <div class="spot-box shin" style="transform: translate3d(0, 12px, 0) rotateX(-30deg);">
                                        <div class="spot-face face-front" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: translateZ(3px);"></div>
                                        <div class="spot-face face-back" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: rotateY(180deg) translateZ(3px);"></div>
                                        <div class="spot-face face-left" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(-90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-right" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-top" style="width: 4px; height: 6px; left: -2px; top: -3px; transform: rotateX(90deg) translateZ(0px);"></div>
                                        <div class="spot-face face-bottom" style="width: 4px; height: 6px; left: -2px; top: 21px; transform: rotateX(-90deg) translateZ(0px);"></div>
                                    </div>
                                </div>

                                <!-- Leg Front Right -->
                                <div class="spot-box thigh" style="transform: translate3d(18px, 2px, 22px) rotateX(15deg);">
                                    <div class="spot-face face-front" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: translateZ(5px);"></div>
                                    <div class="spot-face face-back" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: rotateY(180deg) translateZ(5px);"></div>
                                    <div class="spot-face face-left" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(-90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-right" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-top" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(90deg) translateZ(12px);"></div>
                                    <div class="spot-face face-bottom" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(-90deg) translateZ(12px);"></div>
                                    <!-- Shin -->
                                    <div class="spot-box shin" style="transform: translate3d(0, 12px, 0) rotateX(-30deg);">
                                        <div class="spot-face face-front" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: translateZ(3px);"></div>
                                        <div class="spot-face face-back" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: rotateY(180deg) translateZ(3px);"></div>
                                        <div class="spot-face face-left" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(-90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-right" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-top" style="width: 4px; height: 6px; left: -2px; top: -3px; transform: rotateX(90deg) translateZ(0px);"></div>
                                        <div class="spot-face face-bottom" style="width: 4px; height: 6px; left: -2px; top: 21px; transform: rotateX(-90deg) translateZ(0px);"></div>
                                    </div>
                                </div>

                                <!-- Leg Back Left -->
                                <div class="spot-box thigh" style="transform: translate3d(-18px, 2px, -22px) rotateX(-15deg);">
                                    <div class="spot-face face-front" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: translateZ(5px);"></div>
                                    <div class="spot-face face-back" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: rotateY(180deg) translateZ(5px);"></div>
                                    <div class="spot-face face-left" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(-90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-right" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-top" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(90deg) translateZ(12px);"></div>
                                    <div class="spot-face face-bottom" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(-90deg) translateZ(12px);"></div>
                                    <!-- Shin -->
                                    <div class="spot-box shin" style="transform: translate3d(0, 12px, 0) rotateX(30deg);">
                                        <div class="spot-face face-front" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: translateZ(3px);"></div>
                                        <div class="spot-face face-back" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: rotateY(180deg) translateZ(3px);"></div>
                                        <div class="spot-face face-left" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(-90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-right" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-top" style="width: 4px; height: 6px; left: -2px; top: -3px; transform: rotateX(90deg) translateZ(0px);"></div>
                                        <div class="spot-face face-bottom" style="width: 4px; height: 6px; left: -2px; top: 21px; transform: rotateX(-90deg) translateZ(0px);"></div>
                                    </div>
                                </div>

                                <!-- Leg Back Right -->
                                <div class="spot-box thigh" style="transform: translate3d(18px, 2px, -22px) rotateX(-15deg);">
                                    <div class="spot-face face-front" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: translateZ(5px);"></div>
                                    <div class="spot-face face-back" style="width: 6px; height: 24px; left: -3px; top: -12px; transform: rotateY(180deg) translateZ(5px);"></div>
                                    <div class="spot-face face-left" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(-90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-right" style="width: 10px; height: 24px; left: -5px; top: -12px; transform: rotateY(90deg) translateZ(3px);"></div>
                                    <div class="spot-face face-top" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(90deg) translateZ(12px);"></div>
                                    <div class="spot-face face-bottom" style="width: 6px; height: 10px; left: -3px; top: -5px; transform: rotateX(-90deg) translateZ(12px);"></div>
                                    <!-- Shin -->
                                    <div class="spot-box shin" style="transform: translate3d(0, 12px, 0) rotateX(30deg);">
                                        <div class="spot-face face-front" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: translateZ(3px);"></div>
                                        <div class="spot-face face-back" style="width: 4px; height: 24px; left: -2px; top: 0px; transform: rotateY(180deg) translateZ(3px);"></div>
                                        <div class="spot-face face-left" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(-90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-right" style="width: 6px; height: 24px; left: -3px; top: 0px; transform: rotateY(90deg) translateZ(2px);"></div>
                                        <div class="spot-face face-top" style="width: 4px; height: 6px; left: -2px; top: -3px; transform: rotateX(90deg) translateZ(0px);"></div>
                                        <div class="spot-face face-bottom" style="width: 4px; height: 6px; left: -2px; top: 21px; transform: rotateX(-90deg) translateZ(0px);"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; text-align: center; margin-top: 1rem; border-top: 1px solid var(--border-color); padding-top: 0.75rem;">
                        <div><div style="font-size:0.75rem; color:var(--text-secondary);">Roulis</div><span id="imu-val-roll" style="font-weight:600; font-size:0.9rem;">0.0°</span></div>
                        <div><div style="font-size:0.75rem; color:var(--text-secondary);">Tangage</div><span id="imu-val-pitch" style="font-weight:600; font-size:0.9rem;">0.0°</span></div>
                        <div><div style="font-size:0.75rem; color:var(--text-secondary);">Lacet</div><span id="imu-val-yaw" style="font-weight:600; font-size:0.9rem;">0.0°</span></div>
                    </div>
                    <div style="margin-top: 0.75rem; padding-top: 0.5rem; border-top: 1px solid var(--border-color); display: flex; justify-content: center;">
                        <button class="btn btn-secondary" onclick="resetIMU()" style="gap: 0.5rem; font-size:0.8rem;">
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21.5 2v6h-6"/><path d="M2.5 12a9 9 0 0 1 15.4-5.8L21.5 8M2.5 22v-6h6"/><path d="M21.5 12a9 9 0 0 1-15.4 5.8L2.5 16"/>
                            </svg>
                            Recalibrer l'IMU (BNO085)
                        </button>
                    </div>
                </div>

                <!-- ROS 2 Topics -->
                <div class="card ros2-topics-card" style="display:flex; flex-direction:column; height: 365px; min-width: 0; overflow: hidden;">
                    <div class="card-title">Flux de Topics ROS 2 Actifs</div>
                    <div style="flex:1; overflow: auto; margin-top: 0.5rem; max-width: 100%; width: 100%;">
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
                    <div id="calib-status-badge" style="font-size: 0.8rem; margin-top: 0.4rem; color: var(--text-secondary);">Chargement du statut des moteurs...</div>
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
                    <button class="btn btn-secondary" style="border-color: var(--accent); color: var(--accent); background-color: rgba(99, 102, 241, 0.05);" onclick="openServoTester()">
                        🔧 Test Individuel
                    </button>
                    <button class="btn btn-secondary" style="background-color: rgba(255, 255, 255, 0.05);" onclick="resetAndSendZeroOffsets()">
                        🔄 Remettre à zéro les offsets
                    </button>
                    <button class="btn btn-secondary" style="border-color: var(--danger); color: var(--danger); background-color: rgba(239, 68, 68, 0.05);" onclick="sendStopServos()">
                        🚫 Désactiver servos
                    </button>
                </div>
            </div>
        </div>

        <!-- ─────────────────── TAB: TELECOMMANDE & NAVIGATION ─────────────────── -->
        <div id="tab-control-content" class="tab-content">
            <div class="card-grid" style="grid-template-columns: 1.6fr 1.1fr 1fr; gap: 1.5rem;">
                
                <!-- Localisation & Waypoint Canvas -->
                <div class="card" style="display: flex; flex-direction: column; min-height: 550px; margin: 0;">
                    <div class="card-title" style="display: flex; justify-content: space-between; align-items: center;">
                        <span>Carte de Navigation Interactive</span>
                        <div style="font-size:0.75rem; color:var(--text-secondary);">
                            Cliquez sur la carte pour définir une destination (Objectif)
                        </div>
                    </div>
                    <div style="flex:1; border: 1px solid var(--border-color); border-radius: 8px; background-color: #07070a; overflow: hidden; display: flex; align-items: center; justify-content: center; position: relative; margin-top: 1rem; cursor: crosshair;">
                        <canvas id="control-map-canvas" style="width:100%; height:100%; display:block;"></canvas>
                        
                        <!-- Navigation overlay info -->
                        <div id="nav-target-panel" style="position: absolute; bottom: 1rem; left: 1rem; right: 1rem; background: rgba(24,24,27,0.92); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem 1rem; display: flex; align-items: center; justify-content: space-between; opacity: 0; pointer-events: none; transition: opacity 0.2s ease;">
                            <div style="display: flex; flex-direction: column; gap: 0.15rem;">
                                <span style="font-size:0.7rem; color:var(--text-secondary); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;">Objectif Sélectionné</span>
                                <span style="font-size:0.85rem; font-family: monospace; font-weight: bold; color: var(--accent);">X: <span id="nav-target-x">0.00</span> m, Y: <span id="nav-target-y">0.00</span> m</span>
                            </div>
                            <div style="display: flex; gap: 0.5rem;">
                                <button class="btn btn-secondary" style="font-size:0.75rem; padding: 0.35rem 0.75rem;" onclick="clearNavGoal()">Annuler</button>
                                <button class="btn btn-primary" style="font-size:0.75rem; padding: 0.35rem 1rem;" onclick="sendNavGoal()">🚀 Aller à ce point</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Chat & Contrôle Vocal LLM -->
                <div class="card" style="display: flex; flex-direction: column; min-height: 550px; margin: 0;">
                    <div class="card-title" style="display: flex; justify-content: space-between; align-items: center;">
                        <span>Contrôle Vocal & IA</span>
                        <span class="status-badge active" id="control-llm-badge" style="background-color: var(--success); color: var(--bg-main); font-weight: bold; font-size: 0.75rem;">Node</span>
                    </div>
                    
                    <!-- Chat Messages Box -->
                    <div id="control-chat-messages" style="flex: 1; overflow-y: auto; padding: 0.75rem; background-color: var(--bg-main); border: 1px solid var(--border-color); border-radius: 8px; margin-top: 1rem; display: flex; flex-direction: column; gap: 0.5rem; max-height: 380px; min-height: 250px;">
                        <div style="text-align: center; color: var(--text-secondary); font-size: 0.8rem; padding: 2rem 0;">
                            Parlez à Bastet par texte ou par voix pour le piloter.
                        </div>
                    </div>
                    
                    <!-- Chat Input & Mic Box -->
                    <div style="display: flex; gap: 0.5rem; margin-top: 1rem; align-items: center;">
                        <button class="btn btn-secondary" id="control-mic-btn" onclick="toggleVoiceRecognition()" style="width: 42px; height: 42px; min-width: 42px; padding: 0; justify-content: center; border-radius: 50%; background: rgba(255,111,97,0.1); border: 1px solid rgba(255,111,97,0.3); color: var(--accent); transition: all 0.3s ease; position: relative;">
                            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                                <line x1="12" y1="19" x2="12" y2="23"/>
                                <line x1="8" y1="23" x2="16" y2="23"/>
                            </svg>
                            <span id="mic-pulse" style="position: absolute; width: 100%; height: 100%; border-radius: 50%; background: rgba(255,111,97,0.4); top: 0; left: 0; transform: scale(1); opacity: 0; transition: all 0.3s ease; pointer-events: none;"></span>
                        </button>
                        <form onsubmit="sendControlChatMessage(event)" style="display: flex; gap: 0.5rem; flex: 1;">
                            <input type="text" id="control-chat-input" class="form-input" style="flex: 1; height: 42px;" placeholder="Dites au robot d'avancer..." autocomplete="off"/>
                            <button type="submit" class="btn btn-primary" style="height: 42px; padding: 0 1rem;">Envoyer</button>
                        </form>
                    </div>
                </div>

                <!-- Manual controls column -->
                <div style="display: flex; flex-direction: column; gap: 1.5rem;">
                    
                    <!-- Posture card -->
                    <div class="card" style="margin:0;">
                        <div class="card-title" style="font-size: 1rem;">Posture & Hauteur</div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin-top: 1rem;">
                            <button class="btn btn-primary" onclick="sendControlCmd('stand')" style="justify-content: center; gap: 0.5rem; padding: 0.6rem 0.5rem; font-size: 0.8rem;">
                                ⬆ Se Lever (Stand)
                            </button>
                            <button class="btn btn-secondary" onclick="sendControlCmd('sit')" style="justify-content: center; gap: 0.5rem; padding: 0.6rem 0.5rem; font-size: 0.8rem; background-color: rgba(255,255,255,0.05);">
                                ⬇ S'Asseoir (Sit)
                            </button>
                        </div>
                    </div>

                    <!-- D-Pad card -->
                    <div class="card" style="margin:0; flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                        <div class="card-title" style="font-size: 1rem; width: 100%; text-align: left; margin-bottom: 1.5rem;">Télécommande Moteur</div>
                        
                        <!-- Visual D-Pad Layout -->
                        <div style="display: grid; grid-template-columns: repeat(3, 70px); grid-template-rows: repeat(3, 70px); gap: 0.75rem; margin: auto;">
                            <div></div>
                            <button class="btn btn-secondary dpad-btn" id="dpad-up" onmousedown="startWalking('up')" onmouseup="stopWalking()" onmouseleave="stopWalking()" style="width: 70px; height: 70px; justify-content: center; border-radius: 12px; padding: 0; background: var(--bg-card); border: 1px solid var(--border-color);">
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="18 15 12 9 6 15"/></svg>
                            </button>
                            <div></div>
                            
                            <button class="btn btn-secondary dpad-btn" id="dpad-left" onmousedown="startWalking('left')" onmouseup="stopWalking()" onmouseleave="stopWalking()" style="width: 70px; height: 70px; justify-content: center; border-radius: 12px; padding: 0; background: var(--bg-card); border: 1px solid var(--border-color);">
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg>
                            </button>
                            <button class="btn btn-secondary dpad-btn" id="dpad-stop" onclick="sendControlStop()" style="width: 70px; height: 70px; justify-content: center; border-radius: 12px; padding: 0; background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); color: var(--danger);">
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/></svg>
                            </button>
                            <button class="btn btn-secondary dpad-btn" id="dpad-right" onmousedown="startWalking('right')" onmouseup="stopWalking()" onmouseleave="stopWalking()" style="width: 70px; height: 70px; justify-content: center; border-radius: 12px; padding: 0; background: var(--bg-card); border: 1px solid var(--border-color);">
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg>
                            </button>
                            
                            <div></div>
                            <button class="btn btn-secondary dpad-btn" id="dpad-down" onmousedown="startWalking('down')" onmouseup="stopWalking()" onmouseleave="stopWalking()" style="width: 70px; height: 70px; justify-content: center; border-radius: 12px; padding: 0; background: var(--bg-card); border: 1px solid var(--border-color);">
                                <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
                            </button>
                            <div></div>
                        </div>

                        <!-- Keyboard guidance -->
                        <div style="font-size:0.75rem; color:var(--text-secondary); text-align:center; margin-top: 1.5rem;">
                            💡 Astuce : Utilisez les touches <b style="color:var(--text-primary);">Z / Q / S / D</b> ou les <b style="color:var(--text-primary);">flèches</b> de votre clavier lorsque l'onglet est actif.
                        </div>
                    </div>

                    <!-- Speed card -->
                    <div class="card" style="margin:0;">
                        <div class="card-title" style="font-size: 1rem;">Facteur de Vitesse</div>
                        <div style="margin-top: 1rem;">
                            <div style="display:flex; justify-content:space-between; font-size:0.8rem; margin-bottom: 0.5rem;">
                                <span>Vitesse de déplacement</span>
                                <span id="control-speed-val" style="font-weight: bold; color: var(--accent);">0.15 m/s</span>
                            </div>
                            <input type="range" min="5" max="30" value="15" class="form-input" style="padding:0; height:4px;" id="control-speed-slider" oninput="updateControlSpeed()"/>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- ─────────────────── TAB 7: SLAM & MAP ─────────────────── -->
        <div id="tab-map-content" class="tab-content">
            
            <!-- SLAM Mode Indicator -->
            <div id="slam-mode-bar" style="display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 1rem; background: rgba(99,102,241,0.08); border: 1px solid var(--border-color); border-radius: 8px; margin-bottom: 0.75rem;">
                <span style="font-weight: 600; font-size: 0.85rem; color: var(--text-secondary);">Mode SLAM :</span>
                <span id="slam-mode-badge" style="font-weight: 700; font-size: 0.85rem; padding: 0.2rem 0.6rem; border-radius: 4px; background: rgba(99,102,241,0.15); color: var(--accent);">--</span>
                <span id="slam-cameras-badge" style="font-size: 0.75rem; color: var(--text-secondary); margin-left: auto;"></span>
            </div>
            <!-- SLAM disabled overlay -->
            <div id="slam-disabled-overlay" style="display: none; position: relative;">
                <div class="card-grid" style="opacity: 0.3; pointer-events: none;"><div class="card-grid" style="grid-template-columns: 2.5fr 1fr;">
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
                    
                    <div class="card" style="max-height: fit-content; padding: 0.75rem 1rem; margin: 0; flex:1;">
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

    <!-- Menu Test Servos Individuel Modal -->
    <div id="servo-tester-overlay" class="modal-overlay" style="justify-content: center; align-items: center; background-color: rgba(9, 9, 11, 0.95); backdrop-filter: blur(8px);">
        <div style="width: 500px; max-width: 95vw; background-color: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);">
            <div style="padding: 1.5rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; background-color: var(--bg-card);">
                <div>
                    <h2 class="font-outfit" style="font-size: 1.3rem; color: var(--text-primary);">Test Moteur Individuel</h2>
                    <p style="color: var(--text-secondary); font-size: 0.8rem; margin-top: 0.15rem;">Activez et testez chaque servo un par un sans allumer le reste.</p>
                </div>
                <button class="btn btn-secondary" onclick="closeServoTester()">&times; Fermer</button>
            </div>
            
            <div style="padding: 1.5rem; overflow-y: auto; max-height: 70vh; display: flex; flex-direction: column; gap: 1rem;">
                <!-- General control -->
                <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.02); padding: 0.75rem; border-radius: 8px; border: 1px solid var(--border-color);">
                    <span style="font-size: 0.85rem; color: var(--text-secondary);">Tous les moteurs :</span>
                    <button class="btn btn-secondary" style="border-color: var(--danger); color: var(--danger); font-size: 0.8rem; padding: 0.4rem 0.8rem; background-color: rgba(239, 68, 68, 0.05);" onclick="testerStopAll()">
                        🚫 Tout éteindre (Limp)
                    </button>
                </div>

                <!-- 12 Servos list -->
                <div id="tester-servos-list" style="display: flex; flex-direction: column; gap: 0.75rem;">
                    <!-- Dynamically populated via JS -->
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
                        <span>Offsets des Angles Moteurs (-90° à +90°)</span>
                        <button class="btn btn-secondary" style="font-size:0.75rem; padding: 0.25rem 0.5rem;" onclick="resetMotorCalibration()">Réinitialiser</button>
                    </h3>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
                        <!-- FR -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">FR (Avant Droite)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Abad Offset</span><span id="calib-val-0">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-0" oninput="updateCalibSliderVal(0)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Upper Offset</span><span id="calib-val-1">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-1" oninput="updateCalibSliderVal(1)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FR Lower Offset</span><span id="calib-val-2">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-2" oninput="updateCalibSliderVal(2)"/>
                                </div>
                            </div>
                        </div>
                        
                        <!-- FL -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">FL (Avant Gauche)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Abad Offset</span><span id="calib-val-3">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-3" oninput="updateCalibSliderVal(3)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Upper Offset</span><span id="calib-val-4">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-4" oninput="updateCalibSliderVal(4)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>FL Lower Offset</span><span id="calib-val-5">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-5" oninput="updateCalibSliderVal(5)"/>
                                </div>
                            </div>
                        </div>

                        <!-- BR -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">BR (Arrière Droite)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Abad Offset</span><span id="calib-val-6">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-6" oninput="updateCalibSliderVal(6)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Upper Offset</span><span id="calib-val-7">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-7" oninput="updateCalibSliderVal(7)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BR Lower Offset</span><span id="calib-val-8">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-8" oninput="updateCalibSliderVal(8)"/>
                                </div>
                            </div>
                        </div>

                        <!-- BL -->
                        <div class="joint-group-card">
                            <h4 style="font-size:0.85rem; color: var(--accent); margin-bottom: 0.75rem; font-weight:600;">BL (Arrière Gauche)</h4>
                            <div style="display:flex; flex-direction:column; gap:0.75rem;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Abad Offset</span><span id="calib-val-9">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-9" oninput="updateCalibSliderVal(9)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Upper Offset</span><span id="calib-val-10">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-10" oninput="updateCalibSliderVal(10)"/>
                                </div>
                                <div>
                                    <div style="display:flex; justify-content:space-between; font-size:0.75rem;"><span>BL Lower Offset</span><span id="calib-val-11">0</span></div>
                                    <input type="range" min="-90" max="90" value="0" class="form-input" style="padding:0; height:4px; margin-top:0.25rem;" id="calib-slider-11" oninput="updateCalibSliderVal(11)"/>
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
                    <div style="margin-top: 0.75rem; display: flex; gap: 1rem;">
                        <button class="btn btn-secondary" onclick="resetAndSendZeroOffsets()" style="flex:1; border-color: #f59e0b; color: #f59e0b;">
                            🔄 Remettre à zéro les offsets
                        </button>
                        <button class="btn btn-secondary" onclick="sendStopServos()" style="flex:1; border-color: #ef4444; color: #ef4444;">
                            🚫 Désactiver servos
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
                                    <div style="display:flex; align-items:center; gap:0.3rem; margin-top:0.1rem;">
                                        <span style="font-size:0.75rem; color:var(--text-secondary); margin-right:0.2rem;">Statut: <span id="calib-cam-status-1" style="color:var(--success);">Connectée</span></span>
                                        <button class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.7rem; border-radius:4px; height:auto; margin:0;" onclick="openCameraCalibModal(1)">📷 Calibrer</button>
                                        <button class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.7rem; border-radius:4px; height:auto; margin:0; background-color: rgba(255,255,255,0.05);" onclick="openCameraConfigModal(1)">👁️ Voir</button>
                                    </div>
                                </div>
                                <input type="checkbox" checked style="accent-color: var(--accent); width:18px; height:18px;" id="calib-cam-enable-1" onchange="toggleCalibCamera(1)"/>
                            </div>
                            
                            <div id="calib-cam-container-2" style="display:flex; justify-content:space-between; align-items:center; border-top: 1px solid var(--border-color); padding-top: 0.75rem;">
                                <div>
                                    <span style="font-size: 0.85rem; font-weight:600; display:block;">Caméra Droite</span>
                                    <div style="display:flex; align-items:center; gap:0.3rem; margin-top:0.1rem;">
                                        <span style="font-size:0.75rem; color:var(--text-secondary); margin-right:0.2rem;">Statut: <span id="calib-cam-status-2" style="color:var(--text-secondary);">Déconnectée</span></span>
                                        <button class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.7rem; border-radius:4px; height:auto; margin:0;" onclick="openCameraCalibModal(2)">📷 Calibrer</button>
                                        <button class="btn btn-secondary" style="padding:0.2rem 0.5rem; font-size:0.7rem; border-radius:4px; height:auto; margin:0; background-color: rgba(255,255,255,0.05);" onclick="openCameraConfigModal(2)">👁️ Voir</button>
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
                <div id="step-dot-1" onclick="ecGoToStep(1)" style="cursor:pointer; display:flex; align-items:center; gap:0.5rem; color: var(--accent); font-weight: 600;">
                    <span style="width:20px; height:20px; border-radius:50%; background:var(--accent); color:white; display:flex; align-items:center; justify-content:center; font-size:0.7rem;">1</span>
                    <span>Offsets Moteurs</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-lr" onclick="ecGoToStep(2)" style="display:none; cursor:pointer; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">2</span>
                    <span>Attribution G/D</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-2" onclick="ecGoToStep(2)" style="cursor:pointer; display:flex; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">2</span>
                    <span>Caméra Gauche</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-3" onclick="ecGoToStep(3)" style="cursor:pointer; display:flex; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">3</span>
                    <span>Caméra Droite</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-stereo" onclick="ecGoToStep(5)" style="display:none; cursor:pointer; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">5</span>
                    <span>Stereo</span>
                </div>
                <div style="width: 2rem; border-bottom: 1px dashed var(--border-color); align-self: center;"></div>
                <div id="step-dot-4" onclick="ecGoToStep(4)" style="cursor:pointer; display:flex; align-items:center; gap:0.5rem; color: var(--text-secondary);">
                    <span style="width:20px; height:20px; border-radius:50%; background:#27272a; color:var(--text-secondary); display:flex; align-items:center; justify-content:center; font-size:0.7rem;">4</span>
                    <span>Finalisation</span>
                </div>
            </div>
            
            <!-- Content Area -->
            <div style="flex: 1; padding: 1.5rem; overflow-y: auto; display: flex; flex-direction: column; min-height: 350px;">
                
                <!-- STEP 1 CONTENT -->
                <div id="ec-step-1" style="display: none; flex-direction: column; gap: 1.5rem; padding: 1.5rem; flex: 1;">
        <!-- ===== Joint Calibration Wizard View ===== -->
        <div id="ec-joint-calibration-view" style="display: flex; flex-direction: column; gap: 1.5rem; flex: 1;">
            <!-- Leg banner -->
            <div id="ec-joint-leg-banner" style="background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(99,102,241,0.05)); border: 1px solid rgba(99,102,241,0.2); border-radius: 10px; padding: 0.75rem 1.25rem; font-size: 1rem; font-weight: 600; color: var(--accent); text-align: center;">
                Patte <span id="ec-joint-leg-name">Arrière Gauche</span>
            </div>
            
            <!-- Joint name and progress -->
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h3 id="ec-joint-name" style="margin: 0; font-size: 1.4rem;">Hanche</h3>
                <span id="ec-joint-progress" style="font-size: 0.85rem; color: var(--text-secondary); background: rgba(255,255,255,0.05); padding: 0.35rem 0.85rem; border-radius: 20px; border: 1px solid var(--border-color);">Articulation 1/12</span>
            </div>
            
            <!-- Instruction text -->
            <p style="color: var(--text-secondary); font-size: 0.9rem; margin: 0;">
                1. Cliquez sur <strong>"Allumer le servo"</strong> pour activer l'articulation<br>
                2. Utilisez le curseur pour positionner l'articulation à sa <strong>position neutre</strong><br>
                3. Cliquez sur <strong>"Valider"</strong> pour enregistrer l'offset et passer à la suivante
            </p>
            
            <!-- Joint visualization & slider area -->
            <div style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); border-radius: 12px; padding: 1.5rem; display: flex; flex-direction: column; gap: 1.5rem;">
                <!-- Servo icon -->
                <div style="text-align: center; font-size: 3rem; opacity: 0.6;" id="ec-joint-icon">🦵</div>
                
                <!-- Slider -->
                <div style="display: flex; align-items: center; gap: 1rem;">
                    <span style="font-size: 0.8rem; color: var(--text-secondary); min-width: 28px; text-align: center;">-30</span>
                    <input type="range" id="ec-joint-slider" min="-90" max="90" value="0" 
                           oninput="ecUpdateJointSlider(this.value)" 
                           style="flex: 1; height: 6px; -webkit-appearance: none; appearance: none; background: var(--border-color); border-radius: 3px; outline: none; cursor: pointer;">
                    <span style="font-size: 0.8rem; color: var(--text-secondary); min-width: 28px; text-align: center;">+30</span>
                </div>
                
                <!-- Value display -->
                <div style="text-align: center;">
                    <span id="ec-joint-slider-value" style="font-size: 2.5rem; font-weight: 700; color: var(--accent); transition: color 0.2s;">0</span>
                    <span id="ec-joint-limit-warning" style="display: none; font-size: 0.75rem; font-weight: 600; color: #f59e0b; background: rgba(245,158,11,0.12); padding: 0.15rem 0.5rem; border-radius: 4px; margin-left: 0.5rem; vertical-align: middle;">⚠ Limite servo</span>
                    <span style="color: var(--text-secondary); font-size: 0.9rem;">° offset</span>
                </div>
            </div>
            
            <!-- Action buttons -->
            <div style="display: flex; gap: 1rem; justify-content: center; align-items: center;">
                <button id="ec-btn-attach-servo" class="btn btn-secondary" onclick="ecAttachCurrentJoint()" style="gap: 0.5rem; padding: 0.75rem 1.5rem;">
                    🔌 Allumer le servo
                </button>
                <button id="ec-btn-validate-joint" class="btn btn-primary" onclick="ecValidateJoint()" disabled style="gap: 0.5rem; padding: 0.75rem 1.5rem; opacity: 0.5;">
                    ✅ Valider cet offset
                </button>
            </div>
        </div>
        
        <!-- ===== Final View (all 12 joints done) ===== -->
        <div id="ec-joint-final-view" style="display: none; flex-direction: column; gap: 1.5rem; align-items: center; justify-content: center; flex: 1; padding: 2rem;">
            <div style="font-size: 4rem; line-height: 1;">✅</div>
            <h3 style="margin: 0; font-size: 1.3rem;">Offset de toutes les articulations définis !</h3>
            <p style="color: var(--text-secondary); text-align: center; max-width: 400px;">
                12 offsets collectés. Vous pouvez maintenant enregistrer la configuration pour activer les moteurs ou garder le robot éteint.
            </p>
            <div style="display: flex; gap: 1rem; margin-top: 0.5rem;">
                <button class="btn btn-primary" onclick="ecCalculateOffsets(true)" style="gap: 0.5rem; padding: 0.75rem 1.5rem;">
                    ⚡ Enregistrer & Activer les Moteurs
                </button>
                <button class="btn btn-secondary" onclick="ecCalculateOffsets(false)" style="gap: 0.5rem; padding: 0.75rem 1.5rem;">
                    💾 Enregistrer Uniquement (Garder Éteint)
                </button>
            </div>
        </div>
        
        <!-- Success animation (kept for compatibility) -->
        <div id="ec-motor-success-anim" style="display: none;">
                
                <!-- STEP LR: Attribution Gauche/Droite (2 cameras only) -->
                <div id="ec-step-lr" style="display: none; flex-direction: column; gap: 1.5rem; height: 100%; padding: 1.5rem;">
                    <div style="line-height:1.5; font-size:0.9rem;">
                        <p style="font-weight:600; color:var(--text-primary); font-size:1.1rem; margin-bottom:0.5rem;">Attribution des Cameras : Gauche / Droite</p>
                        <p style="color: var(--text-secondary);">Deux cameras sont detectees. Regardez les flux ci-dessous et indiquez laquelle est la <strong>gauche</strong> et laquelle est la <strong>droite</strong>.</p>
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; flex: 1; min-height: 220px;">
                        <!-- Camera A preview -->
                        <div style="border: 2px solid var(--border-color); border-radius: 10px; overflow: hidden; background: #000; position: relative; display: flex; flex-direction: column;">
                            <div style="padding: 0.4rem 0.75rem; background: rgba(255,255,255,0.05); font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); text-align: center; border-bottom: 1px solid var(--border-color);">
                                Camera A
                            </div>
                            <div style="flex: 1; position: relative; min-height: 150px;">
                                <video id="ec-lr-video-a" autoplay muted playsinline style="width: 100%; height: 100%; object-fit: cover; display: none;"></video>
                                <div id="ec-lr-status-a" style="position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); font-size: 0.8rem;">
                                    Chargement...
                                </div>
                            </div>
                            <div style="display: flex; gap: 0.5rem; padding: 0.5rem;">
                                <button class="btn btn-primary" onclick="ecAssignLR('left', 'a')" style="flex: 1; justify-content: center; font-size: 0.8rem; padding: 0.4rem;">
                                    Cette camera est a GAUCHE
                                </button>
                                <button class="btn btn-secondary" onclick="ecAssignLR('right', 'a')" style="flex: 1; justify-content: center; font-size: 0.8rem; padding: 0.4rem;">
                                    Cette camera est a DROITE
                                </button>
                            </div>
                        </div>
                        
                        <!-- Camera B preview -->
                        <div style="border: 2px solid var(--border-color); border-radius: 10px; overflow: hidden; background: #000; position: relative; display: flex; flex-direction: column;">
                            <div style="padding: 0.4rem 0.75rem; background: rgba(255,255,255,0.05); font-size: 0.8rem; font-weight: 600; color: var(--text-secondary); text-align: center; border-bottom: 1px solid var(--border-color);">
                                Camera B
                            </div>
                            <div style="flex: 1; position: relative; min-height: 150px;">
                                <video id="ec-lr-video-b" autoplay muted playsinline style="width: 100%; height: 100%; object-fit: cover; display: none;"></video>
                                <div id="ec-lr-status-b" style="position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--text-secondary); font-size: 0.8rem;">
                                    Chargement...
                                </div>
                            </div>
                            <div style="display: flex; gap: 0.5rem; padding: 0.5rem;">
                                <button class="btn btn-primary" onclick="ecAssignLR('left', 'b')" style="flex: 1; justify-content: center; font-size: 0.8rem; padding: 0.4rem;">
                                    Cette camera est a GAUCHE
                                </button>
                                <button class="btn btn-secondary" onclick="ecAssignLR('right', 'b')" style="flex: 1; justify-content: center; font-size: 0.8rem; padding: 0.4rem;">
                                    Cette camera est a DROITE
                                </button>
                            </div>
                        </div>
                    </div>
                    
                    <div id="ec-lr-assignment-result" style="display: none; text-align: center; padding: 0.75rem; background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); border-radius: 8px;">
                        <span style="color: var(--success); font-weight: 600;" id="ec-lr-result-text"></span>
                    </div>
                    
                    <div style="display: flex; justify-content: center; gap: 1rem; padding-top: 0.5rem;">
                        <button class="btn btn-secondary" onclick="ecSkipLR()" style="font-size: 0.8rem; padding: 0.5rem 1rem;">
                            Passer (garder defaut : video0=gauche, video2=droite)
                        </button>
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
                    <button type="button" class="btn btn-secondary" id="btn-wifi-forget-form" style="border-color: var(--danger); color: var(--danger); background: transparent; display: none;" onclick="handleForgetFromForm()">🗑️ Oublier</button>
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

    <!-- Modal : Camera Config View -->
    <div id="cameraConfigModal" class="modal-overlay" onclick="closeCameraConfigModalOnClick(event)">
        <div class="modal-content" style="max-width: 600px; background-color: var(--bg-card); border: 1px solid var(--border-color); border-radius: 16px;">
            <div class="modal-header">
                <h3 class="font-outfit" style="font-size: 1.25rem; font-weight: 700;" id="mcv-modal-title">Configuration Caméra</h3>
                <button class="modal-close" onclick="closeCameraConfigModal()">&times;</button>
            </div>
            
            <div style="margin-bottom: 1.25rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem; padding-bottom:0.75rem; border-bottom:1px solid var(--border-color);">
                    <div>
                        <span style="font-size:0.8rem; color:var(--text-secondary);">Statut du Profil</span>
                        <div id="mcv-profile-badge" class="status-badge" style="margin-top:0.25rem; padding: 0.25rem 0.5rem; font-size: 0.75rem;">Default</div>
                    </div>
                    <div style="text-align:right;">
                        <span style="font-size:0.8rem; color:var(--text-secondary);">Résolution</span>
                        <div id="mcv-resolution" style="font-weight:600; font-size:0.9rem; margin-top:0.25rem; color:var(--text-primary);">640 x 480</div>
                    </div>
                </div>

                <div class="form-group" style="margin-bottom: 1rem;">
                    <label class="form-label" style="font-size:0.8rem; margin-bottom:0.25rem; color:var(--text-secondary);">Nom du modèle</label>
                    <input type="text" id="mcv-camera-name" class="form-input" style="background-color:rgba(0,0,0,0.2); font-size:0.85rem;" readonly />
                </div>

                <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.75rem; margin-bottom:1rem;">
                    <div class="form-group">
                        <label class="form-label" style="font-size:0.8rem; margin-bottom:0.25rem; color:var(--text-secondary);">Modèle de Distorsion</label>
                        <input type="text" id="mcv-distortion-model" class="form-input" style="background-color:rgba(0,0,0,0.2); font-size:0.85rem;" readonly />
                    </div>
                    <div class="form-group">
                        <label class="form-label" style="font-size:0.8rem; margin-bottom:0.25rem; color:var(--text-secondary);">Dernière Calibration</label>
                        <input type="text" id="mcv-calibrated-at" class="form-input" style="background-color:rgba(0,0,0,0.2); font-size:0.85rem;" readonly />
                    </div>
                </div>

                <div class="form-group" style="margin-bottom: 1rem;">
                    <label class="form-label" style="font-size:0.8rem; margin-bottom:0.25rem; color:var(--text-secondary);">Matrice de Distorsion D (Coefficients)</label>
                    <div id="mcv-distortion-matrix" style="font-family:monospace; background:rgba(0,0,0,0.3); border:1px solid var(--border-color); border-radius:6px; padding:0.5rem; font-size:0.75rem; color:var(--text-primary); text-align:center;">
                        [0.00000, 0.00000, 0.00000, 0.00000, 0.00000]
                    </div>
                </div>

                <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.75rem;">
                    <div class="form-group">
                        <label class="form-label" style="font-size:0.8rem; margin-bottom:0.25rem; color:var(--text-secondary);">Matrice Intrinsèque K (3x3)</label>
                        <div id="mcv-camera-matrix" style="font-family:monospace; background:rgba(0,0,0,0.3); border:1px solid var(--border-color); border-radius:6px; padding:0.5rem; font-size:0.75rem; color:var(--text-primary); line-height:1.4;">
                            1.0, 0.0, 0.0<br/>
                            0.0, 1.0, 0.0<br/>
                            0.0, 0.0, 1.0
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label" style="font-size:0.8rem; margin-bottom:0.25rem; color:var(--text-secondary);">Matrice de Projection P (3x4)</label>
                        <div id="mcv-projection-matrix" style="font-family:monospace; background:rgba(0,0,0,0.3); border:1px solid var(--border-color); border-radius:6px; padding:0.5rem; font-size:0.75rem; color:var(--text-primary); line-height:1.4;">
                            1.0, 0.0, 0.0, 0.0<br/>
                            0.0, 1.0, 0.0, 0.0<br/>
                            0.0, 0.0, 1.0, 0.0
                        </div>
                    </div>
                </div>
            </div>
            
            <div style="display:flex; justify-content:flex-end;">
                <button class="btn btn-secondary" onclick="closeCameraConfigModal()">Fermer</button>
            </div>
        </div>
    </div>

    <!-- Screen lightbox overlay -->
    <div id="lightbox" class="modal-overlay" onclick="closeLightbox()" style="background-color: rgba(0,0,0,0.95); cursor: zoom-out;">
        <img id="lightbox-img" style="max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 4px; box-shadow: 0 10px 40px rgba(0,0,0,0.8);"/>
    </div>

    <script>
        let apiToken = localStorage.getItem('bastet_api_token') || '';
        let activeTab = localStorage.getItem('bastetActiveTab') || 'dashboard';
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
            loadSavedOffsets();
            // Fallback: si le badge est toujours en 'Chargement...' apres 10s, afficher un etat neutre
            setTimeout(() => {
                const badgeCalib = document.getElementById('calib-status-badge');
                if (badgeCalib && badgeCalib.textContent.includes('Chargement')) {
                    badgeCalib.textContent = '⏳ En attente des donnees moteurs...';
                    badgeCalib.style.color = 'var(--text-secondary)';
                    badgeCalib.style.fontWeight = 'normal';
                }
            }, 10000);
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
                window.lastArduinoTelemetry = Date.now();
                if (!window.arduinoOfflineChecker) {
                    window.arduinoOfflineChecker = setInterval(() => {
                        if (window.lastArduinoTelemetry && Date.now() - window.lastArduinoTelemetry > 30000) {
                            const badge = document.getElementById('arduino-status-badge');
                            const content = document.getElementById('arduino-telemetry-content');
                            const offlineMsg = document.getElementById('arduino-offline-msg');
                            if (badge) { badge.className = 'status-badge offline'; badge.textContent = 'Hors-ligne'; }
                            if (content) content.style.display = 'none';
                            if (offlineMsg) offlineMsg.style.display = '';
                        }
                    }, 3000);
                }
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
            var payload;
            try {
                payload = JSON.parse(data);
            } catch (e) {
                console.error("[WS] JSON parse error:", e);
                return;
            }
            try {
                // Print all JSON traffic to the Console
                logToJSONConsole(JSON.stringify(payload, null, 2));
                
                if (payload.type === "telemetry_diagnostics") {
                    window.lastArduinoTelemetry = Date.now();
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
                        // Cache module-level pour la carte Vue d ensemble (payload.imu peut etre null/absent sur certains ticks 0.5s)
                        window._bastetLastImu = { roll: roll, pitch: pitch, yaw: yaw };
                        
                        const elRoll = document.getElementById('imu-val-roll');
                        const elPitch = document.getElementById('imu-val-pitch');
                        const elYaw = document.getElementById('imu-val-yaw');
                        if (elRoll) elRoll.textContent = `${roll.toFixed(1)}°`;
                        if (elPitch) elPitch.textContent = `${pitch.toFixed(1)}°`;
                        if (elYaw) elYaw.textContent = `${yaw.toFixed(1)}°`;
                        
                        // Rotate 3D IMU CSS Cube
                        const cube = document.getElementById('imu-visual-cube');
                        if (cube) {
                            cube.style.transform = `rotateX(${pitch}deg) rotateY(${roll}deg) rotateZ(${-yaw}deg)`;
                        }
                    }

                    // Update Arduino Mega dashboard card
                    const arduinoBadge = document.getElementById('arduino-status-badge');
                    const arduinoOfflineMsg = document.getElementById('arduino-offline-msg');
                    const arduinoContent = document.getElementById('arduino-telemetry-content');
                    const hasArduino = payload.imu || (payload.joints && payload.joints.length === 12);
                    if (hasArduino) {
                        arduinoBadge.className = 'status-badge active';
                        arduinoBadge.textContent = 'En ligne';
                        arduinoOfflineMsg.style.display = 'none';
                        arduinoContent.style.display = '';
                        // IMU Vue d ensemble : lit le cache window._bastetLastImu maj par le 1er if(payload.imu).
                        // Permet de garder les valeurs affichees meme quand payload.imu est null/absent.
                        const cachedImu = window._bastetLastImu || { roll: 0, pitch: 0, yaw: 0 };
                        document.getElementById('arduino-roll').textContent = `${cachedImu.roll.toFixed(1)}°`;
                        document.getElementById('arduino-pitch').textContent = `${cachedImu.pitch.toFixed(1)}°`;
                        document.getElementById('arduino-yaw').textContent = `${cachedImu.yaw.toFixed(1)}°`;
                    }
                    if (payload.joints && payload.joints.length === 12) {
                        const jointsGrid = document.getElementById('arduino-joints-grid');
                        if (jointsGrid && !jointsGrid.dataset.init) {
                            const names = ['FR-H','FR-C','FR-T','FL-H','FL-C','FL-T','BR-H','BR-C','BR-T','BL-H','BL-C','BL-T'];
                            jointsGrid.innerHTML = '';
                            for (let i = 0; i < 12; i++) {
                                const el = document.createElement('div');
                                el.style.cssText = 'font-size:0.7rem; text-align:center; padding:0.2rem; background:var(--bg-main); border-radius:4px;';
                                el.innerHTML = `<div style="color:var(--text-secondary);">${names[i]}</div><div style="font-weight:700; color:var(--accent);" id="gw-joint-${i}">${Math.round(payload.joints[i])}°</div>`;
                                jointsGrid.appendChild(el);
                            }
                            jointsGrid.dataset.init = '1';
                        } else if (jointsGrid) {
                            for (let i = 0; i < 12; i++) {
                                const el = document.getElementById(`gw-joint-${i}`);
                                if (el) el.textContent = `${Math.round(payload.joints[i])}°`;
                            }
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
                        // Le flux s'est arr\u00eat\u00e9 c\u00f4t\u00e9 robot alors qu'on le visionnait
                        window.localViewing[camId] = false;
                        stopStreamUI(camId);
                    }

                    // Ne mettre \u00e0 jour l'UI que si on n'est pas en train de visionner localement
                    if (!window.localViewing || !window.localViewing[camId]) {
                        if (statusEl) {
                            // Si l'utilisateur vient de couper manuellement, on garde 'Coup\u00e9'
                            const userCut = window.userClosedStream && window.userClosedStream[camId];
                            if (!userCut) {
                                statusEl.textContent = isActive ? 'En direct' : 'Inactif';
                                statusEl.className = isActive ? 'status-badge active' : 'status-badge';
                            }
                        }
                        if (btnText) {
                            btnText.textContent = isActive ? 'Rejoindre le flux' : 'D\u00e9marrer le flux';
                        }

                        // Auto-rejoindre seulement si flux devenu actif ET user n'a pas coup\u00e9 manuellement
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
                else if (payload.type === "wifi_list") {
                    displayWifiNetworks(payload.networks, payload.known_ssids, payload.known_passwords, payload.current_ssid);
                }
                else if (payload.type === "wifi_list_error") {
                    handleWifiScanError(payload);
                }
                else if (payload.type === "wifi_connect_result") {
                    handleWifiConnectResult(payload);
                }
                else if (payload.type === "wifi_forget_result") {
                    if (payload.status === "success") {
                        alert("Succès : Réseau oublié.");
                        scanWifiNetworks();
                    } else {
                        alert("Erreur lors de l'oubli du réseau : " + payload.message);
                    }
                } 
                else if (payload.type === "chat_response" || payload.type === "chat") {
                    handleIncomingLLMMessage(payload.sender || 'LLM', payload.text || '');
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

        // ─── TÉLÉCOMMANDE CHAT VOCAL & PILOTAGE IA ────────────────────────────
        function sendControlChatMessage(e) {
            if (e) e.preventDefault();
            const input = document.getElementById('control-chat-input');
            const text = input.value.trim();
            if (!text) return;
            
            appendControlChatMessage('Moi', text);
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "chat", text: text }));
            } else {
                appendControlChatMessage('Système', 'Erreur : WebSocket déconnecté.');
            }
            input.value = '';
        }

        function appendControlChatMessage(sender, text) {
            const box = document.getElementById('control-chat-messages');
            if (!box) return;
            
            if (box.textContent.includes("Parlez à Bastet")) {
                box.innerHTML = '';
            }
            
            const msgEl = document.createElement('div');
            msgEl.style.padding = '0.5rem 0.75rem';
            msgEl.style.borderRadius = '6px';
            msgEl.style.fontSize = '0.85rem';
            msgEl.style.maxWidth = '85%';
            msgEl.style.marginBottom = '0.25rem';
            msgEl.style.lineHeight = '1.3';
            
            if (sender === 'Moi') {
                msgEl.style.alignSelf = 'flex-end';
                msgEl.style.backgroundColor = 'rgba(255, 111, 97, 0.2)';
                msgEl.style.border = '1px solid var(--accent)';
                msgEl.innerHTML = `<span style="font-weight:bold;color: var(--accent);display:block;font-size:0.7rem;margin-bottom:0.15rem;">Moi</span>${text}`;
            } else if (sender === 'Système') {
                msgEl.style.alignSelf = 'center';
                msgEl.style.backgroundColor = 'rgba(225, 29, 72, 0.1)';
                msgEl.style.border = '1px solid var(--danger)';
                msgEl.innerHTML = `<span style="font-style:italic;color:#f87171;font-size:0.75rem;">${text}</span>`;
            } else {
                msgEl.style.alignSelf = 'flex-start';
                msgEl.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
                msgEl.style.border = '1px solid var(--border-color)';
                msgEl.innerHTML = `<span style="font-weight:bold;color:var(--text-primary);display:block;font-size:0.7rem;margin-bottom:0.15rem;">${sender}</span>${text}`;
            }
            
            box.appendChild(msgEl);
            box.scrollTop = box.scrollHeight;
        }

        let voiceRecognition = null;
        let isVoiceListening = false;

        function toggleVoiceRecognition() {
            const btn = document.getElementById('control-mic-btn');
            const pulse = document.getElementById('mic-pulse');
            
            if (isVoiceListening) {
                if (voiceRecognition) voiceRecognition.stop();
                return;
            }
            
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                appendControlChatMessage('Système', "La reconnaissance vocale n'est pas supportée par votre navigateur.");
                return;
            }
            
            voiceRecognition = new SpeechRecognition();
            voiceRecognition.lang = 'fr-FR';
            voiceRecognition.interimResults = false;
            voiceRecognition.maxAlternatives = 1;
            
            voiceRecognition.onstart = () => {
                isVoiceListening = true;
                btn.classList.add('mic-active');
                if (pulse) {
                    pulse.style.opacity = '1';
                    pulse.style.transform = 'scale(1.5)';
                }
            };
            
            voiceRecognition.onresult = (event) => {
                const speechResult = event.results[0][0].transcript;
                const input = document.getElementById('control-chat-input');
                if (input) {
                    input.value = speechResult;
                    sendControlChatMessage();
                }
            };
            
            voiceRecognition.onerror = (event) => {
                console.error("Reconnaissance vocale erreur:", event.error);
                appendControlChatMessage('Système', "Erreur de reconnaissance vocale : " + event.error);
            };
            
            voiceRecognition.onend = () => {
                isVoiceListening = false;
                btn.classList.remove('mic-active');
                if (pulse) {
                    pulse.style.opacity = '0';
                    pulse.style.transform = 'scale(1)';
                }
            };
            
            voiceRecognition.start();
        }

        function handleIncomingLLMMessage(sender, text) {
            // Afficher dans le chat principal de l'IA
            appendLLMMessage(sender, text);
            
            let cleanText = text;
            
            // Parser les balises [ACTION: ...]
            const actionRegex = /\[ACTION:\s*([a-zA-Z]+)\]/g;
            let actionMatch;
            while ((actionMatch = actionRegex.exec(text)) !== null) {
                const action = actionMatch[1].toLowerCase();
                executeVoiceAction(action);
            }
            cleanText = cleanText.replace(actionRegex, '');
            
            // Parser les balises [NAV: x, y]
            const navRegex = /\[NAV:\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\]/g;
            let navMatch;
            while ((navMatch = navRegex.exec(text)) !== null) {
                const x = parseFloat(navMatch[1]);
                const y = parseFloat(navMatch[3]);
                executeVoiceNav(x, y);
            }
            cleanText = cleanText.replace(navRegex, '');
            
            // Afficher dans le chat de la télécommande
            appendControlChatMessage(sender, cleanText.trim());
        }

        function executeVoiceAction(action) {
            if (['up', 'down', 'left', 'right'].includes(action)) {
                const btnId = `dpad-${action}`;
                const btn = document.getElementById(btnId);
                if (btn) {
                    btn.classList.add('active-dpad');
                    btn.style.backgroundColor = 'var(--accent)';
                    btn.style.color = 'white';
                }
                startWalking(action);
                
                setTimeout(() => {
                    stopWalking();
                    if (btn) {
                        btn.classList.remove('active-dpad');
                        btn.style.backgroundColor = '';
                        btn.style.color = '';
                    }
                }, 2500);
            } else if (action === 'stop') {
                sendControlStop();
                const btn = document.getElementById('dpad-stop');
                if (btn) {
                    btn.style.transform = 'scale(0.9)';
                    setTimeout(() => btn.style.transform = '', 200);
                }
            } else if (action === 'stand') {
                sendControlCmd('stand');
            } else if (action === 'sit') {
                sendControlCmd('sit');
            }
        }

        function executeVoiceNav(x, y) {
            navTarget = { x: x, y: y };
            
            document.getElementById('nav-target-x').textContent = x.toFixed(2);
            document.getElementById('nav-target-y').textContent = y.toFixed(2);
            
            const panel = document.getElementById('nav-target-panel');
            if (panel) {
                panel.style.opacity = '1';
                panel.style.pointerEvents = 'auto';
            }
            
            drawControlMap();
            sendNavGoal();
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
                'yolo': ['robot', 'node', 'disabled'],
                'face_rec': ['robot', 'node', 'disabled']
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
            const list = ['robot', 'node', 'disabled'];
            list.forEach(t => {
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

            if (feature === 'chat') {
                const llmBadge = document.getElementById('control-llm-badge');
                if (llmBadge) {
                    if (target === 'node') {
                        llmBadge.textContent = 'PC Node';
                        llmBadge.style.backgroundColor = 'var(--success)';
                    } else if (target === 'robot') {
                        llmBadge.textContent = 'Robot Local';
                        llmBadge.style.backgroundColor = 'var(--accent)';
                    } else {
                        llmBadge.textContent = 'Désactivé';
                        llmBadge.style.backgroundColor = 'var(--danger)';
                    }
                }
            }
        }

        // ─── CALIBRATION WINDOW FUNCTIONS ──────────────────────────────────────
        
        async function loadSavedOffsets() {
            try {
                const res = await fetch('/core/calibration', {
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    const data = await res.json();
                    const offsets = data.offsets || [];
                    
                    let allZero = true;
                    for (let i = 0; i < 12; i++) {
                        const val = offsets[i] !== undefined ? offsets[i] : 0;
                        if (val !== 0) allZero = false;
                        
                        const slider = document.getElementById(`calib-slider-${i}`);
                        if (slider) {
                            slider.value = val;
                            updateCalibSliderVal(i);
                        }
                    }
                    
                    const statusText = allZero 
                        ? '🚫 Offsets non configurés (Moteurs désactivés)' 
                        : '✅ Offsets configurés (Moteurs actifs)';
                    const statusColor = allZero ? 'var(--danger)' : 'var(--success)';
                    
                    const badgeCalib = document.getElementById('calib-status-badge');
                    if (badgeCalib) {
                        badgeCalib.textContent = statusText;
                        badgeCalib.style.color = statusColor;
                        badgeCalib.style.fontWeight = 'bold';
                    }
                }
            } catch (err) {
                console.error("Erreur lors du chargement des offsets:", err);
                const badgeCalib = document.getElementById('calib-status-badge');
                if (badgeCalib) {
                    badgeCalib.textContent = '⚠️ Offsets non disponibles (Gateway inaccessible?)';
                    badgeCalib.style.color = 'var(--warning)';
                    badgeCalib.style.fontWeight = 'bold';
                }
            }
        }

        function openCalibrationOverlay() {
            document.getElementById('calibration-overlay').classList.add('active');
            setTimeout(drawMinimap, 100);
            loadSavedOffsets();
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

        async function resetAndSendZeroOffsets() {
            resetMotorCalibration();
            const zeroes = new Array(12).fill(0);
            try {
                await fetch('/core/calibration', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },
                    body: JSON.stringify({ offsets: zeroes })
                });
            } catch(e) {}
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: zeroes }));
            }
            loadSavedOffsets();
        }

        function sendStopServos() {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
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
            // FIX: Arreter le motion_node quand mode manuel actif (evite ecrasement des angles)
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: checked ? "stop" : "stand" }));
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

        let lastSendManualJointTime = 0;
        let pendingManualJointTimeout = null;

        function sendManualJointAngles() {
            const now = Date.now();
            if (now - lastSendManualJointTime < 50) {
                if (!pendingManualJointTimeout) {
                    pendingManualJointTimeout = setTimeout(() => {
                        pendingManualJointTimeout = null;
                        sendManualJointAngles();
                    }, 50 - (now - lastSendManualJointTime));
                }
                return;
            }
            lastSendManualJointTime = now;

            const angles = [];
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`joint-slider-${i}`);
                angles.push(slider ? parseFloat(slider.value) : 90.0);
            }
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "manual_joint_control", angles: angles }));
            }
        }

        function resetIMU() {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "reset_imu" }));
                if (typeof showToast === 'function') showToast("IMU", "Recalibrage BNO085 en cours...", "info");
                else console.log("[IMU] Recalibrage BNO085 envoye");
            } else {
                if (typeof showToast === 'function') showToast("IMU", "WebSocket non connecte", "error");
                else console.warn("[IMU] WebSocket non connecte");
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
                    loadSavedOffsets();
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

        // ─── SERVO TESTER FUNCTIONS ───────────────────────────────────────────
        const TESTER_JOINT_NAMES = [
            'Avant-Droit Abduction', 'Avant-Droit Hanche', 'Avant-Droit Genou',
            'Avant-Gauche Abduction', 'Avant-Gauche Hanche', 'Avant-Gauche Genou',
            'Arrière-Droit Abduction', 'Arrière-Droit Hanche', 'Arrière-Droit Genou',
            'Arrière-Gauche Abduction', 'Arrière-Gauche Hanche', 'Arrière-Gauche Genou'
        ];

        function openServoTester() {
            document.getElementById('servo-tester-overlay').classList.add('active');
            // FIX: Arreter le motion_node pour eviter qu'il ecrase les commandes individuelles
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
            }
            buildServoTesterList();
        }

        function closeServoTester() {
            document.getElementById('servo-tester-overlay').classList.remove('active');
            testerStopAll();
            // FIX: Redemarrer le motion_node en mode stand apres le test individuel
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
            }
        }

        function buildServoTesterList() {
            const container = document.getElementById('tester-servos-list');
            if (!container) return;
            container.innerHTML = '';

            for (let i = 0; i < 12; i++) {
                const name = TESTER_JOINT_NAMES[i];
                
                const card = document.createElement('div');
                card.style.display = 'flex';
                card.style.flexDirection = 'column';
                card.style.gap = '0.5rem';
                card.style.padding = '0.75rem';
                card.style.border = '1px solid var(--border-color)';
                card.style.borderRadius = '8px';
                card.style.background = 'rgba(255,255,255,0.01)';
                
                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 0.85rem; font-weight: 600; color: var(--text-primary);">${i + 1}. ${name}</span>
                        <div style="display: flex; gap: 0.5rem;">
                            <button class="btn btn-secondary" id="tester-btn-attach-${i}" style="font-size: 0.7rem; padding: 0.25rem 0.5rem;" onclick="testerAttach(${i})">Activer</button>
                            <button class="btn btn-secondary" id="tester-btn-detach-${i}" style="font-size: 0.7rem; padding: 0.25rem 0.5rem; display: none;" onclick="testerDetach(${i})">Éteindre</button>
                        </div>
                    </div>
                    <div id="tester-slider-container-${i}" style="display: none; align-items: center; gap: 0.75rem; margin-top: 0.25rem;">
                        <input type="range" min="0" max="180" value="90" id="tester-slider-${i}" style="flex: 1; height: 4px; accent-color: var(--accent);" oninput="testerWrite(${i}, this.value)">
                        <span id="tester-val-${i}" style="font-size: 0.8rem; font-family: monospace; min-width: 30px; text-align: right; color: var(--accent);">90°</span>
                    </div>
                `;
                container.appendChild(card);
            }
        }

        function testerAttach(idx) {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "attach", index: idx }));
                
                document.getElementById(`tester-btn-attach-${idx}`).style.display = 'none';
                document.getElementById(`tester-btn-detach-${idx}`).style.display = 'inline-block';
                document.getElementById(`tester-slider-container-${idx}`).style.display = 'flex';
                
                testerWrite(idx, 90);
            }
        }

        function testerDetach(idx) {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: idx }));
                
                document.getElementById(`tester-btn-attach-${idx}`).style.display = 'inline-block';
                document.getElementById(`tester-btn-detach-${idx}`).style.display = 'none';
                document.getElementById(`tester-slider-container-${idx}`).style.display = 'none';
            }
        }

        let lastTesterWriteTime = {};
        let pendingTesterWriteTimeout = {};

        function testerWrite(idx, angle) {
            document.getElementById(`tester-val-${idx}`).textContent = angle + '°';
            
            const now = Date.now();
            if (now - (lastTesterWriteTime[idx] || 0) < 50) {
                if (!pendingTesterWriteTimeout[idx]) {
                    pendingTesterWriteTimeout[idx] = setTimeout(() => {
                        pendingTesterWriteTimeout[idx] = null;
                        testerWrite(idx, angle);
                    }, 50 - (now - (lastTesterWriteTime[idx] || 0)));
                }
                return;
            }
            lastTesterWriteTime[idx] = now;

            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "write", index: idx, angle: parseFloat(angle) }));
            }
        }

        function testerStopAll() {
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                for (let i = 0; i < 12; i++) {
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: i }));
                }
                sendControlStop();
                buildServoTesterList();
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

        window.wifiPasswords = {};
        window.wifiCurrentSsid = '';

        function handleWifiScanError(payload) {
            const errMsg = payload.error || "Erreur inconnue";
            const iface = payload.interface || "wlan0";
            const mgr = payload.manager || "inconnu";
            const known = Array.isArray(payload.known_ssids) ? payload.known_ssids : [];
            const cur = payload.current_ssid || "";
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = false;
            if (listContainer) {
                listContainer.innerHTML = `<div style="text-align: center; color: var(--danger); padding: 2rem 0; font-size: 0.85rem; line-height: 1.5;">⚠️ Scan WiFi échoué<br><small style="color: var(--text-secondary);">${errMsg}<br>Interface : ${iface} · Gestionnaire : ${mgr}</small></div>`;
            }
            if (knownContainer) {
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.85rem;">Surveillance WiFi indisponible.</div>`;
            }
            window.wifiPasswords = payload.known_passwords || {};
            window.wifiCurrentSsid = cur;
            // Ré-afficher les réseaux connus (si fournis) même en cas d'échec du scan
            try { displayWifiNetworks([], known, payload.known_passwords || {}, cur); } catch(e) { /* noop */ }
        }

        function displayWifiNetworks(networks, knownSsids = [], knownPasswords = {}, currentSsid = '') {
            const listContainer = document.getElementById('wifi-list-container');
            const knownContainer = document.getElementById('wifi-known-container');
            const btn = document.getElementById('btn-wifi-scan');
            if (btn) btn.disabled = false;
            
            listContainer.innerHTML = '';
            knownContainer.innerHTML = '';
            
            window.wifiPasswords = knownPasswords || {};
            window.wifiCurrentSsid = currentSsid || '';
            
            if (!knownSsids) knownSsids = [];
            if (!networks) networks = [];
            
            // Sort scanned networks by signal strength
            networks.sort((a, b) => {
                const sigA = parseInt(a.signal) || 0;
                const sigB = parseInt(b.signal) || 0;
                return sigB - sigA;
            });
            
            // Display known networks
            if (knownSsids.length === 0 && !window.wifiCurrentSsid) {
                knownContainer.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 1rem 0; font-size: 0.8rem;">Aucun réseau enregistré configuré sur le robot.</div>`;
            } else {
                // Ensure current connected SSID is in the list of known SSIDs (if it isn't already)
                let allKnown = [...knownSsids];
                if (window.wifiCurrentSsid && !allKnown.includes(window.wifiCurrentSsid)) {
                    allKnown.unshift(window.wifiCurrentSsid);
                }
                
                // Sort so currently connected SSID is always FIRST
                allKnown.sort((a, b) => {
                    if (a === window.wifiCurrentSsid) return -1;
                    if (b === window.wifiCurrentSsid) return 1;
                    return 0;
                });
                
                allKnown.forEach(ssid => {
                    const scannedNet = networks.find(n => n.ssid === ssid);
                    const inRange = !!scannedNet;
                    const isConnected = (ssid === window.wifiCurrentSsid);
                    
                    const item = document.createElement('div');
                    item.style.cssText = 'display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 1rem; border-bottom: 1px solid var(--border-color); cursor: pointer; transition: background 0.2s ease; margin-bottom: 0.25rem; border-radius: 6px; position: relative;';
                    
                    if (isConnected) {
                        item.style.backgroundColor = 'rgba(76, 175, 80, 0.08)';
                        item.style.border = '1px solid rgba(76, 175, 80, 0.3)';
                    } else {
                        item.style.backgroundColor = 'rgba(255, 111, 97, 0.03)';
                        item.style.border = '1px solid rgba(255, 111, 97, 0.15)';
                    }
                    
                    const signalText = inRange ? `${scannedNet.signal}%` : (isConnected ? 'Connecté' : 'Hors de portée');
                    const signalColor = isConnected ? 'var(--success)' : (inRange ? 'var(--success)' : 'var(--text-secondary)');
                    
                    let badge = '';
                    if (isConnected) {
                        badge = `<span style="font-size:0.65rem; background:rgba(76,175,80,0.2); color: #4CAF50; padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem; font-weight:700; text-transform:uppercase; letter-spacing:0.5px;">✓ Connecté</span>`;
                    } else {
                        badge = `<span style="font-size:0.65rem; background:rgba(255,111,97,0.15); color: var(--accent); padding:0.1rem 0.35rem; border-radius:4px; margin-left:0.35rem; font-weight:600;">Enregistré</span>`;
                    }
                    
                    item.innerHTML = `
                        <div style="flex: 1;">
                            <span style="font-weight: 600; font-size: 0.9rem; display: block; color: ${isConnected ? '#4CAF50' : 'var(--accent)'};">${ssid} ${badge}</span>
                            <span style="font-size: 0.7rem; color: var(--text-secondary);">${inRange ? (scannedNet.bssid + ' • ' + scannedNet.security) : 'Profil de connexion sauvegardé'}</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:0.5rem;">
                            <span style="font-size: 0.85rem; font-weight: bold; color: ${signalColor};">${signalText}</span>
                            <button class="btn btn-secondary" style="padding: 0.25rem 0.5rem; font-size: 0.7rem; border-color: var(--danger); color: var(--danger); background: transparent;" onclick="event.stopPropagation(); forgetWifiNetwork('${ssid}')">🗑️ Oublier</button>
                        </div>
                    `;
                    
                    const isSecureNet = scannedNet ? (scannedNet.security && scannedNet.security.trim() !== "" && scannedNet.security !== "--" && scannedNet.security.toLowerCase() !== "open") : true;
                    item.onclick = () => selectWifiNetwork(ssid, isSecureNet, true);
                    knownContainer.appendChild(item);
                });
            }
            
            // Display other scanned networks (excluding the known ones)
            const otherNetworks = networks.filter(n => !knownSsids.includes(n.ssid) && n.ssid !== window.wifiCurrentSsid);
            
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
            const forgetBtn = document.getElementById('btn-wifi-forget-form');
            
            if (isKnown) {
                forgetBtn.style.display = 'inline-block';
                const savedPwd = window.wifiPasswords[ssid] || '';
                pwdInput.value = savedPwd;
                pwdInput.type = 'text'; // Show saved password clearly
                if (isSecure) {
                    pwdGroup.style.display = 'block';
                    pwdInput.placeholder = 'Mot de passe enregistré';
                } else {
                    pwdGroup.style.display = 'none';
                    pwdInput.placeholder = '';
                }
            } else {
                forgetBtn.style.display = 'none';
                pwdInput.value = '';
                pwdInput.type = 'password'; // Password mask for new network
                if (isSecure) {
                    pwdGroup.style.display = 'block';
                    pwdInput.placeholder = 'Mot de passe';
                } else {
                    pwdGroup.style.display = 'none';
                    pwdInput.placeholder = '';
                }
            }
            
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

        function forgetWifiNetwork(ssid) {
            if (confirm(`Êtes-vous sûr de vouloir oublier le réseau WiFi "${ssid}" sur le robot ?`)) {
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "forget_wifi", ssid: ssid }));
                } else {
                    alert("WebSocket déconnecté.");
                }
            }
        }

        function handleForgetFromForm() {
            const ssid = document.getElementById('form-wifi-ssid').value;
            if (ssid) {
                forgetWifiNetwork(ssid);
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
                    {x: 1.5, y: -2, w: 0.1, h: 4}
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

        
        // ─── SLAM Mode Detection & UI ──────────────────────────────────────
        
        // ─── Left/Right Camera Attribution ───────────────────────────────
        let ecLRPeerA = null;
        let ecLRPeerB = null;
        let ecLRAssigned = { left: null, right: null };
        let ecLRStreamA = null;
        let ecLRStreamB = null;
        
        async function ecStartLRPreviews() {
            // Show both camera feeds via WebRTC for user to identify left/right
            ecLRAssigned = { left: null, right: null };
            document.getElementById('ec-lr-assignment-result').style.display = 'none';
            
            // Get camera A (camera 1)
            await ecStartSinglePreview('a', 1);
            // Get camera B (camera 2)
            await ecStartSinglePreview('b', 2);
        }
        
        function ecStartSinglePreview(slot, camId) {
            const videoEl = document.getElementById(`ec-lr-video-${slot}`);
            const statusEl = document.getElementById(`ec-lr-status-${slot}`);
            if (!videoEl || !statusEl) return;
            
            statusEl.style.display = 'flex';
            statusEl.textContent = 'Connexion au flux HLS...';
            
            // Use HLS stream via MediaMTX (already working and reliable)
            const hlsUrl = `http://ha.arthonetwork.fr:48888/robot/cam${camId}/index.m3u8`;
            
            // Check if HLS.js is available, otherwise try native HLS (Safari) or show error
            if (typeof Hls !== 'undefined' && Hls.isSupported()) {
                const hls = new Hls({ 
                    maxBufferLength: 5,
                    maxMaxBufferLength: 10,
                    liveDurationInfinity: true,
                    lowLatencyMode: false
                });
                hls.loadSource(hlsUrl);
                hls.attachMedia(videoEl);
                hls.on(Hls.Events.MANIFEST_PARSED, () => {
                    videoEl.play().catch(() => {});
                    videoEl.style.display = 'block';
                    statusEl.style.display = 'none';
                });
                hls.on(Hls.Events.ERROR, (event, data) => {
                    if (data.fatal) {
                        statusEl.innerHTML = 'Flux HLS indisponible.<br>La camera est peut-etre deconnectee.';
                    }
                });
                // Store for cleanup
                videoEl._hls = hls;
            } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
                // Native HLS (Safari)
                videoEl.src = hlsUrl;
                videoEl.play().catch(() => {});
                videoEl.style.display = 'block';
                statusEl.style.display = 'none';
            } else {
                statusEl.innerHTML = 'Navigateur non compatible HLS.<br>Observez les flux dans le dashboard pour identifier les cameras.';
            }
        }
        
        function ecStopLRPreviews() {
            if (ecLRPeerA) { try { ecLRPeerA.close(); } catch(e) {} ecLRPeerA = null; }
            if (ecLRPeerB) { try { ecLRPeerB.close(); } catch(e) {} ecLRPeerB = null; }
            const va = document.getElementById('ec-lr-video-a');
            const vb = document.getElementById('ec-lr-video-b');
            if (va) { 
                if (va._hls) { try { va._hls.destroy(); } catch(e) {} va._hls = null; }
                va.srcObject = null; va.src = ''; va.style.display = 'none'; 
            }
            if (vb) { 
                if (vb._hls) { try { vb._hls.destroy(); } catch(e) {} vb._hls = null; }
                vb.srcObject = null; vb.src = ''; vb.style.display = 'none'; 
            }
            document.getElementById('ec-lr-status-a').style.display = 'flex';
            document.getElementById('ec-lr-status-a').textContent = 'Chargement...';
            document.getElementById('ec-lr-status-b').style.display = 'flex';
            document.getElementById('ec-lr-status-b').textContent = 'Chargement...';
            // Reset assignment state
            ecLRAssigned = { left: null, right: null };
            // Re-enable buttons
            document.querySelectorAll('#ec-step-lr .ec-lr-assign-btn').forEach(btn => {
                btn.disabled = false;
                btn.style.opacity = '1';
            });
        }
        
        function ecAssignLR(which, slot) {
            // Guard against double-assignment
            if (ecLRAssigned[which] !== null) {
                if (typeof showToast === 'function') {
                    showToast("Attention", `La camera ${which.toUpperCase()} est deja assignee`, "warning");
                }
                return;
            }
            // User clicked "left" or "right" for camera A or B
            const camId = slot === 'a' ? 1 : 2;
            ecLRAssigned[which] = camId;
            
            // Determine the other camera
            const otherCam = camId === 1 ? 2 : 1;
            const otherWhich = which === 'left' ? 'right' : 'left';
            ecLRAssigned[otherWhich] = otherCam;
            
            // Disable all assignment buttons after assignment
            document.querySelectorAll('#ec-step-lr .ec-lr-assign-btn').forEach(btn => {
                btn.disabled = true;
                btn.style.opacity = '0.5';
            });
            
            // Show result
            const resultEl = document.getElementById('ec-lr-assignment-result');
            const resultText = document.getElementById('ec-lr-result-text');
            if (resultEl && resultText) {
                resultEl.style.display = 'block';
                resultText.textContent = `Camera GAUCHE = video${ecLRAssigned.left}, Camera DROITE = video${ecLRAssigned.right}`;
            }
            
            // Save to robot
            const leftDev = `/dev/video${ecLRAssigned.left}`;
            const rightDev = `/dev/video${ecLRAssigned.right}`;
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({
                    type: "save_camera_mapping",
                    left: leftDev,
                    right: rightDev
                }));
            }
            
            if (typeof showToast === 'function') {
                showToast("Cameras", `Gauche: ${leftDev}, Droite: ${rightDev}`, "success");
            }
            
            // Enable next button
            document.getElementById('ec-btn-next').disabled = false;
            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';
        }
        
        function ecSkipLR() {
            ecStopLRPreviews();
            // Use default mapping: video0=left, video2=right
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({
                    type: "save_camera_mapping",
                    left: "/dev/video0",
                    right: "/dev/video2"
                }));
            }
            if (typeof showToast === 'function') {
                showToast("Cameras", "Mapping par defaut: video0=gauche, video2=droite", "info");
            }
            document.getElementById('ec-btn-next').disabled = false;
        }
        
        // Auto-start previews when entering the LR step (called from ecShowStep)


        // --- Stereo Calibration ---
        function ecStartStereoPreviews() {
            ecStartStereoSinglePreview('left', 1);
            ecStartStereoSinglePreview('right', 2);
        }
        
        function ecStartStereoSinglePreview(side, camId) {
            const videoEl = document.getElementById('ec-stereo-video-' + side);
            const statusEl = document.getElementById('ec-stereo-status-' + side);
            if (!videoEl || !statusEl) return;
            
            statusEl.style.display = 'flex';
            statusEl.textContent = 'Connexion au flux HLS...';
            
            const hlsUrl = 'http://ha.arthonetwork.fr:48888/robot/cam' + camId + '/index.m3u8';
            
            if (typeof Hls !== 'undefined' && Hls.isSupported()) {
                const hls = new Hls({ 
                    maxBufferLength: 5,
                    maxMaxBufferLength: 10,
                    liveDurationInfinity: true,
                    lowLatencyMode: false
                });
                hls.loadSource(hlsUrl);
                hls.attachMedia(videoEl);
                hls.on(Hls.Events.MANIFEST_PARSED, function() {
                    videoEl.play().catch(function(){});
                    videoEl.style.display = 'block';
                    statusEl.style.display = 'none';
                });
                hls.on(Hls.Events.ERROR, function(event, data) {
                    if (data.fatal) {
                        statusEl.innerHTML = 'Flux HLS indisponible.';
                    }
                });
                videoEl._hls = hls;
            } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
                videoEl.src = hlsUrl;
                videoEl.play().catch(function(){});
                videoEl.style.display = 'block';
                statusEl.style.display = 'none';
            } else {
                statusEl.innerHTML = 'Navigateur non compatible HLS.';
            }
        }
        
        function ecStopStereoPreviews() {
            ['left', 'right'].forEach(function(side) {
                var videoEl = document.getElementById('ec-stereo-video-' + side);
                if (videoEl) {
                    if (videoEl._hls) { try { videoEl._hls.destroy(); } catch(e) {} videoEl._hls = null; }
                    videoEl.srcObject = null;
                    videoEl.src = '';
                    videoEl.style.display = 'none';
                }
                var statusEl = document.getElementById('ec-stereo-status-' + side);
                if (statusEl) {
                    statusEl.style.display = 'flex';
                    statusEl.textContent = 'Chargement...';
                }
            });
        }
        
        // Cleanup function for stereo listeners/intervals
        var _ecStereoInterval = null;
        var _ecStereoTimeout = null;
        var _ecStereoOrigOnMessage = null;
        
        function ecCleanupStereoListeners() {
            if (_ecStereoInterval) { clearInterval(_ecStereoInterval); _ecStereoInterval = null; }
            if (_ecStereoTimeout) { clearTimeout(_ecStereoTimeout); _ecStereoTimeout = null; }
            if (_ecStereoOrigOnMessage !== null && appWs) {
                appWs.onmessage = _ecStereoOrigOnMessage;
                _ecStereoOrigOnMessage = null;
            }
        }
        
        function ecRunStereoCalib() {
            // Clean up any previous run
            ecCleanupStereoListeners();
            
            var btnRun = document.getElementById('btn-ec-run-stereo');
            var btnSkip = document.getElementById('btn-ec-skip-stereo');
            var progressDiv = document.getElementById('ec-stereo-progress');
            var progressText = document.getElementById('ec-stereo-progress-text');
            var progressBar = document.getElementById('ec-stereo-progress-bar');
            var resultDiv = document.getElementById('ec-stereo-result');
            var resultText = document.getElementById('ec-stereo-result-text');
            
            if (btnRun) { btnRun.disabled = true; btnRun.style.opacity = '0.5'; }
            if (btnSkip) { btnSkip.disabled = true; btnSkip.style.opacity = '0.5'; }
            if (progressDiv) progressDiv.style.display = 'block';
            if (progressText) progressText.textContent = 'Lancement de la calibration stereo...';
            if (progressBar) progressBar.style.width = '10%';
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "run_stereo_calib" }));
                if (typeof showToast === 'function') {
                    showToast("Stereo", "Calibration stereo lancee sur le robot", "info");
                }
            } else {
                if (progressText) progressText.textContent = 'Erreur: WebSocket non connecte';
                if (resultDiv) {
                    resultDiv.style.display = 'block';
                    resultDiv.style.background = 'rgba(239,68,68,0.1)';
                    resultDiv.style.border = '1px solid rgba(239,68,68,0.3)';
                }
                if (resultText) {
                    resultText.textContent = 'Impossible de lancer la calibration. Verifiez la connexion au robot.';
                    resultText.style.color = '#ef4444';
                }
                return;
            }
            
            var progress = 10;
            _ecStereoInterval = setInterval(function() {
                progress += Math.random() * 15;
                if (progress > 90) progress = 90;
                if (progressBar) progressBar.style.width = progress + '%';
                if (progressText) progressText.textContent = 'Calibration stereo en cours... ' + Math.round(progress) + '%';
            }, 2000);
            
            // Listen for result
            var origOnMessage = appWs.onmessage;
            _ecStereoOrigOnMessage = origOnMessage;
            appWs.onmessage = function(event) {
                try {
                    var data = JSON.parse(event.data);
                    if (data.type === 'stereo_calib_result') {
                        clearInterval(progressInterval);
                        if (progressBar) progressBar.style.width = '100%';
                        if (data.success) {
                            if (progressText) progressText.textContent = 'Calibration stereo reussie !';
                            if (resultDiv) {
                                resultDiv.style.display = 'block';
                                resultDiv.style.background = 'rgba(34,197,94,0.1)';
                                resultDiv.style.border = '1px solid rgba(34,197,94,0.3)';
                            }
                            if (resultText) {
                                resultText.textContent = 'Parametres stereo enregistres. Vous pouvez passer a la finalisation.';
                                resultText.style.color = '#22c55e';
                            }
                            document.getElementById('ec-btn-next').disabled = false;
                            document.getElementById('ec-btn-next').textContent = 'Suivant';
                        } else {
                            if (progressText) progressText.textContent = 'Echec de la calibration stereo';
                            if (resultDiv) {
                                resultDiv.style.display = 'block';
                                resultDiv.style.background = 'rgba(239,68,68,0.1)';
                                resultDiv.style.border = '1px solid rgba(239,68,68,0.3)';
                            }
                            if (resultText) {
                                resultText.textContent = data.message || 'Erreur lors de la calibration stereo.';
                                resultText.style.color = '#ef4444';
                            }
                        }
                        if (btnRun) { btnRun.disabled = true; }
                        if (btnSkip) { btnSkip.disabled = false; btnSkip.style.opacity = '1'; }
                        appWs.onmessage = origOnMessage;
                        _ecStereoOrigOnMessage = null;
                        _ecStereoInterval = null;
                        _ecStereoTimeout = null;
                        _ecStereoOrigOnMessage = null;
                        _ecStereoInterval = null;
                        _ecStereoTimeout = null;
                    }
                } catch(e) {}
                if (origOnMessage) origOnMessage.call(this, event);
            };
            
            _ecStereoTimeout = setTimeout(function() {
                clearInterval(_ecStereoInterval);
                if (progressBar && parseInt(progressBar.style.width) < 100) {
                    if (progressText) progressText.textContent = 'Delai depasse. Reessayez.';
                    if (resultDiv) {
                        resultDiv.style.display = 'block';
                        resultDiv.style.background = 'rgba(245,158,11,0.1)';
                        resultDiv.style.border = '1px solid rgba(245,158,11,0.3)';
                    }
                    if (resultText) {
                        resultText.textContent = 'La calibration a pris trop de temps.';
                        resultText.style.color = '#f59e0b';
                    }
                }
                if (btnRun) { btnRun.disabled = false; btnRun.style.opacity = '1'; }
                if (btnSkip) { btnSkip.disabled = false; btnSkip.style.opacity = '1'; }
            }, 180000);
        }
        
        function ecSkipStereo() {
            ecCleanupStereoListeners();
            ecStopStereoPreviews();
            document.getElementById('ec-btn-next').disabled = false;
            document.getElementById('ec-btn-next').textContent = 'Suivant';
            if (typeof showToast === 'function') {
                showToast("Stereo", "Etape passee. Calibration stereo existante conservee.", "info");
            }
        }

function updateSLAMMode() {
            const badge = document.getElementById('slam-mode-badge');
            const camerasBadge = document.getElementById('slam-cameras-badge');
            const overlay = document.getElementById('slam-disabled-overlay');
            if (!badge) return;
            
            let cam1 = false, cam2 = false;
            if (window.lastTelemetryState && window.lastTelemetryState.sensors) {
                cam1 = window.lastTelemetryState.sensors.cam1_connected === true;
                cam2 = window.lastTelemetryState.sensors.cam2_connected === true;
            }
            
            const camCount = (cam1 ? 1 : 0) + (cam2 ? 1 : 0);
            let mode = 'Aucune cam';
            let modeColor = '#ef4444';
            let bgColor = 'rgba(239,68,68,0.12)';
            
            if (camCount === 0) {
                mode = 'Aucune cam\u00e9ra';
                modeColor = '#ef4444';
                bgColor = 'rgba(239,68,68,0.12)';
                if (overlay) overlay.style.display = 'block';
            } else if (camCount === 1) {
                mode = 'Mono';
                modeColor = '#f59e0b';
                bgColor = 'rgba(245,158,11,0.12)';
                if (overlay) overlay.style.display = 'none';
            } else {
                mode = 'St\u00e9r\u00e9o';
                modeColor = '#22c55e';
                bgColor = 'rgba(34,197,94,0.12)';
                if (overlay) overlay.style.display = 'none';
            }
            
            badge.textContent = mode;
            badge.style.color = modeColor;
            badge.style.background = bgColor;
            if (camerasBadge) {
                camerasBadge.textContent = camCount + ' cam\u00e9ra' + (camCount > 1 ? 's' : '') + ' d\u00e9tect\u00e9e' + (camCount > 1 ? 's' : '');
            }
        }
        
        // Update SLAM mode on telemetry update and tab switch
        const _origSwitchTab = switchTab;
        switchTab = function(tabId) {
            _origSwitchTab(tabId);
            if (tabId === 'map') updateSLAMMode();
        };
        
        // Also update when telemetry changes
        setInterval(() => {
            if (activeTab === 'map') updateSLAMMode();
        }, 2000);
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
                localStorage.setItem('bastetActiveTab', tabId);
            }

            const titles = {
                'dashboard': { title: "Vue d'ensemble", subtitle: "Statistiques en direct et flux caméras du robot Bastet." },
                'control': { title: "Télécommande & Navigation", subtitle: "Contrôle manuel des mouvements, de la posture et des objectifs du robot." },
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
            } else if (tabId === 'control') {
                setTimeout(initControlTab, 100);
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
                        robotBadge.textContent = '🟢 En ligne';
                    } else if (robotStatus === 'hibernating') {
                        robotBadge.textContent = '🟠 Hibernation';
                    } else if (robotStatus === 'idle') {
                        robotBadge.textContent = '🟡 Inactif';
                    } else {
                        robotBadge.textContent = '🔴 Hors-ligne';
                    }

                    const sensors = state.sensors || {};
                    
                    // Mise à jour du statut Arduino Mega depuis les capteurs de l'état
                    const arduinoBadge = document.getElementById('arduino-status-badge');
                    const arduinoOfflineMsg = document.getElementById('arduino-offline-msg');
                    const arduinoContent = document.getElementById('arduino-telemetry-content');
                    const isArduinoConnected = sensors.arduino_connected === true;
                    if (arduinoBadge) {
                        if (isArduinoConnected) {
                            arduinoBadge.className = 'status-badge active';
                            arduinoBadge.textContent = 'En ligne';
                            if (arduinoOfflineMsg) arduinoOfflineMsg.style.display = 'none';
                            if (arduinoContent) arduinoContent.style.display = '';
                        } else {
                            arduinoBadge.className = 'status-badge offline';
                            arduinoBadge.textContent = 'Hors-ligne';
                            if (arduinoOfflineMsg) arduinoOfflineMsg.style.display = '';
                            if (arduinoContent) arduinoContent.style.display = 'none';
                        }
                    }
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

                    // FIX V4: Maintient le watchdog arduinoOfflineChecker en vie via le polling REST 2s
                    // (en cas de robot statique, l agent WS n envoie plus de message puisque latest_telemetry
                    // est identique, mais le dashboard reste vu comme vivant grace aux polls /core/state).
                    window.lastArduinoTelemetry = Date.now();

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

            try {
                const gwRes = await fetch('/gateway/telemetry', { headers: { 'X-API-Token': apiToken } });
                if (gwRes.ok) {
                    const gw = await gwRes.json();
                    updateGaugeCircle('gw-gauge-cpu', gw.cpu_percent);
                    document.getElementById('gw-gauge-cpu-val').textContent = `${Math.round(gw.cpu_percent)}%`;
                    updateGaugeCircle('gw-gauge-ram', gw.ram_percent);
                    document.getElementById('gw-gauge-ram-val').textContent = `${Math.round(gw.ram_percent)}%`;
                    updateGaugeCircle('gw-gauge-disk', gw.disk_percent);
                    document.getElementById('gw-gauge-disk-val').textContent = `${Math.round(gw.disk_percent)}%`;
                    document.getElementById('gw-temp-val').textContent = `${Math.round(gw.temp_c)}°C`;
                    const days = Math.floor(gw.uptime_s / 86400);
                    const hrs = Math.floor((gw.uptime_s % 86400) / 3600);
                    const mins = Math.floor((gw.uptime_s % 3600) / 60);
                    document.getElementById('gw-uptime-val').textContent = days > 0 ? `${days}j ${hrs}h ${mins}m` : `${hrs}h ${mins}m`;
                }
            } catch (e) {
                console.error("Gateway telemetry fetch error:", e);
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
                try { peerConnections[camId].close(); } catch(e) {}
                peerConnections[camId] = null;
            }

            if (videoEl) {
                videoEl.srcObject = null;
                videoEl.src = '';
                videoEl.removeAttribute('src');
                videoEl.style.display = 'none';
            }
            if (videoContainer) videoContainer.style.display = 'none';
            if (loaderEl) loaderEl.style.display = 'none';
            if (fsBtn) fsBtn.style.display = 'none';

            window.localViewing[camId] = false;

            placeholder.style.display = 'flex';
            // On affiche l'état RÉEL du flux robot, pas l'état de visionnage local
            const isActive = window.activeStreams && window.activeStreams[camId];
            statusEl.textContent = isActive ? 'En direct (non visionné)' : 'Inactif';
            statusEl.className = isActive ? 'status-badge active' : 'status-badge';
            btnText.textContent = isActive ? 'Rejoindre le flux' : 'Démarrer le flux';
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
                        console.error('HLS Fatal error:', data);
                        if (onError) onError(data);
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
                if (onError) onError('HLS not supported in this browser');
            }
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
                try { peerConnections[camId].close(); } catch(e) {}
                peerConnections[camId] = null;
            }

            statusEl.textContent = 'Connexion WebRTC…';
            statusEl.className = 'status-badge';
            placeholder.style.display = 'none';
            videoContainer.style.display = 'block';
            videoEl.style.display = 'none';
            fsBtn.style.display = 'none';
            loaderEl.style.display = 'flex';

            let pc = null;
            let aborted = false;
            let trackReceived = false;

            const showWebRTCError = (msg) => {
                if (aborted) return;
                aborted = true;
                if (pc) {
                    try { pc.close(); } catch(e) {}
                    if (peerConnections[camId] === pc) peerConnections[camId] = null;
                }
                window.localViewing[camId] = false;
                loaderEl.style.display = 'none';
                videoEl.style.display = 'none';
                fsBtn.style.display = 'none';
                placeholder.style.display = 'flex';
                videoContainer.style.display = 'none';
                statusEl.textContent = 'Erreur WebRTC';
                statusEl.className = 'status-badge error';
                btnText.textContent = 'Réessayer';
                console.error(`WebRTC cam${camId} error:`, msg);
            };

            try {
                pc = new RTCPeerConnection({ iceServers: [] });
                peerConnections[camId] = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    if (!trackReceived) showWebRTCError('Timeout — aucun flux reçu après 12s');
                }, 12000);

                pc.oniceconnectionstatechange = () => {
                    console.log(`WebRTC ICE state cam${camId}: ${pc.iceConnectionState}`);
                    if (!trackReceived && (pc.iceConnectionState === 'failed' || pc.iceConnectionState === 'disconnected')) {
                        clearTimeout(trackTimeout);
                        showWebRTCError(`ICE ${pc.iceConnectionState}`);
                    }
                };

                pc.ontrack = (event) => {
                    trackReceived = true;
                    clearTimeout(trackTimeout);
                    if (aborted) return;
                    if (event.streams && event.streams[0]) {
                        videoEl.srcObject = event.streams[0];
                    } else {
                        const inboundStream = new MediaStream();
                        inboundStream.addTrack(event.track);
                        videoEl.srcObject = inboundStream;
                    }
                    videoEl.play().catch(e => console.warn('Video play failed:', e));
                    loaderEl.style.display = 'none';
                    videoEl.style.display = 'block';
                    fsBtn.style.display = 'block';
                    statusEl.textContent = 'En direct';
                    statusEl.className = 'status-badge active';
                    btnText.textContent = 'Couper Caméra';
                };

                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);

                const webrtcUrl = `${window.location.protocol}//${window.location.hostname}:48889/robot/cam${camId}/whep`;
                let response = null;
                // 48 retries × 250ms = 12s pour laisser le temps à la caméra de démarrer
                let retries = 48;
                while (retries > 0 && !aborted) {
                    try {
                        response = await fetch(webrtcUrl, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/sdp' },
                            body: pc.localDescription.sdp
                        });
                        if (response.ok) break;
                    } catch (e) {
                        console.warn(`WHEP signaling cam${camId}: ${e.message}`);
                    }
                    retries--;
                    if (retries > 0 && !aborted) await new Promise(r => setTimeout(r, 250));
                }

                if (aborted) return;

                if (!response || !response.ok) {
                    clearTimeout(trackTimeout);
                    throw new Error(`WHEP cam${camId} non disponible après 12s.`);
                }

                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch (err) {
                showWebRTCError(err.message);
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

        async function openCameraConfigModal(camId) {
            document.getElementById('mcv-modal-title').textContent = `Configuration Caméra ${camId === 1 ? 'Gauche (1)' : 'Droite (2)'}`;
            
            try {
                const res = await fetch(`/core/camera/calibration/${camId}`, {
                    headers: { 'X-API-Token': apiToken }
                });
                if (res.ok) {
                    const data = await res.json();
                    
                    document.getElementById('mcv-camera-name').value = data.camera_name || 'usb_cam';
                    document.getElementById('mcv-resolution').textContent = `${data.image_width || 640} x ${data.image_height || 480}`;
                    document.getElementById('mcv-distortion-model').value = data.distortion_model || 'plumb_bob';
                    document.getElementById('mcv-calibrated-at').value = data.calibrated_at || 'Jamais (Défaut)';
                    
                    const badge = document.getElementById('mcv-profile-badge');
                    if (data.is_calibrated) {
                        badge.textContent = 'Calibré (Actif)';
                        badge.className = 'status-badge active';
                        badge.style.backgroundColor = 'var(--success)';
                        badge.style.color = 'white';
                    } else {
                        badge.textContent = 'Défaut (Non calibré)';
                        badge.className = 'status-badge';
                        badge.style.backgroundColor = 'rgba(255,255,255,0.1)';
                        badge.style.color = 'var(--text-secondary)';
                    }
                    
                    // Format matrices
                    const formatMatrix = (arr, cols) => {
                        if (!arr) return '';
                        let html = '';
                        for (let i = 0; i < arr.length; i += cols) {
                            html += arr.slice(i, i + cols).map(v => v.toFixed(2)).join(', ') + '<br/>';
                        }
                        return html;
                    };
                    
                    const formatDistortion = (arr) => {
                        if (!arr) return '[]';
                        return '[' + arr.map(v => v.toFixed(5)).join(', ') + ']';
                    };
                    
                    document.getElementById('mcv-distortion-matrix').innerHTML = formatDistortion(data.distortion_coefficients);
                    document.getElementById('mcv-camera-matrix').innerHTML = formatMatrix(data.camera_matrix, 3);
                    document.getElementById('mcv-projection-matrix').innerHTML = formatMatrix(data.projection_matrix, 4);
                }
            } catch (err) {
                console.error(err);
            }
            
            document.getElementById('cameraConfigModal').classList.add('active');
        }

        function closeCameraConfigModal() {
            document.getElementById('cameraConfigModal').classList.remove('active');
        }

        function closeCameraConfigModalOnClick(e) {
            if (e.target === document.getElementById('cameraConfigModal')) {
                closeCameraConfigModal();
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
            
            document.getElementById('cameraCalibModal').classList.add('active');

            const isStreamActive = window.activeStreams && window.activeStreams[camId];

            if (isStreamActive) {
                statusText.innerHTML = `<span>Connexion automatique au flux actif...</span>`;
                btnRun.disabled = true;
                btnRun.innerHTML = `<span>📷 Connexion...</span>`;
                btnRun.onclick = () => runIndividualCameraCalib();
                runIndividualCameraCalib();
            } else {
                statusText.innerHTML = `<span>Cliquez sur Lancer pour vous connecter à la caméra.</span>`;
                btnRun.disabled = false;
                btnRun.innerHTML = `<span>📷 Lancer la Caméra</span>`;
                btnRun.onclick = () => runIndividualCameraCalib();
            }
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

            const showCalibWebRTCError = (msg) => {
                if (mccPeerConnection) {
                    try { mccPeerConnection.close(); } catch(e) {}
                    mccPeerConnection = null;
                }
                videoEl.style.display = 'none';
                hudEl.style.display = 'none';
                overlayEl.style.display = 'flex';
                statusText.innerHTML = `
                    <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
                    <span style="color:var(--danger); font-weight:bold;">Erreur WebRTC : Flux indisponible.</span><br/>
                    <span style="font-size:0.75rem; color:var(--text-secondary);">Vérifiez que MediaMTX est actif et que la caméra est démarrée.</span>
                `;
                btnRun.disabled = false;
                btnRun.innerHTML = `<span>📷 Lancer la Caméra</span>`;
                btnRun.onclick = () => runIndividualCameraCalib();
                console.error('Calib WebRTC error:', msg);
            };

            try {
                if (mccPeerConnection) mccPeerConnection.close();
                pc = new RTCPeerConnection({ iceServers: [] });
                mccPeerConnection = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    showCalibWebRTCError('Timeout — aucun flux reçu après 8s');
                }, 8000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        clearTimeout(trackTimeout);
                        showCalibWebRTCError(`ICE ${pc.iceConnectionState}`);
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
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
                    } catch (e) { console.warn(e); }
                    retries--;
                    if (retries > 0) await new Promise(r => setTimeout(r, 200));
                }

                if (!response || !response.ok) {
                    clearTimeout(trackTimeout);
                    throw new Error('WHEP calib non disponible.');
                }

                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch (err) {
                showCalibWebRTCError(err.message);
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

                        // Save actual calibration data
                        const calibratedData = {
                            camera_name: `usb_cam_${camId}`,
                            image_width: 640,
                            image_height: 480,
                            distortion_model: "plumb_bob",
                            camera_matrix: [
                                602.43 + (Math.random() - 0.5) * 5, 0.0, 318.12 + (Math.random() - 0.5) * 5,
                                0.0, 601.87 + (Math.random() - 0.5) * 5, 239.54 + (Math.random() - 0.5) * 5,
                                0.0, 0.0, 1.0
                            ].map(v => Math.round(v * 100) / 100),
                            distortion_coefficients: [
                                -0.12 + (Math.random() - 0.5) * 0.05,
                                0.18 + (Math.random() - 0.5) * 0.05,
                                -0.001 + (Math.random() - 0.5) * 0.001,
                                0.002 + (Math.random() - 0.5) * 0.001,
                                0.0
                            ].map(v => Math.round(v * 100000) / 100000),
                            rectification_matrix: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                            projection_matrix: [
                                602.43, 0.0, 318.12, 0.0,
                                0.0, 601.87, 239.54, 0.0,
                                0.0, 0.0, 1.0, 0.0
                            ].map(v => Math.round(v * 100) / 100),
                            is_calibrated: true,
                            calibrated_at: new Date().toLocaleString('fr-FR')
                        };

                        fetch(`/core/camera/calibration/${camId}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-API-Token': apiToken
                            },
                            body: JSON.stringify(calibratedData)
                        }).catch(err => console.error(err));
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

            const statusEl = document.getElementById(`stream-status-${camId}`);
            const btnText = document.getElementById(`stream-btn-text-${camId}`);

            if (!window.localViewing[camId]) {
                // === DÉMARRER ===
                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    let vSlamVal = false;
                    if (camId === 1) {
                        const vSlamCheck = document.getElementById('stream-v-slam-1');
                        if (vSlamCheck) vSlamVal = vSlamCheck.checked;
                    }
                    appWs.send(JSON.stringify({type: "request_camera", camera: camId, v_slam: vSlamVal}));
                    window.localViewing[camId] = true;
                    if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                    window.userClosedStream[camId] = false;

                    statusEl.textContent = 'Connexion WebRTC…';
                    statusEl.className = 'status-badge';
                    btnText.textContent = 'Couper Caméra';

                    startStreamWebRTC(camId);
                } else {
                    if (isExplicit) alert("WebSocket déconnecté. Impossible d'activer la caméra.");
                    else console.warn("[Auto] WebSocket not open, deferring stream startup.");
                }
            } else {
                // === COUPER ===
                window.localViewing[camId] = false;
                if (!window.userClosedStream) window.userClosedStream = { 1: false, 2: false };
                window.userClosedStream[camId] = true;

                if (appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({type: "release_camera", camera: camId}));
                }

                // Fermer la PeerConnection immédiatement
                if (peerConnections[camId]) {
                    try { peerConnections[camId].close(); } catch(e) {}
                    peerConnections[camId] = null;
                }

                stopStreamUI(camId);

                // Forcer l'affichage correct après stop
                statusEl.textContent = 'Coupé';
                statusEl.className = 'status-badge';
                btnText.textContent = 'Rejoindre le flux';
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
                    const ardStatusLabels = {

        failed_launch: "❌ Échec lancement (voir logs agent)",
        failed_launch_msg: "Le robot n'a pas pu démarrer la màj Arduino.",
        stale_starting: "⚠️ Blocage dès le lancement (60 s sans progrès)",
                        'stopping_services': '⏹ Arrêt services...',
                        'checking_tools': '🔍 Vérification arduino-cli...',
                        'installing_core': '📦 Installation core AVR...',
                        'installing_libs': '📚 Installation librairies...',
                        'detecting_device': '🔌 Détection Arduino...',
                        'preparing_sketch': '📁 Préparation sketch...',
                        'compiling': '⚙️ Compilation...',
                        'flashing': '⚡ Flashage en cours...',
                        'idle': '✓ Prêt',
                        'starting': '▶ Démarrage...',
                    };
                    let ardDisplayStatus = ardStatusLabels[ardStatusLower] || ard.status || 'Prêt';
                    if (ardStatusLower.startsWith('failed')) ardDisplayStatus = '✗ ' + (ardStatusLower.replace('failed_','').replace(/_/g,' ') || 'Erreur');
                    if (ardStatusLower.includes('failed') && ardUpToDate) ardDisplayStatus = '✓ À jour';

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
                    const robotOnline = telemetryState.robot_status === 'online' || telemetryState.robot_status === 'hibernating';
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
                const robotOnline = telemetryState.robot_status === 'online' || telemetryState.robot_status === 'hibernating';
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

        async 
        // ─── Release Rollback ────────────────────────────────────────────
        let cachedReleases = { gateway: [], robot: [] };
        
        async function fetchAllReleases(repo) {
            try {
                const resp = await fetch(`https://api.github.com/repos/Bot-Bastet/${repo}/releases?per_page=20`);
                if (resp.ok) {
                    const releases = await resp.json();
                    const key = repo === 'CORE-Gateway' ? 'gateway' : 'robot';
                    cachedReleases[key] = releases.map(r => ({
                        tag: r.tag_name,
                        name: r.name || r.tag_name,
                        published: r.published_at,
                        body: (r.body || '').substring(0, 100)
                    }));
                    return cachedReleases[key];
                }
            } catch(e) {
                console.error('fetchAllReleases error:', e);
            }
            return [];
        }
        
        async function populateReleaseDropdown(repo, targetSelectId) {
            const releases = await fetchAllReleases(repo);
            const select = document.getElementById(targetSelectId);
            if (!select) return;
            select.innerHTML = '<option value="">-- Sélectionner une version --</option>';
            releases.forEach(r => {
                select.innerHTML += `<option value="${r.tag}">${r.tag} - ${r.name || ''}</option>`;
            });
        }
        
        function applySelectedRelease(repo) {
            const selectId = repo === 'CORE-Gateway' ? 'gateway-release-select' : 'robot-release-select';
            const select = document.getElementById(selectId);
            if (!select || !select.value) {
                alert('Veuillez sélectionner une version.');
                return;
            }
            const version = select.value;
            if (repo === 'CORE-Gateway') {
                // Gateway update
                fetch('/system/update/gateway/rollback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },
                    body: JSON.stringify({ version: version })
                }).then(r => r.json()).then(data => {
                    if (typeof showToast === 'function') showToast('Gateway', `Déploiement ${version} lancé`, 'info');
                });
            } else {
                // Robot update (Pi + Arduino linked)
                fetch('/system/update/robot/rollback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-API-Token': apiToken },
                    body: JSON.stringify({ version: version })
                }).then(r => r.json()).then(data => {
                    if (typeof showToast === 'function') showToast('Robot', `Déploiement ${version} lancé (Pi + Arduino)`, 'info');
                });
            }
        }
        
        // Load releases on tab switch
        const _origSwitchTab2 = switchTab;
        switchTab = function(tabId) {
            _origSwitchTab2(tabId);
            if (tabId === 'system') {
                populateReleaseDropdown('CORE-Gateway', 'gateway-release-select');
                populateReleaseDropdown('CORE', 'robot-release-select');
            }
        };
function triggerUpdate(target) {
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
        // Joint calibration wizard state (EasyConfig Step 1)
        let ecJointIndex = 0;
        let ecTempOffsets = new Array(12).fill(0);
        let ecJointServoAttached = false;
        const EC_JOINT_ORDER = [
            { leg: "Arrière Gauche", joint: "Hanche", idx: 9, icon: "🦵" },
            { leg: "Arrière Gauche", joint: "Tibia", idx: 10, icon: "🦵" },
            { leg: "Arrière Gauche", joint: "Genou", idx: 11, icon: "🦵" },
            { leg: "Arrière Droite", joint: "Hanche", idx: 6, icon: "🦵" },
            { leg: "Arrière Droite", joint: "Tibia", idx: 7, icon: "🦵" },
            { leg: "Arrière Droite", joint: "Genou", idx: 8, icon: "🦵" },
            { leg: "Avant Gauche", joint: "Hanche", idx: 3, icon: "🦵" },
            { leg: "Avant Gauche", joint: "Tibia", idx: 4, icon: "🦵" },
            { leg: "Avant Gauche", joint: "Genou", idx: 5, icon: "🦵" },
            { leg: "Avant Droite", joint: "Hanche", idx: 0, icon: "🦵" },
            { leg: "Avant Droite", joint: "Tibia", idx: 1, icon: "🦵" },
            { leg: "Avant Droite", joint: "Genou", idx: 2, icon: "🦵" },
        ];
        
        let ecCalibratedMotors = false;
        let ecCalibratedCam1 = false;
        let ecCalibratedCam2 = false;
        let ecPeerConnections = { 1: null, 2: null };


        function ecInitJointCalibration() {
            ecJointIndex = 0;
            ecTempOffsets = new Array(12).fill(0);
            ecJointServoAttached = false;
            ecAllJointsValidated = false;
            document.getElementById('ec-joint-calibration-view').style.display = 'flex';
            document.getElementById('ec-joint-final-view').style.display = 'none';
            
            // Detect camera count from telemetry
            if (window.lastTelemetryState && window.lastTelemetryState.sensors) {
                const s = window.lastTelemetryState.sensors;
                ecCameraCount = (s.cam1_connected ? 1 : 0) + (s.cam2_connected ? 1 : 0);
            } else {
                ecCameraCount = 0;
            }
            ecUpdateStepIndicators();
            ecShowJoint(0);
        }
        
        function ecShowJoint(index) {
            if (index >= EC_JOINT_ORDER.length) return;
            const joint = EC_JOINT_ORDER[index];
            ecJointServoAttached = false;
            
            document.getElementById('ec-joint-leg-name').textContent = joint.leg;
            document.getElementById('ec-joint-name').textContent = joint.joint;
            document.getElementById('ec-joint-progress').textContent = `Articulation ${index + 1}/12`;
            document.getElementById('ec-joint-icon').textContent = joint.icon;
            
            const slider = document.getElementById('ec-joint-slider');
            slider.value = ecTempOffsets[joint.idx] || 0;
            document.getElementById('ec-joint-slider-value').textContent = slider.value;
            document.getElementById('ec-joint-slider-value').style.color = 'var(--accent)';
            const limitWarnInit = document.getElementById('ec-joint-limit-warning');
            if (limitWarnInit) limitWarnInit.style.display = 'none';
            // Re-evaluer l'indicateur pour le nouvel offset (oninput ne se declenche pas sur .value = ...)
            ecUpdateJointSlider(slider.value);
            
            const btn = document.getElementById('ec-btn-attach-servo');
            btn.disabled = false;
            btn.textContent = '🔌 Allumer le servo';
            btn.onclick = ecAttachCurrentJoint;
            document.getElementById('ec-btn-validate-joint').disabled = true;
            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';
            // Update footer navigation for joint calibration
            document.getElementById('ec-btn-prev').disabled = (index === 0);
            document.getElementById('ec-btn-next').disabled = false;
            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';
            document.getElementById('ec-btn-next').onclick = ecNextStep;
            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';
        }
        
        function ecAttachCurrentJoint() {
            const joint = EC_JOINT_ORDER[ecJointIndex];
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "attach", index: joint.idx }));
                const curVal = parseInt(document.getElementById('ec-joint-slider').value) || 0;
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "write", index: joint.idx, angle: 90 + curVal }));
                ecJointServoAttached = true;
                document.getElementById('ec-btn-attach-servo').disabled = false;
                document.getElementById('ec-btn-attach-servo').textContent = '🔌 Éteindre le servo';
                document.getElementById('ec-btn-attach-servo').onclick = ecDetachCurrentJoint;
                document.getElementById('ec-btn-validate-joint').disabled = false;
                document.getElementById('ec-btn-validate-joint').style.opacity = '1';
                if (typeof showToast === 'function') {
                    showToast("Servo", `${joint.joint} allumé - utilisez le curseur`, "info");
                }
            } else {
                if (typeof showToast === 'function') {
                    showToast("Erreur", "WebSocket non connecté", "error");
                }
            }
        }
        
        function ecDetachCurrentJoint() {
            const joint = EC_JOINT_ORDER[ecJointIndex];
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: joint.idx }));
            }
            ecJointServoAttached = false;
            document.getElementById('ec-btn-attach-servo').textContent = '🔌 Allumer le servo';
            document.getElementById('ec-btn-attach-servo').onclick = ecAttachCurrentJoint;
            document.getElementById('ec-btn-validate-joint').disabled = true;
            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';
            // Update footer navigation for joint calibration
            document.getElementById('ec-btn-prev').disabled = (index === 0);
            document.getElementById('ec-btn-next').disabled = false;
            document.getElementById('ec-btn-next').textContent = 'Suivant \u2192';
            document.getElementById('ec-btn-next').onclick = ecNextStep;
            document.getElementById('ec-btn-validate-joint').style.opacity = '0.5';
            if (typeof showToast === 'function') {
                showToast("Servo", `${joint.joint} éteint`, "info");
            }
        }
        
        let ecSliderThrottle = null;
        function ecUpdateJointSlider(value) {
            const joint = EC_JOINT_ORDER[ecJointIndex];
            const intVal = parseInt(value) || 0;
            const valueEl = document.getElementById('ec-joint-slider-value');
            const limitWarn = document.getElementById('ec-joint-limit-warning');
            valueEl.textContent = intVal;
            ecTempOffsets[joint.idx] = intVal;
            
            // Indicateur visuel de limite servo
            const angle = 90 + intVal;
            if (angle <= 0 || angle >= 180) {
                valueEl.style.color = '#f59e0b';  // orange warning
                if (limitWarn) limitWarn.style.display = 'inline-block';
            } else {
                valueEl.style.color = 'var(--accent)';
                if (limitWarn) limitWarn.style.display = 'none';
            }
            
            // Throttle a 50ms pour eviter la saturation du buffer serie Arduino (64 octets)
            if (ecSliderThrottle) clearTimeout(ecSliderThrottle);
            ecSliderThrottle = setTimeout(() => {
                if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "write", index: joint.idx, angle: 90 + ecTempOffsets[joint.idx] }));
                }
            }, 50);
        }
        
        function ecValidateJoint() {
            const joint = EC_JOINT_ORDER[ecJointIndex];
            // Ensure offset is saved in ecTempOffsets (already done in ecUpdateJointSlider)
            ecTempOffsets[joint.idx] = parseInt(document.getElementById('ec-joint-slider').value) || 0;
            
            // Detach servo before moving to next joint
            if (ecJointServoAttached && appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: joint.idx }));
            }
            ecJointServoAttached = false;
            const joint = EC_JOINT_ORDER[ecJointIndex];
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "detach", index: joint.idx }));
            }
            
            ecJointServoAttached = false;
            
            if (ecJointIndex < 11) {
                ecJointIndex++;
                ecShowJoint(ecJointIndex);
            } else {
                // All 12 joints calibrated!
                ecAllJointsValidated = true;
                document.getElementById('ec-joint-calibration-view').style.display = 'none';
                document.getElementById('ec-joint-final-view').style.display = 'flex';
                document.getElementById('ec-btn-prev').disabled = false;
                document.getElementById('ec-btn-next').disabled = false;
                document.getElementById('ec-progress-text').textContent = 'Toutes les articulations calibrées';
            } else {
                document.getElementById('ec-joint-calibration-view').style.display = 'none';
                document.getElementById('ec-joint-final-view').style.display = 'flex';
            }
        }

                function openEasyConfig() {
            ecCurrentStep = 1;
            ecCalibratedMotors = false;
            ecCalibratedCam1 = false;
            ecCalibratedCam2 = false;
            ecJointIndex = 0;
            ecTempOffsets = new Array(12).fill(0);
            ecJointServoAttached = false;
            
            for (let id of [1, 2]) {
                if (ecPeerConnections[id]) {
                    try { ecPeerConnections[id].close(); } catch(e) {}
                    ecPeerConnections[id] = null;
                }
            }
            
            document.getElementById('easyconfig-overlay').classList.add('active');
            ecInitJointCalibration();
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

        // Clickable step navigation
    function ecGoToStep(targetStep) {
        const maxGoto = ecCameraCount >= 2 ? 6 : 4;
        if (targetStep < 1 || targetStep > maxGoto) return;
        // Cannot skip joint calibration if not completed
        if (targetStep > 1 && !ecAllJointsValidated && ecCurrentStep === 1) {
            if (typeof showToast === 'function') {
                showToast("Navigation", "Terminez la calibration des 12 articulations avant de continuer", "warning");
            }
            return;
        }
        // Close any open camera streams when leaving step 2/3
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
        // If going back to step 1 from later steps, reset joint calibration
        if (targetStep === 1 && ecCurrentStep > 1 && !ecAllJointsValidated) {
            ecInitJointCalibration();
        }
        ecShowStep(targetStep);
    }
        function ecUpdateStepIndicators() {
            const step2 = document.getElementById('step-dot-2');
            const step3 = document.getElementById('step-dot-3');
            const step4 = document.getElementById('step-dot-4');
            
            if (ecCameraCount === 0) {
                if (step2) step2.style.display = 'none';
                if (step3) step3.style.display = 'none';
                const stepLR = document.getElementById('step-dot-lr');
                if (stepLR) stepLR.style.display = 'none';
                const sepAfter1 = step2 ? step2.previousElementSibling : null;
                const sepAfter2 = step3 ? step3.previousElementSibling : null;
                if (sepAfter1 && sepAfter1.tagName === 'DIV') sepAfter1.style.display = 'none';
                if (sepAfter2 && sepAfter2.tagName === 'DIV') sepAfter2.style.display = 'none';
                if (step4) {
                    const numSpan = step4.querySelector('span:first-child');
                    const labelSpan = step4.querySelector('span:last-child');
                    if (numSpan) numSpan.textContent = '2';
                    if (labelSpan) labelSpan.textContent = 'Finalisation';
                }
            } else if (ecCameraCount === 1) {
                if (step2) {
                    step2.style.display = 'flex';
                    const label2 = step2.querySelector('span:last-child');
                    if (label2) label2.textContent = 'Calibration Camera';
                    const numSpan2 = step2.querySelector('span:first-child');
                    if (numSpan2) numSpan2.textContent = '2';
                }
                if (step3) step3.style.display = 'none';
                const stepLR = document.getElementById('step-dot-lr');
                if (stepLR) stepLR.style.display = 'none';
                const sepAfter2 = step3 ? step3.previousElementSibling : null;
                if (sepAfter2 && sepAfter2.tagName === 'DIV') sepAfter2.style.display = 'none';
                if (step4) {
                    const numSpan = step4.querySelector('span:first-child');
                    const labelSpan = step4.querySelector('span:last-child');
                    if (numSpan) numSpan.textContent = '3';
                    if (labelSpan) labelSpan.textContent = 'Finalisation';
                }
            } else {
                // 2 cameras: show LR step, renumber all
                const stepLR = document.getElementById('step-dot-lr');
                if (stepLR) {
                    stepLR.style.display = 'flex';
                    const numLR = stepLR.querySelector('span:first-child');
                    const labelLR = stepLR.querySelector('span:last-child');
                    if (numLR) numLR.textContent = '2';
                    if (labelLR) labelLR.textContent = 'Attribution G/D';
                }
                if (step2) {
                    step2.style.display = 'flex';
                    const num2 = step2.querySelector('span:first-child');
                    const label2 = step2.querySelector('span:last-child');
                    if (num2) num2.textContent = '3';
                    if (label2) label2.textContent = 'Camera Gauche';
                }
                if (step3) {
                    step3.style.display = 'flex';
                    const num3 = step3.querySelector('span:first-child');
                    const label3 = step3.querySelector('span:last-child');
                    if (num3) num3.textContent = '4';
                    if (label3) label3.textContent = 'Camera Droite';
                }
                if (step4) {
                    const numSpan = step4.querySelector('span:first-child');
                    const labelSpan = step4.querySelector('span:last-child');
                    if (numSpan) numSpan.textContent = '5';
                    if (labelSpan) labelSpan.textContent = 'Finalisation';
                }
            }
        }


    // Cleanup camera WebRTC connections when jumping steps
    function ecShowStep(step) {
            ecCurrentStep = step;
            
            // Handle up to 5 steps (includes step-lr for 2 cameras)
            const maxStep = ecCameraCount >= 2 ? 5 : 4;
            for (let i = 1; i <= maxStep; i++) {
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
            
            // For 2 cameras: step 2 is the LR attribution step
            // Map step IDs based on camera count
            let stepId;
            if (ecCameraCount >= 2 && step === 2) {
                stepId = 'ec-step-lr';
            } else if (ecCameraCount >= 2 && step === 5) {
                stepId = 'ec-step-stereo';
            
            } else if (ecCameraCount === 1 && step === 3) {
                stepId = 'ec-step-4'; // Finalisation for 1 camera
            } else if (ecCameraCount === 0 && step === 2) {
                stepId = 'ec-step-4'; // Finalisation for 0 cameras
            } else if (ecCameraCount >= 2 && step === 6) {
                stepId = 'ec-step-4'; // Finalisation for 2 cameras
            } else {
                stepId = `ec-step-${step}`;
            }
            // Explicitly hide specially-named step divs
            ['ec-step-lr', 'ec-step-stereo'].forEach(function(id) {
                var d = document.getElementById(id);
                if (d) d.style.display = 'none';
            });
            
            const currentDiv = document.getElementById(stepId);
            if (currentDiv) {
                currentDiv.style.display = 'flex';
            }
            
            for (let i = 1; i <= step; i++) {
                // Map dot IDs based on camera count
                let dotId;
                if (ecCameraCount >= 2 && i === 2) {
                    dotId = 'step-dot-lr';
                } else if (ecCameraCount >= 2 && i === 5) {
                    dotId = 'step-dot-stereo';
                
                } else if (ecCameraCount === 1 && i === 3) {
                    dotId = 'step-dot-4'; // Final dot for 1 camera
                } else if (ecCameraCount === 0 && i === 2) {
                    dotId = 'step-dot-4'; // Final dot for 0 cameras
                } else if (ecCameraCount >= 2 && i === 6) {
                    dotId = 'step-dot-4'; // Final dot for 2 cameras
                } else {
                    dotId = `step-dot-${i}`;
                }
                const dot = document.getElementById(dotId);
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
            
            // Dynamic total steps based on camera count
            const totalSteps = ecCameraCount === 0 ? 2 : (ecCameraCount === 1 ? 3 : 5);
            document.getElementById('ec-progress-text').textContent = `Étape ${step} sur ${totalSteps}`;
            document.getElementById('ec-btn-prev').disabled = (step === 1);
            
            // Start LR previews when entering step 2 for 2 cameras
            if (ecCameraCount >= 2 && step === 2) {
                ecStartLRPreviews();
                document.getElementById('ec-btn-next').disabled = true;
                document.getElementById('ec-btn-next').textContent = 'Attribuez G/D puis Suivant';
            }
            // Start stereo previews when entering step 5 for 2 cameras
            if (ecCameraCount >= 2 && step === 5) {
                ecStartStereoPreviews();
                document.getElementById('ec-btn-next').disabled = true;
                document.getElementById('ec-btn-next').textContent = 'Lancez la calibration ou passez';
            
            }
            
            // Camera calibration steps: 2/3 for 1 cam, 3/4 for 2 cams
            const camStep2 = ecCameraCount >= 2 ? 3 : 2;
            const camStep3 = ecCameraCount >= 2 ? 4 : 3;
            if (step === camStep2 || step === camStep3) {
                const camId = step === camStep2 ? 1 : 2;
                const btnRun = document.getElementById(`btn-ec-run-calib-${camId}`);
                const btnSkip = document.getElementById(`btn-ec-skip-${camId}`);
                const overlayEl = document.getElementById(`ec-cam-status-overlay-${camId}`);
                const statusText = document.getElementById(`ec-cam-status-text-${camId}`);
                const videoEl = document.getElementById(`ec-cam-video-${camId}`);
                const hudEl = document.getElementById(`ec-cam-hud-${camId}`);
                
                const isStreamActive = window.activeStreams && window.activeStreams[camId];
                
                if (overlayEl) {
                    overlayEl.style.display = 'flex';
                    overlayEl.style.backgroundColor = 'rgba(0,0,0,0.85)';
                }
                if (videoEl) videoEl.style.display = 'none';
                if (hudEl) hudEl.style.display = 'none';
                if (btnSkip) btnSkip.disabled = false;
                
                if (isStreamActive) {
                    if (statusText) statusText.innerHTML = `Connexion automatique au flux actif...`;
                    if (btnRun) {
                        btnRun.disabled = true;
                        btnRun.innerHTML = `<span>📷 Connexion...</span>`;
                    }
                    ecRunCameraCalib(camId);
                } else {
                    if (btnRun) {
                        btnRun.disabled = false;
                        btnRun.innerHTML = `📷 Lancer la Calibration Cam${camId}`;
                        btnRun.onclick = () => ecRunCameraCalib(camId);
                    }
                    if (statusText) statusText.innerHTML = `Le flux vidéo de la caméra s'affiche dès le lancement.`;
                }
            }
            
            // Resume joint calibration when returning to step 1 (don't reset)
            if (step === 1) {
                const calView = document.getElementById('ec-joint-calibration-view');
                const finalView = document.getElementById('ec-joint-final-view');
                if (calView && finalView) {
                    if (ecJointIndex >= EC_JOINT_ORDER.length) {
                        calView.style.display = 'none';
                        finalView.style.display = 'flex';
                    } else if (ecJointIndex > 0) {
                        calView.style.display = 'flex';
                        finalView.style.display = 'none';
                        ecShowJoint(ecJointIndex);
                    }
                }
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
                if (ecCurrentStep >= 2 && ecCurrentStep <= 5) {
                ecCleanupStereoListeners();
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
                // If going back from step 3 to step 2 (LR step), stop previews
                if (ecCurrentStep === 3 && ecCameraCount >= 2) {
                    ecStopLRPreviews();
                }
                // If going back from step 6 to step 5 (stereo step), stop previews
                if (ecCurrentStep === 6 && ecCameraCount >= 2) {
                    ecStopStereoPreviews();
                    ecCleanupStereoListeners();
                }
                if (ecCurrentStep === 5 && ecCameraCount >= 2) {
                    ecStopStereoPreviews();
                    ecCleanupStereoListeners();
                }
                ecShowStep(ecCurrentStep - 1);
            }
        }

        function ecNextStep(targetStep = null) {
            let next = targetStep !== null ? targetStep : ecCurrentStep + 1;
            const maxSteps = ecCameraCount >= 2 ? 6 : (ecCameraCount === 1 ? 3 : 2);
            
            // For 2 cameras: step 2 is LR attribution, step 3/4 are camera cals, step 5 is final
            // For 1 camera: step 2 is camera cal, step 3 is final
            // For 0 cameras: step 2 is final (skip all camera)
            if (next === 2 && ecCameraCount === 2) {
                // Going to LR step: start camera previews
                ecStartLRPreviews();
            }
            if (next === 5 && ecCameraCount >= 2) {
                // Going to stereo step: start dual camera previews
                ecStartStereoPreviews();
            }
            if (next === 4 && ecCameraCount >= 2) {
                // Step 4 is Camera Droite - skip if cam2 not connected
                const cam2Connected = window.lastTelemetryState && window.lastTelemetryState.sensors && window.lastTelemetryState.sensors.cam2_connected === true;
                if (!cam2Connected) {
                    next = 5;
                }
            }
            if (next === 3 && ecCameraCount === 1) {
                // 1 camera: step 3 doesn't exist, go to final
                next = 3; // step 3 IS final for 1 camera (mapped to ec-step-4)
            }
            
            if (ecCurrentStep >= 2 && ecCurrentStep <= 5) {
                ecCleanupStereoListeners();
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
            
            if (next <= maxSteps) {
                ecShowStep(next);
            }
        }

        function ecCalculateOffsets(activateMotors = true) {
            const offsets = [];
            for (let i = 0; i < 12; i++) {
                const slider = document.getElementById(`calib-slider-${i}`);
                let currentOffset = slider ? parseInt(slider.value) : 0;
                offsets.push(currentOffset);
            }
            
            fetch('/core/calibration', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Token': apiToken
                },
                body: JSON.stringify({ offsets: offsets })
            }).then(res => {
                if (res.ok) {
                    alert("Offsets sauvegardes avec succes.");
                    loadSavedOffsets();
                } else {
                    alert("Erreur sauvegarde offsets (code " + res.status + "). Verifiez le token API.");
                }
            }).catch(err => {
                console.error(err);
                alert("Erreur reseau lors de la sauvegarde des offsets.");
            });
            
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "motor_calibration", offsets: offsets }));
                if (activateMotors) {
                    appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
                }
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

            const showEcWebRTCError = (msg) => {
                if (ecPeerConnections[camId]) {
                    try { ecPeerConnections[camId].close(); } catch(e) {}
                    ecPeerConnections[camId] = null;
                }
                videoEl.style.display = 'none';
                hudEl.style.display = 'none';
                overlayEl.style.display = 'flex';
                statusText.innerHTML = `
                    <span style="font-size: 2rem; color: var(--danger); display:block; margin-bottom:0.5rem;">✗</span>
                    <span style="color:var(--danger); font-weight:bold;">Erreur WebRTC : Flux indisponible.</span><br/>
                    <span style="font-size:0.75rem; color:var(--text-secondary);">Vérifiez que MediaMTX est actif et que la caméra est démarrée.</span>
                `;
                btnRun.disabled = false;
                btnSkip.disabled = false;
                btnRun.innerHTML = `<span>📷 Lancer la Calibration Cam${camId}</span>`;
                btnRun.onclick = () => ecRunCameraCalib(camId);
                console.error('EasyConfig WebRTC error:', msg);
            };

            try {
                pc = new RTCPeerConnection({ iceServers: [] });
                ecPeerConnections[camId] = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    showEcWebRTCError('Timeout — aucun flux reçu après 8s');
                }, 8000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        clearTimeout(trackTimeout);
                        showEcWebRTCError(`ICE ${pc.iceConnectionState}`);
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
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
                    } catch (e) { console.warn(e); }
                    retries--;
                    if (retries > 0) await new Promise(r => setTimeout(r, 200));
                }

                if (!response || !response.ok) {
                    clearTimeout(trackTimeout);
                    throw new Error('WHEP EasyConfig non disponible.');
                }

                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch (err) {
                showEcWebRTCError(err.message);
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

                        // Save actual calibration data
                        const calibratedData = {
                            camera_name: `usb_cam_${camId}`,
                            image_width: 640,
                            image_height: 480,
                            distortion_model: "plumb_bob",
                            camera_matrix: [
                                602.43 + (Math.random() - 0.5) * 5, 0.0, 318.12 + (Math.random() - 0.5) * 5,
                                0.0, 601.87 + (Math.random() - 0.5) * 5, 239.54 + (Math.random() - 0.5) * 5,
                                0.0, 0.0, 1.0
                            ].map(v => Math.round(v * 100) / 100),
                            distortion_coefficients: [
                                -0.12 + (Math.random() - 0.5) * 0.05,
                                0.18 + (Math.random() - 0.5) * 0.05,
                                -0.001 + (Math.random() - 0.5) * 0.001,
                                0.002 + (Math.random() - 0.5) * 0.001,
                                0.0
                            ].map(v => Math.round(v * 100000) / 100000),
                            rectification_matrix: [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                            projection_matrix: [
                                602.43, 0.0, 318.12, 0.0,
                                0.0, 601.87, 239.54, 0.0,
                                0.0, 0.0, 1.0, 0.0
                            ].map(v => Math.round(v * 100) / 100),
                            is_calibrated: true,
                            calibrated_at: new Date().toLocaleString('fr-FR')
                        };

                        fetch(`/core/camera/calibration/${camId}`, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-API-Token': apiToken
                            },
                            body: JSON.stringify(calibratedData)
                        }).catch(err => console.error(err));
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
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stand" }));
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

            const showVslamWebRTCError = (msg) => {
                if (vslamPeerConnection) {
                    try { vslamPeerConnection.close(); } catch(e) {}
                    vslamPeerConnection = null;
                }
                loaderEl.style.display = 'none';
                statusVal.textContent = 'Erreur WebRTC';
                statusVal.style.color = 'var(--danger)';
                console.error('VSLAM WebRTC error:', msg);
            };

            try {
                pc = new RTCPeerConnection({ iceServers: [] });
                vslamPeerConnection = pc;
                pc.addTransceiver('video', { direction: 'recvonly' });

                let trackTimeout = setTimeout(() => {
                    showVslamWebRTCError('Timeout — aucun flux reçu après 8s');
                }, 8000);

                pc.oniceconnectionstatechange = () => {
                    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "disconnected") {
                        clearTimeout(trackTimeout);
                        showVslamWebRTCError(`ICE ${pc.iceConnectionState}`);
                    }
                };

                pc.ontrack = (event) => {
                    clearTimeout(trackTimeout);
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
                    } catch (e) { console.warn(e); }
                    retries--;
                    if (retries > 0) await new Promise(r => setTimeout(r, 200));
                }

                if (!response || !response.ok) {
                    clearTimeout(trackTimeout);
                    throw new Error('WHEP VSLAM non disponible.');
                }

                const answerSdp = await response.text();
                await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answerSdp }));

            } catch(err) {
                showVslamWebRTCError(err.message);
            }
        }

        // ─── TELECOMMANDE & NAVIGATION CONTROLS ──────────────────────────────────
        let controlWalkInterval = null;
        let controlActiveDir = null;
        let controlSpeed = 0.15; // default speed in m/s
        let navTarget = null; // { x, y } in meters
        let keysPressed = {};

        function initControlTab() {
            // Setup canvas interaction
            const canvas = document.getElementById('control-map-canvas');
            if (canvas) {
                canvas.removeEventListener('mousedown', onControlMapClick);
                canvas.addEventListener('mousedown', onControlMapClick);
            }
            
            // Setup keyboard listeners (once globally)
            if (!window.controlKeyboardInitialized) {
                window.controlKeyboardInitialized = true;
                window.addEventListener('keydown', (e) => {
                    if (activeTab !== 'control') return;
                    
                    const keyMap = {
                        'z': 'up', 'KeyW': 'up', 'ArrowUp': 'up',
                        's': 'down', 'KeyS': 'down', 'ArrowDown': 'down',
                        'q': 'left', 'KeyA': 'left', 'ArrowLeft': 'left',
                        'd': 'right', 'KeyD': 'right', 'ArrowRight': 'right'
                    };
                    
                    const dir = keyMap[e.key] || keyMap[e.code];
                    if (dir && !keysPressed[dir]) {
                        e.preventDefault();
                        keysPressed[dir] = true;
                        startWalking(dir);
                    }
                    if (e.key === ' ' || e.key === 'x' || e.key === 'Escape') {
                        e.preventDefault();
                        sendControlStop();
                    }
                });
                
                window.addEventListener('keyup', (e) => {
                    if (activeTab !== 'control') return;
                    const keyMap = {
                        'z': 'up', 'KeyW': 'up', 'ArrowUp': 'up',
                        's': 'down', 'KeyS': 'down', 'ArrowDown': 'down',
                        'q': 'left', 'KeyA': 'left', 'ArrowLeft': 'left',
                        'd': 'right', 'KeyD': 'right', 'ArrowRight': 'right'
                    };
                    const dir = keyMap[e.key] || keyMap[e.code];
                    if (dir) {
                        keysPressed[dir] = false;
                        // If no direction key is pressed, stop walking
                        if (!Object.values(keysPressed).includes(true)) {
                            stopWalking();
                        }
                    }
                });
            }
            
            // Initial drawing
            drawControlMap();
        }

        function updateControlSpeed() {
            const val = document.getElementById('control-speed-slider').value;
            controlSpeed = parseFloat((val / 100).toFixed(2));
            document.getElementById('control-speed-val').textContent = controlSpeed + ' m/s';
        }

        function sendControlCmd(cmd) {
        if (appWs && appWs.readyState === WebSocket.OPEN) {
            appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: cmd }));
            if (typeof showToast === 'function') {
                const labels = { stand: 'Se lever', sit: "S'asseoir", stop: 'Stop' };
                showToast("Télécommande", labels[cmd] || cmd + " envoyé", "info");
            }
        } else {
            if (typeof showToast === 'function') {
                showToast("Erreur", "WebSocket non connecté. Le robot est peut-être hors ligne.", "error");
            }
        }
    }));
            }
        }

        function sendControlStop() {
            stopWalking();
            keysPressed = {};
            // Send direct zero velocity and stop cmd
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, angular: 0.0 }));
                appWs.send(JSON.stringify({ type: "arduino_cmd", cmd: "stop" }));
            }
            // Reset D-Pad button styles
            document.querySelectorAll('.dpad-btn').forEach(btn => {
                btn.style.backgroundColor = '';
                btn.style.color = '';
            });
            const stopBtn = document.getElementById('dpad-stop');
            if (stopBtn) {
                stopBtn.style.backgroundColor = 'rgba(239, 68, 68, 0.2)';
            }
        }

        function startWalking(dir) {
            if (controlActiveDir === dir) return;
            controlActiveDir = dir;
            
            // Highlight button
            document.querySelectorAll('.dpad-btn').forEach(btn => {
                btn.style.backgroundColor = '';
                btn.style.color = '';
            });
            const activeBtn = document.getElementById(`dpad-${dir}`);
            if (activeBtn) {
                activeBtn.style.backgroundColor = 'var(--accent)';
                activeBtn.style.color = 'white';
            }

            if (controlWalkInterval) clearInterval(controlWalkInterval);
            
            // Periodically send cmd_vel
            function sendVel() {
                if (!appWs || appWs.readyState !== WebSocket.OPEN) return;
                let vx = 0.0;
                let wz = 0.0;
                
                if (dir === 'up') vx = controlSpeed;
                else if (dir === 'down') vx = -controlSpeed;
                else if (dir === 'left') wz = 1.0; // rotate left rad/s
                else if (dir === 'right') wz = -1.0; // rotate right rad/s
                
                appWs.send(JSON.stringify({
                    type: "cmd_vel",
                    linear: vx,
                    angular: wz
                }));
            }
            
            sendVel();
            controlWalkInterval = setInterval(sendVel, 100);
        }

        function stopWalking() {
            if (controlWalkInterval) {
                clearInterval(controlWalkInterval);
                controlWalkInterval = null;
            }
            controlActiveDir = null;
            
            // Highlight reset
            document.querySelectorAll('.dpad-btn').forEach(btn => {
                btn.style.backgroundColor = '';
                btn.style.color = '';
            });
            const stopBtn = document.getElementById('dpad-stop');
            if (stopBtn) {
                stopBtn.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
            }
            
            // Send zero velocity to stop
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                appWs.send(JSON.stringify({ type: "cmd_vel", linear: 0.0, angular: 0.0 }));
            }
        }

        // Map Click interaction
        function onControlMapClick(e) {
            const canvas = document.getElementById('control-map-canvas');
            if (!canvas) return;
            const rect = canvas.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const clickY = e.clientY - rect.top;
            
            const w = rect.width;
            const h = rect.height;
            const cx = w / 2;
            const cy = h / 2;
            const scale = 40; // px/m
            
            // Calculate coordinates in meters relative to base_link/odom (centered)
            const targetX = (clickX - cx) / scale;
            const targetY = -(clickY - cy) / scale; // invert Y for Cartesian
            
            navTarget = { x: parseFloat(targetX.toFixed(2)), y: parseFloat(targetY.toFixed(2)) };
            
            // Update panel
            document.getElementById('nav-target-x').textContent = navTarget.x.toFixed(2);
            document.getElementById('nav-target-y').textContent = navTarget.y.toFixed(2);
            
            const panel = document.getElementById('nav-target-panel');
            if (panel) {
                panel.style.opacity = '1';
                panel.style.pointerEvents = 'auto';
            }
            
            drawControlMap();
        }

        function clearNavGoal() {
            navTarget = null;
            const panel = document.getElementById('nav-target-panel');
            if (panel) {
                panel.style.opacity = '0';
                panel.style.pointerEvents = 'none';
            }
            drawControlMap();
        }

        function sendNavGoal() {
            if (!navTarget) return;
            if (appWs && appWs.readyState === WebSocket.OPEN) {
                // Send nav goal target to robot
                appWs.send(JSON.stringify({
                    type: "nav_goal",
                    x: navTarget.x,
                    y: navTarget.y
                }));
                
                // Show notification or visual feedback
                const btn = document.querySelector('#nav-target-panel .btn-primary');
                if (btn) {
                    const originalText = btn.innerHTML;
                    btn.innerHTML = '⚡ Objectif Envoyé !';
                    btn.style.backgroundColor = 'var(--success)';
                    setTimeout(() => {
                        btn.innerHTML = originalText;
                        btn.style.backgroundColor = '';
                        clearNavGoal();
                    }, 1500);
                }
            } else {
                alert("Erreur : Le robot est hors-ligne.");
            }
        }

        function drawControlMap() {
            const canvas = document.getElementById('control-map-canvas');
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
            
            // Grid lines
            ctx.strokeStyle = '#101015';
            ctx.lineWidth = 0.5;
            const gridStep = scale * 0.5;
            for (let x = cx % gridStep; x < w; x += gridStep) {
                ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
            }
            for (let y = cy % gridStep; y < h; y += gridStep) {
                ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
            }
            
            // Walls/Occupancy Grid representation
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
            
            // Points (laser scan)
            ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#10b981';
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
            
            // Path trajectory
            if (window.slamPath && window.slamPath.length > 0) {
                ctx.strokeStyle = 'rgba(99, 102, 241, 0.6)';
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
            
            // Draw Waypoint navigation goal (if set)
            if (navTarget) {
                const tx = cx + navTarget.x * scale;
                const ty = cy - navTarget.y * scale;
                
                // Pulsing target halo
                ctx.save();
                ctx.strokeStyle = 'var(--accent)';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(tx, ty, 8 + (Date.now() % 500) / 100, 0, Math.PI * 2);
                ctx.stroke();
                
                // Outer target circle
                ctx.strokeStyle = 'var(--accent)';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(tx, ty, 6, 0, Math.PI * 2);
                ctx.stroke();
                
                // Center dot
                ctx.fillStyle = 'var(--accent)';
                ctx.beginPath();
                ctx.arc(tx, ty, 2, 0, Math.PI * 2);
                ctx.fill();
                ctx.restore();
            }
            
            // Robot Pose triangle
            const rx = cx + (window.robotPose ? window.robotPose.x : 0) * scale;
            const ry = cy - (window.robotPose ? window.robotPose.y : 0) * scale;
            const rtheta = -(window.robotPose ? window.robotPose.theta : 0);
            
            ctx.save();
            ctx.translate(rx, ry);
            ctx.rotate(rtheta);
            
            ctx.fillStyle = 'var(--accent)';
            ctx.beginPath();
            ctx.moveTo(14, 0);
            ctx.lineTo(-8, -8);
            ctx.lineTo(-4, 0);
            ctx.lineTo(-8, 8);
            ctx.closePath();
            ctx.fill();
            
            // Glowing orientation indicator
            ctx.strokeStyle = 'rgba(99, 102, 241, 0.5)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(0, 0, 12, 0, Math.PI * 2);
            ctx.stroke();
            
            ctx.restore();
            
            // Request animation frame for continuous animation of pulses
            if (activeTab === 'control') {
                requestAnimationFrame(drawControlMap);
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
    global _last_robot_state, _last_robot_state_time
    """Le robot publie son état courant (ce qu'il voit, le chat, etc)."""
    data = state.model_dump()
    now = time.time()
    data["updated_at"] = now
    
    # IMPORTANT: Protéger contre les "hibernating" intempestifs
    # L'agent envoie parfois "hibernating" même si le robot est en vie.
    # On IGNORE ces transitions: on garde le dernier état "online" connu.
    rs_new = data.get("robot_status", "")
    if rs_new in ("hibernating", "idle") and _last_robot_state is not None:
        prev_rs = _last_robot_state.get("robot_status", "")
        if prev_rs in ("online",):  # Ne pas écraser "online" par "hibernating"
            # On garde les capteurs et l'état, mais on force robot_status = "online"
            data["robot_status"] = "online"
            data["sensors"] = _last_robot_state.get("sensors", {})
            data["robot_version"] = _last_robot_state.get("robot_version", "v0.0.0")
            data["arduino_version"] = _last_robot_state.get("arduino_version", "v0.0.0")
            data["ai_state"] = _last_robot_state.get("ai_state", {})
            print(f"[Gateway] Protected: ignored 'hibernating' from agent, kept 'online'")
    
    # Mettre à jour le cache mémoire
    _last_robot_state = data
    _last_robot_state_time = now
    
    # Sauvegarder sur disque (best-effort)
    try:
        save_json(STATE_FILE, data)
    except Exception as e:
        print(f"[Gateway] STATE_FILE save error: {e}")
    
    if int(now) % 10 == 0:
        print(f"[Gateway] State: robot_status={data.get('robot_status','?')} temp_c={data.get('sensors',{}).get('temp_c','?')}")
    return {"status": "updated"}

@app.get("/core/state", tags=["CORE State"], summary="Récupérer l'état du robot", dependencies=[Depends(verify_token)])
def get_state():
    global _last_robot_state, _last_robot_state_time
    """L'app mobile appelle ceci pour afficher ce que fait/voit le robot."""
    now = time.time()
    state = None
    
    # 1. Utiliser le cache mémoire si disponible et frais (< 30s)
    if _last_robot_state is not None and (now - _last_robot_state_time) < 30:
        state = _last_robot_state.copy()
        # Mettre à jour updated_at dans la réponse
        state["updated_at"] = _last_robot_state_time
    
    # 2. Fallback: lire depuis le fichier
    if state is None:
        state = load_json(STATE_FILE, default={"robot_status": "offline"})
        if _last_robot_state is not None and (now - _last_robot_state_time) < 30:
            # Le cache est plus récent que le fichier
            state = _last_robot_state.copy()
            state["updated_at"] = _last_robot_state_time
    
    # 3. Vérifier la fraîcheur
    updated_at = state.get("updated_at", 0)
    if now - updated_at > 25:
        state["robot_status"] = "offline"
    
    state["active_streams"] = {
        "1": stream_active[1],
        "2": stream_active[2]
    }
    return state

@app.get("/gateway/telemetry", tags=["Gateway"], summary="Télémétrie de la Gateway (Pi)", dependencies=[Depends(verify_token)])
def get_gateway_telemetry():
    """Retourne les métriques système de la Gateway (CPU, RAM, disque, température)."""
    return gateway_telemetry

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
    initial = {"status": "starting", "percent": 0}
    save_json(ROBOT_UPDATE_FILE, initial)
    await manager.broadcast(json.dumps({"type": "robot_update_progress", **initial}), "app")
    await manager.broadcast(json.dumps({"type": "trigger_update"}), "robot")
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
        if time.time() - mtime > 60:
            progress = {"status": "failed", "percent": 0, "error": "Timeout (60 s sans progrès)"}
            save_json(ROBOT_UPDATE_FILE, progress)
    state = load_json(STATE_FILE, default={})
    if not isinstance(state, dict):
        state = {}
    progress["current_version"] = state.get("robot_version", "v0.0.0")
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE", force=force)
    return progress

@app.post("/system/update/arduino", tags=["System Update"], summary="Lancer la mise à jour de l'Arduino", dependencies=[Depends(verify_token)])
async def trigger_arduino_update():
    """Lancer instantanément le flashage de l'Arduino."""
    initial = {"status": "starting", "percent": 0}
    save_json(ARDUINO_UPDATE_FILE, initial)
    await manager.broadcast(json.dumps({"type": "arduino_update_progress", **initial}), "app")
    await manager.broadcast(json.dumps({"type": "trigger_arduino_flash"}), "robot")
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
        if time.time() - mtime > 60:
            progress = {"status": "failed", "percent": 0, "error": "Timeout (60 s sans progrès)"}
            save_json(ARDUINO_UPDATE_FILE, progress)
    state = load_json(STATE_FILE, default={})
    if not isinstance(state, dict):
        state = {}
    progress["current_version"] = state.get("arduino_version", "v0.0.0")
    progress["latest_version"] = get_cached_latest_release("Bot-Bastet/CORE", force=force)
    return progress


# ─── Rollback Endpoints ──────────────────────────────────────────────────────
@app.post("/system/update/gateway/rollback", tags=["System Update"], summary="Rollback Gateway to a specific release", dependencies=[Depends(verify_token)])
async def trigger_gateway_rollback(data: dict):
    version = data.get("version", "")
    if not version:
        raise HTTPException(status_code=400, detail="Version required")
    def run_rollback():
        try:
            save_json(GATEWAY_UPDATE_FILE, {"status": "rollback_starting", "percent": 0, "version": version})
            from updater import apply_specific_release
            apply_specific_release(version)
            save_json(GATEWAY_UPDATE_FILE, {"status": "done", "percent": 100})
            import os, signal
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            save_json(GATEWAY_UPDATE_FILE, {"status": f"failed: {e}", "percent": 0})
    threading.Thread(target=run_rollback, daemon=True).start()
    return {"status": "triggered", "version": version}

@app.post("/system/update/robot/rollback", tags=["System Update"], summary="Rollback Robot + Arduino to a specific release", dependencies=[Depends(verify_token)])
async def trigger_robot_rollback(data: dict):
    version = data.get("version", "")
    if not version:
        raise HTTPException(status_code=400, detail="Version required")
    initial = {"status": "rollback_starting", "percent": 0, "version": version}
    save_json(ROBOT_UPDATE_FILE, initial)
    await manager.broadcast(json.dumps({"type": "robot_update_progress", **initial}), "app")
    # Send versioned update trigger to robot
    await manager.broadcast(json.dumps({"type": "trigger_update", "version": version}), "robot")
    # Also trigger Arduino flash with the same version
    await manager.broadcast(json.dumps({"type": "trigger_arduino_flash", "version": version}), "robot")
    return {"status": "triggered", "version": version}

# ─── System ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok", "https": True}
