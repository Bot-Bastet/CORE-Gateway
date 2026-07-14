"""Bastet Gateway - Camera Streams API
====================================

REST surface for the shared on-demand camera-stream lifecycle. State is held
in ``config.py`` (unified WS + REST listeners, ``stream_active``,
``camera_idle_kill_at``, ``stop_camera_delayed``). This router only EXPOSES
that state to REST callers (mobile app, scripts, dashboards from other
browsers) and is responsible for emitting ``stream_state_sync`` so /ws/app
subscribers stay in sync with REST-side mutations.

Endpoints (all under /api, all require X-API-Token):

  GET    /api/cameras                      Manifest of currently-plugged cameras
  GET    /api/streams                      All running streams + viewer counts + idle timers
  GET    /api/streams/{cam}                Per-camera stream status
  POST   /api/streams/{cam}/join           Idempotent: bumps REST viewer count, starts stream if needed
  DELETE /api/streams/{cam}/leave          Removes caller from REST viewers; unified idle kill if last
  POST   /api/streams/{cam}/stop           Hard-stop (anti-griefing: 409 if others watch)

Anti-griefing contract (now unified in config.py):
  - WS subscribers are tracked in ``active_camera_listeners``
  - REST subscribers are tracked in ``rest_camera_listeners``
  - The stream stays alive while WS ∪ REST is non-empty AND keep_alive=False
  - When both are empty, ``stop_camera_delayed`` schedules a 60s idle kill
    (cancellable on a fresh join).
"""
import asyncio
import time
from time import time as _time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel

from auth import verify_token
from models import MonoCalibRequest, StereoCalibRequest
from config import (
    stream_active, stream_v_slam, stream_keep_alive,
    active_camera_listeners, rest_camera_listeners,
    camera_stop_timers, camera_idle_kill_at,
    latest_diagnostics, manager, stop_camera_delayed,
    total_consumers, should_schedule_idle_kill,
)
from connection_manager import touch_rest_viewer, drop_rest_viewer, REST_VIEWER_TTL_SECONDS
from routes.ws_helpers import emit_stream_state_sync  # WS sync broadcast helper
import json

router = APIRouter(prefix="/api", tags=["Streams"])


# ─── Schema ────────────────────────────────────────────────────────────────

class JoinBody(BaseModel):
    client_id: Optional[str] = None  # opaque caller identifier; auto-generated if absent

class LeaveBody(BaseModel):
    client_id: Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────────────────────

def _camera_manifest() -> dict:
    """Manifest unifie des cameras branchees.

    SEULE source de verite : latest_diagnostics["sensors"] (deja normalisee
    par normalize_camera_manifest() au moment de la reception WS robot ET
    au moment du POST /core/state).

    On a supprime la logique positionnelle dupliquee qui etait ici —
    elle etait une copie de normalize_camera_manifest() dans config.py
    avec une semantique legerement differente, ce qui produisait 3 sources
    de verite differentes (agent.py, config.py, streams.py).

    Maintenant : on lit juste les booleans normalises.
    """
    sensors = latest_diagnostics.get("sensors", {}) if latest_diagnostics else {}
    devices = sensors.get("available_video_devices")
    if not isinstance(devices, list):
        devices = []

    def _dev(side: int) -> Optional[str]:
        if len(devices) >= side:
            return devices[side - 1]
        # Mapping-aware fallback: si le user a assigne un device specifique
        mapping = sensors.get("camera_mapping")
        side_key = "left" if side == 1 else "right"
        if isinstance(mapping, dict):
            val = mapping.get(side_key)
            if isinstance(val, dict):
                dev = val.get("device")
                if dev and dev in devices:
                    return dev
            elif isinstance(val, str) and val in devices:
                return val
        return None

    def _cal(side: int) -> bool:
        cs = sensors.get("calibration_status", {}) or {}
        return bool((cs.get(str(side)) or cs.get(side) or {}).get("calibrated", False))

    return {
        1: {
            "connected": bool(sensors.get("cam1_connected", False)),
            "device": _dev(1),
            "calibrated": _cal(1),
        },
        2: {
            "connected": bool(sensors.get("cam2_connected", False)),
            "device": _dev(2),
            "calibrated": _cal(2),
        },
    }


def _stream_state(cam_id: int) -> dict:
    """Project the unified gateway state into a JSON-friendly payload."""
    ws_count = len(active_camera_listeners.get(cam_id, set()))
    rest_ids = list(rest_camera_listeners.get(cam_id, set()))
    rest_count = len(rest_ids)
    running = bool(stream_active.get(cam_id, False))
    kill_at = camera_idle_kill_at.get(cam_id, 0.0) or 0.0
    idle_kill_ms = int(max(0.0, kill_at - _time()) * 1000) if kill_at > 0 else 0
    return {
        "running": running,
        "viewers": ws_count + rest_count,
        "rest_viewers": rest_ids,
        "browser_viewers": ws_count,
        "ws_viewers": ws_count,
        "idle_kill_ms": idle_kill_ms,
        "pending": False,
        "v_slam": bool(stream_v_slam.get(cam_id, False)),
        "keep_alive": bool(stream_keep_alive.get(cam_id, False)),
    }


async def _maybe_start_robot(cam_id: int):
    """Send start_camera to robot + stream_status to /ws/app if the stream wasn't already active."""
    if stream_active.get(cam_id, False):
        return
    stream_active[cam_id] = True
    try:
        await manager.broadcast(
            json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": False}),
            "robot",
        )
        await manager.broadcast(
            json.dumps({"type": "stream_status", "camera": cam_id, "active": True}),
            "app",
        )
    except Exception as e:
        stream_active[cam_id] = False
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")


async def _maybe_schedule_idle_kill(cam_id: int):
    """If we're the last consumer (WS ∪ REST = 0) AND keep_alive is off, schedule the 60s kill."""
    if not should_schedule_idle_kill(cam_id):
        return
    if camera_stop_timers[cam_id] is not None:
        camera_stop_timers[cam_id].cancel()
        camera_idle_kill_at[cam_id] = 0.0
    camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))


# ─── Routes ────────────────────────────────────────────────────────────────

@router.get("/cameras")
def get_cameras(_: str = Depends(verify_token)):
    return {"cameras": _camera_manifest()}


@router.get("/streams")
def get_streams(_: str = Depends(verify_token)):
    return {"streams": {1: _stream_state(1), 2: _stream_state(2)}}


@router.get("/streams/{cam}")
def get_stream(cam: int, _: str = Depends(verify_token)):
    if cam not in (1, 2):
        raise HTTPException(status_code=404, detail="Caméra inconnue. IDs valides : 1, 2.")
    return _stream_state(cam)


@router.post("/streams/{cam}/join")
async def join_stream(
    cam: int,
    body: JoinBody = Body(default_factory=JoinBody),
    _: str = Depends(verify_token),
):
    """Idempotent: add this REST caller as a viewer. If the first viewer, send start_camera to robot.

    Anti-griefing: cancel any in-flight idle-kill timer. Sets running=True if first viewer.

    HEARTBEAT: les clients REST (app mobile) doivent re-POST ce join au moins
    toutes les REST_VIEWER_TTL_SECONDS (75) secondes tant qu'ils regardent le
    flux. Sans heartbeat, le viewer est purgé et le flux peut se couper 60 s
    plus tard s'il était le dernier.
    """
    if cam not in (1, 2):
        raise HTTPException(status_code=404, detail="Caméra inconnue. IDs valides : 1, 2.")
    if not _camera_manifest()[cam]["connected"]:
        raise HTTPException(
            status_code=409,
            detail=f"Camera {cam} n'est pas branchée physiquement (le robot ne la voit pas).",
        )

    client_id = body.client_id or f"rest-{int(time.time()*1000)}"
    was_present = client_id in rest_camera_listeners[cam]
    rest_camera_listeners[cam].add(client_id)
    touch_rest_viewer(cam, client_id)  # heartbeat anti-viewer-fantôme

    # Cancel any in-flight idle-kill timer (someone's rejoining)
    if camera_stop_timers[cam] is not None:
        camera_stop_timers[cam].cancel()
        camera_idle_kill_at[cam] = 0.0
        camera_stop_timers[cam] = None

    if not stream_active.get(cam, False):
        await _maybe_start_robot(cam)

    # Broadcast the new state so any /ws/app dashboards stay in sync.
    await emit_stream_state_sync(cam, manager)

    status = "already" if was_present else ("starting" if _stream_state(cam)["viewers"] == 1 else "joined")
    return {"status": status, "client_id": client_id, **_stream_state(cam)}


@router.delete("/streams/{cam}/leave")
async def leave_stream(
    cam: int,
    body: LeaveBody = Body(default_factory=LeaveBody),
    _: str = Depends(verify_token),
):
    """Decrement a REST viewer. If the unified consumer set drops to 0, schedule the 60s idle kill.

    Note: mobile apps MUST send a stable ``client_id`` (UUID per install) to leave cleanly.
    Two joins without explicit client_id generate different auto-IDs and can only be
    removed one at a time.
    """
    if cam not in (1, 2):
        raise HTTPException(status_code=404, detail="Caméra inconnue. IDs valides : 1, 2.")
    if body.client_id is None:
        raise HTTPException(status_code=400, detail="client_id requis pour le leave REST.")
    rest_camera_listeners[cam].discard(body.client_id)
    drop_rest_viewer(cam, body.client_id)

    cooldown_started = False
    if should_schedule_idle_kill(cam):
        await _maybe_schedule_idle_kill(cam)
        cooldown_started = True

    await emit_stream_state_sync(cam, manager)

    return {"status": "left", "client_id": body.client_id,
            "cooldown_starts": cooldown_started, **_stream_state(cam)}


@router.post("/streams/{cam}/stop")
async def force_stop(cam: int, _: str = Depends(verify_token)):
    """Anti-griefing hard-stop: 409 if anyone else (REST or browser) is still watching.

    Only succeeds when total_consumers(cam) <= 1 (i.e. the caller is the only viewer).
    """
    if cam not in (1, 2):
        raise HTTPException(status_code=404, detail="Caméra inconnue. IDs valides : 1, 2.")
    if total_consumers(cam) > 1:
        rest_count = len(rest_camera_listeners.get(cam, set()))
        browser_count = len(active_camera_listeners.get(cam, set()))
        raise HTTPException(
            status_code=409,
            detail=(f"Impossible d'arrêter : {rest_count + browser_count} viewers regardent encore "
                    f"(REST: {rest_count}, navigateur: {browser_count}). Anti-griefing garanti."),
        )
    if camera_stop_timers[cam] is not None:
        camera_stop_timers[cam].cancel()
        camera_idle_kill_at[cam] = 0.0
        camera_stop_timers[cam] = None
    rest_camera_listeners[cam].clear()
    stream_active[cam] = False
    try:
        await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam}), "robot")
        await manager.broadcast(
            json.dumps({"type": "stream_status", "camera": cam, "active": False}),
            "app",
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    await emit_stream_state_sync(cam, manager)
    return {"status": "stopped", **_stream_state(cam)}


# ─── Debug endpoint (TEMP : retire après debug caméra) ────────────────────

@router.get("/debug/state")
def debug_state(_: str = Depends(verify_token)):
    """Dump l'état in-memory pour debug : latest_diagnostics brut + compteurs.

    Cet endpoint sert a comprendre ce que la gateway voit reellement apres
    reception de la derniere telemetry_diagnostics du robot. A retirer des
    que le bug du manifest est confirmé résolu.
    """
    from time import time as _time
    sensors = latest_diagnostics.get("sensors", {}) if latest_diagnostics else {}
    devices = sensors.get("available_video_devices")
    return {
        "latest_diagnostics_keys": list(latest_diagnostics.keys()) if latest_diagnostics else [],
        "sensors": sensors,
        "available_video_devices_raw": devices,
        "available_video_devices_type": type(devices).__name__ if devices is not None else "None",
        "available_video_devices_len": len(devices) if isinstance(devices, list) else None,
        "stream_running": dict(stream_active),
        "ws_listeners_count": {1: len(active_camera_listeners.get(1, set())),
                                2: len(active_camera_listeners.get(2, set()))},
        "rest_listeners_count": {1: len(rest_camera_listeners.get(1, set())),
                                  2: len(rest_camera_listeners.get(2, set()))},
        "kill_at": dict(camera_idle_kill_at),
        "now": _time(),
    }


# ─── Camera Calibration Endpoints ──────────────────────────────────────────

@router.post("/calibration/camera/run/mono")
async def run_mono_calibration_rest(req: MonoCalibRequest, _: str = Depends(verify_token)):
    """Lancer la calibration mono via REST."""
    payload = {
        "type": "run_mono_calib",
        "camera": req.camera,
        "chessboard_cols": req.chessboard_cols,
        "chessboard_rows": req.chessboard_rows,
        "square_size_mm": req.square_size_mm,
        "timeout_seconds": 300
    }
    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Commande run_mono_calib transmise au robot."}


@router.post("/calibration/camera/run/stereo")
async def run_stereo_calibration_rest(req: StereoCalibRequest, _: str = Depends(verify_token)):
    """Lancer la calibration stéréo via REST."""
    payload = {
        "type": "run_stereo_calib",
        "chessboard_cols": req.chessboard_cols,
        "chessboard_rows": req.chessboard_rows,
        "square_size_mm": req.square_size_mm,
        "timeout_seconds": 300
    }
    try:
        await manager.broadcast(json.dumps(payload), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Commande run_stereo_calib transmise au robot."}


@router.post("/calibration/camera/abort")
async def abort_calibration_rest(_: str = Depends(verify_token)):
    """Arrêter ou annuler la calibration en cours."""
    try:
        await manager.broadcast(json.dumps({"type": "stop_camera", "camera": 1}), "robot")
        await manager.broadcast(json.dumps({"type": "stop_camera", "camera": 2}), "robot")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Robot WS indisponible: {e}")
    return {"status": "ok", "message": "Commande d'annulation calibration transmise au robot."}

