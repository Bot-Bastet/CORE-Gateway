import ssl
import threading
from pathlib import Path

# Endpoints et tokens
GATEWAY_URL = "https://ha.arthonetwork.fr:44888"
WS_URL = "wss://ha.arthonetwork.fr:44888/ws/robot"
API_TOKEN = "bst_c9f28d3a1e4b85c7f0d4b9a2e6f1c3d5"

# Fichiers de configuration et version
VERSION_FILE = Path("/opt/spotbot/version.txt")
CAMERA_MAPPING_FILE = Path("/opt/spotbot/config/camera_mapping.json")
CALIB_STATUS_FILE = Path("/opt/spotbot/config/calib_status.json")
CAMERA_CALIB_MONO_FILE = Path("/opt/spotbot/config/camera_mono.yaml")
CAMERA_CALIB_LEFT_FILE = Path("/opt/spotbot/config/camera_stereo_left.yaml")
CAMERA_CALIB_RIGHT_FILE = Path("/opt/spotbot/config/camera_stereo_right.yaml")

# Contexte SSL pour requêtes Gateway (auto-signées)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# Variables globales d'état ROS 2
ros2_process = None
latest_telemetry = None

# Pipeline d'état IA
tts_target = "robot"
stt_target = "robot"
chat_target = "robot"
yolo_state = "robot"
face_rec_state = "robot"

# Événements de contrôle pour l'annulation de la calibration
calibration_cancel_events = {
    1: threading.Event(),
    2: threading.Event()
}
stereo_calibration_cancel_event = threading.Event()

def get_version() -> str:
    if VERSION_FILE.exists():
        try:
            return VERSION_FILE.read_text().strip()
        except Exception:
            pass
    return "v0.0.0"
