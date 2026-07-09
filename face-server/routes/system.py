"""System routes: core state, MyGES, calibration, diagnostics, health."""
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from config import (
    DATA_DIR, STATE_FILE, MYGES_FILE, CALIBRATION_FILE,
    CAMERA_CALIB_1_FILE, CAMERA_CALIB_2_FILE, DEFAULT_CAM_CALIB,
    STEREO_CALIB_FILE, DEFAULT_STEREO_CALIB,
    stream_active, gateway_telemetry, latest_diagnostics,
    load_json, save_json, manager, normalize_camera_manifest,
)
from auth import verify_token
from models import (
    CoreState, MyGESCredentials, NavGoalRequest, MotionVelocityRequest,
    MotionJointsRequest, ArduinoCommandRequest, ChatRequest
)
from myges_api import MyGesAPI

router = APIRouter(tags=["System"])


# ─── Health ────────────────────────────────────────────────────────────────

@router.get("/health", tags=["System"], summary="Health check")
def health():
    return {"status": "ok", "https": True}


# ─── Core State ────────────────────────────────────────────────────────────

@router.post("/core/state", summary="Mettre à jour l'état du robot", dependencies=[Depends(verify_token)])
async def update_state(state: CoreState):
    import config
    data = state.model_dump()
    now = time.time()
    data["updated_at"] = now

    rs_new = data.get("robot_status", "")
    if rs_new in ("hibernating", "idle") and config._last_robot_state is not None:
        prev_rs = config._last_robot_state.get("robot_status", "")
        if prev_rs in ("online",):
            data["robot_status"] = "online"
            data["sensors"] = config._last_robot_state.get("sensors", {})
            data["robot_version"] = config._last_robot_state.get("robot_version", "v0.0.0")
            data["arduino_version"] = config._last_robot_state.get("arduino_version", "v0.0.0")
            data["ai_state"] = config._last_robot_state.get("ai_state", {})

    sensors_in = data.get("sensors")
    if isinstance(sensors_in, dict):
        normalize_camera_manifest(sensors_in)

    config._last_robot_state = data
    config._last_robot_state_time = now

    try:
        save_json(STATE_FILE, data)
    except Exception as e:
        print(f"[Gateway] STATE_FILE save error: {e}")

    await manager.broadcast(json.dumps({
        "type": "state",
        "payload": data
    }), "app")

    if int(now) % 10 == 0:
        print(f"[Gateway] State: robot_status={data.get('robot_status','?')} temp_c={data.get('sensors',{}).get('temp_c','?')}")
    return {"status": "updated"}


@router.get("/core/state", summary="Récupérer l'état du robot", dependencies=[Depends(verify_token)])
def get_state():
    import config
    now = time.time()
    state = None
    if config._last_robot_state is not None and (now - config._last_robot_state_time) < 30:
        state = config._last_robot_state.copy()
        state["updated_at"] = config._last_robot_state_time
    if state is None:
        state = load_json(STATE_FILE, default={"robot_status": "offline"})
        if config._last_robot_state is not None and (now - config._last_robot_state_time) < 30:
            state = config._last_robot_state.copy()
            state["updated_at"] = config._last_robot_state_time
    updated_at = state.get("updated_at", 0)
    if now - updated_at > 25:
        state["robot_status"] = "offline"
    state["active_streams"] = {"1": stream_active[1], "2": stream_active[2]}
    sensors_out = state.get("sensors")
    if isinstance(sensors_out, dict):
        normalize_camera_manifest(sensors_out)
    return state


# ─── Gateway Telemetry ─────────────────────────────────────────────────────

@router.get("/gateway/telemetry", summary="Télémétrie de la Gateway (Pi)", dependencies=[Depends(verify_token)])
def get_gateway_telemetry():
    return gateway_telemetry


# ─── Stream Quality Config ───────────────────────────────────────────────────

STREAM_CONFIG_FILE = DATA_DIR / "stream_config.json"
DEFAULT_STREAM_CONFIG = {
    "cam1": {"stream_res": "1280x720", "stream_fps": 20, "vslam_res": "640x480", "codec": "auto"},
    "cam2": {"stream_res": "1280x720", "stream_fps": 20, "vslam_res": "640x480", "codec": "auto"},
}

@router.get("/core/stream/config", summary="Récupérer la config qualité stream", dependencies=[Depends(verify_token)])
def get_stream_config():
    return load_json(STREAM_CONFIG_FILE, default=DEFAULT_STREAM_CONFIG)

@router.post("/core/stream/config", summary="Sauvegarder la config qualité stream", dependencies=[Depends(verify_token)])
async def save_stream_config(data: dict):
    save_json(STREAM_CONFIG_FILE, data)
    await manager.broadcast(json.dumps({"type": "stream_quality_config", "config": data}), "robot")
    return {"status": "saved"}


# ─── Diagnostics ───────────────────────────────────────────────────────────

@router.get("/core/diagnostics", summary="Récupérer les diagnostics temps-réel", dependencies=[Depends(verify_token)])
def get_diagnostics():
    return latest_diagnostics


# ─── Calibration ───────────────────────────────────────────────────────────

@router.post("/core/calibration", summary="Sauvegarder les offsets de calibration", dependencies=[Depends(verify_token)])
def save_calibration(data: dict):
    save_json(CALIBRATION_FILE, data)
    return {"status": "saved"}

@router.get("/core/calibration", summary="Récupérer les offsets de calibration", dependencies=[Depends(verify_token)])
def get_calibration():
    return load_json(CALIBRATION_FILE, default={"offsets": [0]*12})

@router.get("/core/camera/calibration/{cam_id}", summary="Récupérer la calibration d'une caméra", dependencies=[Depends(verify_token)])
def get_camera_calibration(cam_id: int):
    if cam_id == 1:
        return load_json(CAMERA_CALIB_1_FILE, default=DEFAULT_CAM_CALIB)
    else:
        return load_json(CAMERA_CALIB_2_FILE, default=DEFAULT_CAM_CALIB)


@router.post("/core/camera/calibration/{cam_id}", summary="Sauvegarder la calibration d'une caméra", dependencies=[Depends(verify_token)])
def save_camera_calibration(cam_id: int, data: dict):
    if cam_id == 1:
        save_json(CAMERA_CALIB_1_FILE, data)
    else:
        save_json(CAMERA_CALIB_2_FILE, data)
    return {"status": "saved"}


@router.get("/core/camera/calibration/stereo", summary="Récupérer la calibration stéréo", dependencies=[Depends(verify_token)])
def get_stereo_calibration():
    return load_json(STEREO_CALIB_FILE, default=DEFAULT_STEREO_CALIB)


@router.post("/core/camera/calibration/stereo", summary="Sauvegarder la calibration stéréo", dependencies=[Depends(verify_token)])
def save_stereo_calibration(data: dict):
    save_json(STEREO_CALIB_FILE, data)
    return {"status": "saved"}


@router.post("/core/camera/calibration/reset", summary="Réinitialiser toutes les calibrations caméra aux valeurs par défaut", dependencies=[Depends(verify_token)])
def reset_camera_calibrations():
    """Remet CAMERA_CALIB_1, CAMERA_CALIB_2 et STEREO_CALIB aux valeurs DEFAULT."""
    save_json(CAMERA_CALIB_1_FILE, DEFAULT_CAM_CALIB)
    save_json(CAMERA_CALIB_2_FILE, DEFAULT_CAM_CALIB)
    save_json(STEREO_CALIB_FILE, DEFAULT_STEREO_CALIB)
    return {"status": "reset", "message": "Toutes les calibrations camera ont ete reinitialisees aux valeurs par defaut."}


# ─── MyGES ─────────────────────────────────────────────────────────────────

@router.post("/myges", summary="Enregistrer id/mdp MyGES", dependencies=[Depends(verify_token)])
def save_myges(creds: MyGESCredentials, name: str = Query(..., description="Nom de l'utilisateur (ex: Teano)")):
    all_comptes = load_json(MYGES_FILE, default={})
    all_comptes[name] = {"username": creds.username, "password": creds.password, "updated_at": time.time()}
    save_json(MYGES_FILE, all_comptes)
    return {"status": "saved", "user": name}

@router.get("/myges", summary="Récupérer id/mdp MyGES", dependencies=[Depends(verify_token)])
def get_myges():
    data = load_json(MYGES_FILE, default={})
    if not data:
        raise HTTPException(status_code=404, detail="No credentials stored")
    return data

@router.post("/myges/test", summary="Tester les identifiants MyGES", dependencies=[Depends(verify_token)])
def test_myges(creds: MyGESCredentials):
    try:
        api = MyGesAPI(creds.username, creds.password)
        if not api.token:
            return {"status": "error", "message": "Authentification échouée — identifiants invalides."}
        agenda = api.get_upcoming_agenda_text(days=7)
        courses_count = agenda.count("- De") if "- De" in agenda else 0
        return {
            "status": "success",
            "message": f"Connexion réussie ! {courses_count} cours trouvés pour les 7 prochains jours.",
            "agenda_preview": agenda[:500]
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur: {str(e)}"}


# ─── Robot Control REST Endpoints ──────────────────────────────────────────

@router.post("/api/robot/navigation/goal", summary="Envoyer un objectif de navigation", dependencies=[Depends(verify_token)])
async def send_nav_goal_rest(req: NavGoalRequest):
    """Envoyer un objectif de navigation (x, y) au robot."""
    payload = {
        "type": "nav_goal",
        "x": req.x,
        "y": req.y
    }
    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Objectif de navigation envoyé."}


@router.post("/api/robot/motion/velocity", summary="Envoyer une commande de vitesse", dependencies=[Depends(verify_token)])
async def send_motion_velocity_rest(req: MotionVelocityRequest):
    """Envoyer une commande de vitesse (téléopération) au robot."""
    payload = {
        "type": "cmd_vel",
        "linear": req.linear,
        "angular": req.angular
    }
    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Commande de vitesse envoyée."}


@router.post("/api/robot/motion/joints", summary="Envoyer les angles des servos ROS", dependencies=[Depends(verify_token)])
async def send_motion_joints_rest(req: MotionJointsRequest):
    """Envoyer les angles des 12 servos ROS au robot."""
    payload = {
        "type": "manual_joint_control",
        "angles": req.angles
    }
    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Commande d'articulations envoyée."}


@router.post("/api/robot/arduino/command", summary="Envoyer une commande directe à l'Arduino", dependencies=[Depends(verify_token)])
async def send_arduino_cmd_rest(req: ArduinoCommandRequest):
    """Envoyer une commande à l'Arduino Mega."""
    payload = {
        "type": "arduino_cmd",
        "cmd": req.cmd
    }
    if req.index is not None:
        payload["index"] = req.index
    if req.angle is not None:
        payload["angle"] = req.angle

    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Commande Arduino envoyée."}


@router.post("/api/robot/chat", summary="Envoyer un message textuel au système de chat IA", dependencies=[Depends(verify_token)])
async def send_chat_rest(req: ChatRequest):
    """Envoyer un message de chat au robot."""
    payload = {
        "type": "chat",
        "text": req.text
    }
    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Message de chat envoyé."}

