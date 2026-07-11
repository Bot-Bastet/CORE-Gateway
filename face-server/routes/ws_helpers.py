"""Shared WebSocket helpers for Bastet Gateway.

This module provides:
  - emit_stream_state_sync()      Broadcast unified stream state to /ws/app and /ws/node
  - handle_node_connection_change()  Auto-failover AI targets on node connect/disconnect
  - handle_camera_join()           Shared request_camera / join_stream logic (app + node)
  - handle_camera_release()        Shared release_camera logic
  - handle_camera_stop()           Shared stop_camera logic
  - handle_toggle_keep_stream()    Shared toggle_keep_stream logic
  - handle_camera_leave()          Shared leave_stream logic
"""
import asyncio
import json as _json

from time import time as _time
from config import (
    manager, stream_active, stream_v_slam,
    stream_keep_alive, active_camera_listeners, camera_idle_kill_at,
    camera_stop_timers, rest_camera_listeners, preferred_ai_targets,
    robot_posture,
    stop_camera_delayed, should_schedule_idle_kill,
    save_json, ROBOT_POSTURE_FILE,
)


async def emit_stream_state_sync(cam_id: int, manager):
    """Emet l'etat partage du flux camera vers /ws/app et /ws/node.

    Payload :
        type                "stream_state_sync"
        camera              1 | 2
        running             bool   - flux cote robot actif ?
        viewers             int    - compteurs unifies (WS + REST)
        ws_viewers          int    - nombre de WebSocket consommateurs
        rest_viewers        int    - nombre de clients REST (mobile APP, scripts)
        keep_alive          bool   - mode economie d'energie desactive
        v_slam              bool   - mode V-SLAM active sur ce flux
        idle_kill_ms        int    - ms avant fermeture timer auto (0 si pas planifie)
    """
    ws_count = len(active_camera_listeners[cam_id])
    rest_count = len(rest_camera_listeners[cam_id])
    kill_at = camera_idle_kill_at[cam_id]
    idle_kill_ms = int(max(0.0, kill_at - _time()) * 1000) if kill_at > 0 else 0
    payload = {
        "type": "stream_state_sync",
        "camera": cam_id,
        "running": stream_active[cam_id],
        "viewers": ws_count + rest_count,
        "ws_viewers": ws_count,
        "rest_viewers": rest_count,
        "keep_alive": stream_keep_alive[cam_id],
        "v_slam": stream_v_slam[cam_id],
        "idle_kill_ms": idle_kill_ms,
    }
    encoded = _json.dumps(payload)
    await manager.broadcast(encoded, "app")
    await manager.broadcast(encoded, "node")


async def handle_node_connection_change(connected: bool):
    """Bascule automatiquement les services quand le PC Node se connecte/deconnecte.

    - Node connecte  → toutes les features passent a "node"
    - Node deconnecte → toutes les features actuellement sur "node" repassent a "disabled"
    - Envoie ai_state_update a tous les dashboards pour que l'UI reflete l'etat reel.
    """
    for feature in list(preferred_ai_targets.keys()):
        if connected:
            # Prise en charge totale : tout bascule sur le PC Node
            preferred_ai_targets[feature] = "node"
            active_target = "node"
        else:
            # Deconnexion : ne touche que les features qui etaient sur le node
            if preferred_ai_targets[feature] != "node":
                continue
            preferred_ai_targets[feature] = "disabled"
            active_target = "disabled"

        controlled_msg = _json.dumps({
            "type": "ai_control",
            "feature": feature,
            "target": active_target,
        })
        await manager.broadcast(controlled_msg, "robot")
        await manager.broadcast(controlled_msg, "node")

    # Synchro UI : tous les dashboards recoivent l'etat reel
    await manager.broadcast(_json.dumps({
        "type": "ai_state_update",
        "ai_state": dict(preferred_ai_targets),
    }), "app")


# ─── Shared Camera Stream Handlers ─────────────────────────────────────────
#
# These functions encapsulate the common WebSocket message-handling logic
# that was previously duplicated verbatim (or near-verbatim) in both
# ws_app.py and ws_node.py.
#
# The ONLY behavioural difference between app and node is the V-SLAM
# calibration check: the node verifies that a camera is calibrated before
# allowing a V-SLAM stream.  This is injected via the optional
# ``check_vslam_calibration`` async callable (None = no check, i.e. app).
# ───────────────────────────────────────────────────────────────────────────


async def handle_camera_join(
    websocket,
    cam_id: int,
    v_slam: bool,
    manager,
    *,
    check_vslam_calibration=None,
):
    """Shared handler for ``request_camera`` / ``join_stream`` messages.

    Parameters
    ----------
    check_vslam_calibration : callable | None
        ``async def(cam_id) -> dict | None``
        If provided and *v_slam* is True, called BEFORE starting the stream.
        Must return a ``vslam_blocked`` payload dict if the stream should be
        blocked, or ``None`` to proceed.
        Used only by the node WebSocket handler.
    """
    active_camera_listeners[cam_id].add(websocket)
    if camera_stop_timers[cam_id] is not None:
        camera_stop_timers[cam_id].cancel()
        camera_stop_timers[cam_id] = None
        camera_idle_kill_at[cam_id] = 0.0

    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
    stream_v_slam[cam_id] = v_slam

    if not stream_active[cam_id] or v_slam_changed:
        # V-SLAM calibration gate (node-only path)
        if v_slam and check_vslam_calibration is not None:
            blocked = await check_vslam_calibration(cam_id)
            if blocked is not None:
                await websocket.send_json(blocked)
                stream_active[cam_id] = False
                await emit_stream_state_sync(cam_id, manager)
                return

        stream_active[cam_id] = True
        await manager.broadcast(
            _json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}),
            "robot",
        )
        await manager.broadcast(
            _json.dumps({"type": "stream_status", "camera": cam_id, "active": True}),
            "app",
        )
    else:
        # Stream deja actif : re-envoyer start_camera au robot (idempotent)
        await manager.broadcast(
            _json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}),
            "robot",
        )

    await emit_stream_state_sync(cam_id, manager)


async def handle_camera_release(websocket, cam_id: int, manager):
    """Shared handler for ``release_camera`` messages."""
    if not stream_active[cam_id]:
        return
    if websocket in active_camera_listeners[cam_id]:
        active_camera_listeners[cam_id].remove(websocket)
        if should_schedule_idle_kill(cam_id):
            if camera_stop_timers[cam_id] is not None:
                camera_stop_timers[cam_id].cancel()
                camera_idle_kill_at[cam_id] = 0.0
            camera_stop_timers[cam_id] = asyncio.create_task(
                stop_camera_delayed(cam_id, manager)
            )
    await emit_stream_state_sync(cam_id, manager)


async def handle_camera_stop(cam_id: int, manager):
    """Shared handler for ``stop_camera`` messages."""
    if not stream_active[cam_id]:
        return
    if camera_stop_timers[cam_id] is not None:
        camera_stop_timers[cam_id].cancel()
        camera_stop_timers[cam_id] = None
        camera_idle_kill_at[cam_id] = 0.0
    stream_active[cam_id] = False
    await manager.broadcast(
        _json.dumps({"type": "stop_camera", "camera": cam_id}), "robot"
    )
    await manager.broadcast(
        _json.dumps({"type": "stream_status", "camera": cam_id, "active": False}),
        "app",
    )
    await emit_stream_state_sync(cam_id, manager)


async def handle_toggle_keep_stream(cam_id: int, keep: bool, manager):
    """Shared handler for ``toggle_keep_stream`` messages."""
    stream_keep_alive[cam_id] = keep
    if keep:
        stream_active[cam_id] = True
        if camera_stop_timers[cam_id] is not None:
            camera_stop_timers[cam_id].cancel()
            camera_stop_timers[cam_id] = None
            camera_idle_kill_at[cam_id] = 0.0
        await manager.broadcast(
            _json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]}),
            "robot",
        )
        await manager.broadcast(
            _json.dumps({"type": "stream_status", "camera": cam_id, "active": True}),
            "app",
        )
    else:
        if should_schedule_idle_kill(cam_id):
            if camera_stop_timers[cam_id] is not None:
                camera_stop_timers[cam_id].cancel()
                camera_idle_kill_at[cam_id] = 0.0
            camera_stop_timers[cam_id] = asyncio.create_task(
                stop_camera_delayed(cam_id, manager)
            )
    await manager.broadcast(
        _json.dumps({"type": "keep_stream_status", "camera": cam_id, "keep": keep}),
        "app",
    )
    await emit_stream_state_sync(cam_id, manager)


async def handle_camera_leave(websocket, cam_id: int, manager):
    """Shared handler for ``leave_stream`` messages."""
    if websocket in active_camera_listeners[cam_id]:
        active_camera_listeners[cam_id].remove(websocket)
        if should_schedule_idle_kill(cam_id):
            if camera_stop_timers[cam_id] is not None:
                camera_stop_timers[cam_id].cancel()
                camera_idle_kill_at[cam_id] = 0.0
            camera_stop_timers[cam_id] = asyncio.create_task(
                stop_camera_delayed(cam_id, manager)
            )
    await emit_stream_state_sync(cam_id, manager)


# ─── Robot Posture & Demo Mode ────────────────────────────────────────────


async def handle_robot_posture_update(key: str, value, manager):
    """Met a jour une cle de posture robot et la broadcast a tous les clients.

    Stocke dans ``robot_posture`` (GatewayState → synchro multi-client).
    Broadcast le nouvel etat complet a tous les dashboards (app) + mobile.
    """
    if key not in robot_posture:
        return

    # Clamp values to valid ranges
    if key == "height":
        value = max(60.0, min(140.0, float(value)))
    elif key == "speed":
        value = max(3.0, min(30.0, float(value)))
    elif key in ("roll", "pitch", "yaw"):
        value = max(-15.0, min(15.0, float(value)))
    elif key == "demo_mode":
        value = bool(value)
    elif key == "powered":
        value = bool(value)

    robot_posture[key] = value

    # Persist to disk so posture survives gateway restarts
    save_json(ROBOT_POSTURE_FILE, dict(robot_posture))

    # Broadcast to ALL app clients (dashboard + mobile) so UI stays in sync
    await manager.broadcast(_json.dumps({
        "type": "robot_posture_sync",
        "robot_posture": dict(robot_posture),
    }), "app")

    # Forward to robot channel when NOT in demo mode (real robot needs gait params)
    if not robot_posture.get("demo_mode", False):
        await manager.broadcast(_json.dumps({
            "type": "robot_posture",
            "key": key,
            "value": value,
        }), "robot")

    # If user toggled demo mode OFF, tell robot to sit (safe position)
    if key == "demo_mode" and not value:
        await manager.broadcast(_json.dumps({
            "type": "arduino_cmd", "cmd": "sit"
        }), "robot")

    # If user toggled powered OFF, tell robot to stop (detach servos)
    if key == "powered" and not value:
        await manager.broadcast(_json.dumps({
            "type": "arduino_cmd", "cmd": "stop"
        }), "robot")
        # Also send cmd_vel zero to stop any walking
        await manager.broadcast(_json.dumps({
            "type": "cmd_vel", "linear": 0.0, "angular": 0.0
        }), "robot")
    elif key == "powered" and value:
        # Powered on: sit is safe default
        await manager.broadcast(_json.dumps({
            "type": "arduino_cmd", "cmd": "sit"
        }), "robot")


async def handle_demo_mode_toggle(enabled: bool, manager):
    """Active/desactive le mode demo.

    Demo ON  → les moteurs sont eteints (simulation uniquement).
    Demo OFF → les moteurs s'allument et le robot s'assoit.
    """
    robot_posture["demo_mode"] = enabled

    # Persist to disk
    save_json(ROBOT_POSTURE_FILE, dict(robot_posture))

    if enabled:
        # Disable motors for simulation (robot expects "stop")
        await manager.broadcast(_json.dumps({
            "type": "arduino_cmd", "cmd": "stop"
        }), "robot")
    else:
        # Re-enable motors and sit
        await manager.broadcast(_json.dumps({
            "type": "arduino_cmd", "cmd": "sit"
        }), "robot")

    # Sync all dashboards
    await manager.broadcast(_json.dumps({
        "type": "robot_posture_sync",
        "robot_posture": dict(robot_posture),
    }), "app")
