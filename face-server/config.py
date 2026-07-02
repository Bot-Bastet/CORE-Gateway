"""Configuration, global state, and helper functions for Bastet Gateway."""
import os
import json
import time
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import WebSocket

# ─── Paths ─────────────────────────────────────────────────────────────────
FACES_DIR = Path(os.getenv("FACES_DIR", "/data/faces"))
DATA_DIR = Path("/data")
META_FILE = FACES_DIR / "meta.json"
MYGES_FILE = DATA_DIR / "myges.json"
STATE_FILE = DATA_DIR / "core_state.json"
USERS_FILE = DATA_DIR / "users.json"
CALIBRATION_FILE = DATA_DIR / "calibration.json"
GATEWAY_UPDATE_FILE = DATA_DIR / "gateway_update_state.json"
ROBOT_UPDATE_FILE = DATA_DIR / "robot_update_state.json"
ARDUINO_UPDATE_FILE = DATA_DIR / "arduino_update_state.json"
CAMERA_CALIB_1_FILE = DATA_DIR / "camera_calib_1.json"
CAMERA_CALIB_2_FILE = DATA_DIR / "camera_calib_2.json"

FACES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ─── Global State ──────────────────────────────────────────────────────────
latest_diagnostics = {}
gateway_telemetry = {"cpu_percent": 0, "ram_percent": 0, "disk_percent": 0, "temp_c": 0, "uptime_s": 0}
_last_robot_state = None
_last_robot_state_time = 0

API_TOKEN = os.getenv("API_TOKEN", "your-api-token-here")

DEFAULT_CAM_CALIB = {
    "image_width": 640, "image_height": 480, "camera_name": "usb_cam",
    "camera_matrix": {"rows": 3, "cols": 3, "data": [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0]},
    "distortion_model": "plumb_bob",
    "distortion_coefficients": {"rows": 1, "cols": 5, "data": [0.0, 0.0, 0.0, 0.0, 0.0]},
    "rectification_matrix": {"rows": 3, "cols": 3, "data": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]},
    "projection_matrix": {"rows": 3, "cols": 4, "data": [600.0, 0.0, 320.0, 0.0, 0.0, 600.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]},
}

GITHUB_RELEASES_CACHE = {}

# ─── Helpers ───────────────────────────────────────────────────────────────

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
            path.unlink()
            modified = True
        else:
            seen_hashes.add(unique_id)
            entry["hash"] = file_hash
            new_meta.append(entry)
    if modified:
        save_json(META_FILE, new_meta)
        print("🧹 Nettoyage des doublons terminé.")

# ─── WebSocket State ───────────────────────────────────────────────────────
import asyncio

active_camera_listeners = {1: set(), 2: set()}
stream_active = {1: False, 2: False}
stream_v_slam = {1: False, 2: False}
stream_keep_alive = {1: False, 2: False}
camera_stop_timers = {1: None, 2: None}

preferred_ai_targets = {
    "tts": "robot", "stt": "robot", "chat": "robot",
    "yolo": "robot", "face_rec": "robot",
}

async def stop_camera_delayed(cam_id: int, manager):
    await asyncio.sleep(30)
    if stream_keep_alive[cam_id]:
        return
    if len(active_camera_listeners[cam_id]) == 0:
        stream_active[cam_id] = False
        await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": False}), "app")

def cleanup_camera_listeners(websocket: WebSocket, manager):
    for cam_id in [1, 2]:
        if websocket in active_camera_listeners[cam_id]:
            active_camera_listeners[cam_id].remove(websocket)
            if len(active_camera_listeners[cam_id]) == 0:
                if camera_stop_timers[cam_id] is not None:
                    camera_stop_timers[cam_id].cancel()
                camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {
            "robot": [], "node": [], "app": []
        }
    async def connect(self, websocket: WebSocket, client_type: str):
        await websocket.accept()
        if client_type in self.active_connections:
            self.active_connections[client_type].append(websocket)
    def disconnect(self, websocket: WebSocket, client_type: str):
        if client_type in self.active_connections and websocket in self.active_connections[client_type]:
            self.active_connections[client_type].remove(websocket)
        cleanup_camera_listeners(websocket, self)
    async def broadcast(self, message: str, target_client_type: str):
        for connection in self.active_connections.get(target_client_type, []):
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()
