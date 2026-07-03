"""System routes: core state, MyGES, calibration, updates, diagnostics, health."""
import os
import json
import time
import threading
import requests

from fastapi import APIRouter, Depends, HTTPException, Query

from config import (
    STATE_FILE, MYGES_FILE, USERS_FILE, CALIBRATION_FILE,
    CAMERA_CALIB_1_FILE, CAMERA_CALIB_2_FILE, DEFAULT_CAM_CALIB,
    GATEWAY_UPDATE_FILE, ROBOT_UPDATE_FILE, ARDUINO_UPDATE_FILE,
    GITHUB_RELEASES_CACHE, stream_active, gateway_telemetry, latest_diagnostics,
    load_json, save_json, manager,
)
from auth import verify_token
from models import CoreState, UpdateProgress, MyGESCredentials
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

    config._last_robot_state = data
    config._last_robot_state_time = now

    try:
        save_json(STATE_FILE, data)
    except Exception as e:
        print(f"[Gateway] STATE_FILE save error: {e}")

    # Broadcast state to all connected app clients so the dashboard
    # shows 🟢 En ligne in real-time instead of stale "Hors ligne".
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
    """Teste si les identifiants MyGES sont valides en tentant de récupérer l'agenda."""
    try:
        api = MyGesAPI(creds.username, creds.password)
        if not api.token:
            return {"status": "error", "message": "Authentification échouée — identifiants invalides."}
        agenda = api.get_upcoming_agenda_text(days=7)
        # Count courses
        courses_count = agenda.count("- De") if "- De" in agenda else 0
        return {
            "status": "success",
            "message": f"Connexion réussie ! {courses_count} cours trouvés pour les 7 prochains jours.",
            "agenda_preview": agenda[:500]
        }
    except Exception as e:
        return {"status": "error", "message": f"Erreur: {str(e)}"}


# ─── Version Helpers ───────────────────────────────────────────────────────

def get_cached_latest_release(repo: str, force: bool = False) -> str:
    now = time.time()
    if not force and repo in GITHUB_RELEASES_CACHE:
        tag, cached_time = GITHUB_RELEASES_CACHE[repo]
        if now - cached_time < 300:
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


# ─── System Updates ────────────────────────────────────────────────────────

@router.post("/system/update/gateway", summary="Lancer la mise à jour de la Gateway", dependencies=[Depends(verify_token)])
async def trigger_gateway_update():
    def run_up():
        try:
            from updater import check_and_apply_update
            save_json(GATEWAY_UPDATE_FILE, {"status": "starting", "percent": 0})
            updated = check_and_apply_update()
            if updated:
                import signal as sig
                save_json(GATEWAY_UPDATE_FILE, {"status": "done", "percent": 100})
                os.kill(os.getpid(), sig.SIGTERM)
            else:
                save_json(GATEWAY_UPDATE_FILE, {"status": "idle", "percent": 100})
        except Exception as e:
            save_json(GATEWAY_UPDATE_FILE, {"status": f"failed: {e}", "percent": 0})
    threading.Thread(target=run_up, daemon=True).start()
    return {"status": "triggered"}


@router.get("/system/update/gateway/progress", summary="Récupérer le progrès de mise à jour Gateway", dependencies=[Depends(verify_token)])
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


@router.post("/system/update/gateway/progress", summary="Mettre à jour le progrès Gateway", dependencies=[Depends(verify_token)])
async def update_gateway_progress(progress: UpdateProgress):
    data = progress.model_dump()
    save_json(GATEWAY_UPDATE_FILE, data)
    await manager.broadcast(json.dumps({"type": "gateway_update_progress", **data}), "app")
    return {"status": "ok"}


@router.post("/system/update/robot", summary="Lancer la mise à jour du robot", dependencies=[Depends(verify_token)])
async def trigger_robot_update():
    initial = {"status": "starting", "percent": 0}
    save_json(ROBOT_UPDATE_FILE, initial)
    await manager.broadcast(json.dumps({"type": "robot_update_progress", **initial}), "app")
    await manager.broadcast(json.dumps({"type": "trigger_update"}), "robot")
    return {"status": "triggered"}


@router.post("/system/update/robot/progress", summary="Mettre à jour le progrès du robot", dependencies=[Depends(verify_token)])
async def update_robot_progress(progress: UpdateProgress):
    data = progress.model_dump()
    save_json(ROBOT_UPDATE_FILE, data)
    await manager.broadcast(json.dumps({"type": "robot_update_progress", **data}), "app")
    return {"status": "ok"}


@router.get("/system/update/robot/progress", summary="Récupérer le progrès de mise à jour robot", dependencies=[Depends(verify_token)])
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


@router.post("/system/update/arduino", summary="Lancer la mise à jour de l'Arduino", dependencies=[Depends(verify_token)])
async def trigger_arduino_update():
    initial = {"status": "starting", "percent": 0}
    save_json(ARDUINO_UPDATE_FILE, initial)
    await manager.broadcast(json.dumps({"type": "arduino_update_progress", **initial}), "app")
    await manager.broadcast(json.dumps({"type": "trigger_arduino_flash"}), "robot")
    return {"status": "triggered"}


@router.post("/system/update/arduino/progress", summary="Mettre à jour le progrès de l'Arduino", dependencies=[Depends(verify_token)])
async def update_arduino_progress(progress: UpdateProgress):
    data = progress.model_dump()
    save_json(ARDUINO_UPDATE_FILE, data)
    await manager.broadcast(json.dumps({"type": "arduino_update_progress", **data}), "app")
    return {"status": "ok"}


@router.get("/system/update/arduino/progress", summary="Récupérer le progrès de mise à jour Arduino", dependencies=[Depends(verify_token)])
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


# ─── Rollback ──────────────────────────────────────────────────────────────

@router.post("/system/update/gateway/rollback", summary="Rollback Gateway to a specific release", dependencies=[Depends(verify_token)])
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
            import signal as sig
            os.kill(os.getpid(), sig.SIGTERM)
        except Exception as e:
            save_json(GATEWAY_UPDATE_FILE, {"status": f"failed: {e}", "percent": 0})
    threading.Thread(target=run_rollback, daemon=True).start()
    return {"status": "triggered", "version": version}


@router.post("/system/update/robot/rollback", summary="Rollback Robot + Arduino to a specific release", dependencies=[Depends(verify_token)])
async def trigger_robot_rollback(data: dict):
    version = data.get("version", "")
    if not version:
        raise HTTPException(status_code=400, detail="Version required")
    initial = {"status": "rollback_starting", "percent": 0, "version": version}
    save_json(ROBOT_UPDATE_FILE, initial)
    await manager.broadcast(json.dumps({"type": "robot_update_progress", **initial}), "app")
    await manager.broadcast(json.dumps({"type": "trigger_update", "version": version}), "robot")
    await manager.broadcast(json.dumps({"type": "trigger_arduino_flash", "version": version}), "robot")
    return {"status": "triggered", "version": version}
