"""Configuration, global state, and helper functions for Bastet Gateway."""
import os
import json
import time
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import WebSocket
import asyncio

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
STEREO_CALIB_FILE = DATA_DIR / "camera_calib_stereo.json"

FACES_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ─── Gateway State ────────────────────────────────

class GatewayState:
    """Centralized mutable state for the Bastet Gateway."""

    def __init__(self):
        self.latest_diagnostics: dict = {}
        self.gateway_telemetry: dict = {
            "cpu_percent": 0, "ram_percent": 0, "disk_percent": 0,
            "temp_c": 0, "uptime_s": 0,
        }
        self.last_robot_state = None
        self.last_robot_state_time: float = 0.0
        self.active_camera_listeners: dict = {1: set(), 2: set()}
        self.stream_active: dict = {1: False, 2: False}
        self.stream_v_slam: dict = {1: False, 2: False}
        self.stream_keep_alive: dict = {1: False, 2: False}
        self.camera_stop_timers: dict = {1: None, 2: None}
        self.rest_camera_listeners: dict = {1: set(), 2: set()}
        self.camera_idle_kill_at: dict = {1: 0.0, 2: 0.0}
        self.preferred_ai_targets: dict = {
            "tts": "robot", "stt": "robot", "chat": "robot",
            "yolo": "robot", "face_rec": "robot",
        }
        self.github_releases_cache: dict = {}

    @property
    def _lock(self) -> "asyncio.Lock":
        try:
            return self.__dict__['_lock_obj']
        except KeyError:
            self.__dict__['_lock_obj'] = asyncio.Lock()
            return self.__dict__['_lock_obj']

    async def set_diagnostics(self, data: dict) -> None:
        async with self._lock:
            self.latest_diagnostics.clear()
            self.latest_diagnostics.update(data)

    def snapshot_diagnostics(self) -> dict:
        return dict(self.latest_diagnostics)


state = GatewayState()

# Backward-compatible module-level aliases
latest_diagnostics   = state.latest_diagnostics
gateway_telemetry    = state.gateway_telemetry
_last_robot_state    = state.last_robot_state
_last_robot_state_time = state.last_robot_state_time
active_camera_listeners = state.active_camera_listeners
stream_active        = state.stream_active
stream_v_slam        = state.stream_v_slam
stream_keep_alive    = state.stream_keep_alive
camera_stop_timers   = state.camera_stop_timers
rest_camera_listeners = state.rest_camera_listeners
camera_idle_kill_at  = state.camera_idle_kill_at
preferred_ai_targets = state.preferred_ai_targets
GITHUB_RELEASES_CACHE = state.github_releases_cache

API_TOKEN = os.getenv("API_TOKEN", "your-api-token-here")

DEFAULT_CAM_CALIB = {
    "image_width": 640, "image_height": 480, "camera_name": "usb_cam",
    "camera_matrix": {"rows": 3, "cols": 3, "data": [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0]},
    "distortion_model": "plumb_bob",
    "distortion_coefficients": {"rows": 1, "cols": 5, "data": [0.0, 0.0, 0.0, 0.0, 0.0]},
    "rectification_matrix": {"rows": 3, "cols": 3, "data": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]},
    "projection_matrix": {"rows": 3, "cols": 4, "data": [600.0, 0.0, 320.0, 0.0, 0.0, 600.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]},
}

DEFAULT_STEREO_CALIB = {
    "image_width": 640, "image_height": 480,
    "camera_name": "usb_cam",
    # Left camera intrinsics
    "camera_matrix": [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0],
    "distortion_coefficients": [0.0, 0.0, 0.0, 0.0, 0.0],
    # Right camera intrinsics
    "camera2_matrix": [600.0, 0.0, 320.0, 0.0, 600.0, 240.0, 0.0, 0.0, 1.0],
    "camera2_distortion": [0.0, 0.0, 0.0, 0.0, 0.0],
    # Stereo extrinsics (left → right transformation)
    "R": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    "T": [0.0, 0.0, 0.0],
    "E": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "F": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    # Rectification
    "R1": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    "R2": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
    "P1": [600.0, 0.0, 320.0, 0.0, 0.0, 600.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    "P2": [600.0, 0.0, 320.0, -30.0, 0.0, 600.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    "Q": [1.0, 0.0, 0.0, -320.0, 0.0, 1.0, 0.0, -240.0, 0.0, 0.0, 0.0, 600.0, 0.0, 0.0, 0.0167, 0.0],
    # ORB_SLAM3-specific
    "baseline_m": 0.05,
    "camera_bf": 30.0,
    "th_depth": 40.0,
    "is_calibrated": False,
    "reprojection_error": 0.0,
}

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
            file_hash = hashlib.md5(f.read()).hexdigest()  # nosemgrep: python.lang.security.audit.insecure-md5-algorithm — used for file deduplication fingerprint, not cryptographic security
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

def normalize_camera_manifest(sensors: dict) -> None:
    """Calcule cam1_connected / cam2_connected a partir de available_video_devices."""
    devices = sensors.get("available_video_devices")
    if not isinstance(devices, list):
        return
    mapping = sensors.get("camera_mapping")

    def _resolve_dev(side_key: str):
        if not isinstance(mapping, dict):
            return None
        val = mapping.get(side_key)
        if isinstance(val, dict):
            return val.get("device")
        if isinstance(val, str):
            return val
        return None

    if isinstance(mapping, dict) and (_resolve_dev("left") or _resolve_dev("right")):
        left_dev = _resolve_dev("left")
        right_dev = _resolve_dev("right")
        if left_dev and right_dev and left_dev == right_dev and len(devices) == 1:
            sensors["cam1_connected"] = True
            sensors["cam2_connected"] = False
        else:
            sensors["cam1_connected"] = bool(left_dev and left_dev in devices)
            sensors["cam2_connected"] = bool(right_dev and right_dev in devices)
    else:
        sensors["cam1_connected"] = len(devices) >= 1
        sensors["cam2_connected"] = len(devices) >= 2


# ─── Connection Manager ──────────────────────────────────────────────────
# Extracted to connection_manager.py to avoid circular imports.
# Re-export for backward compatibility:
from connection_manager import (
    ConnectionManager, manager, total_consumers, should_schedule_idle_kill,
    stop_camera_delayed, cleanup_camera_listeners, STOP_DELAY_SECONDS,
)
