"""WebSocket handler for app (browser dashboard) clients."""
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from time import time as _time
from config import (
    API_TOKEN, manager, stream_active, stream_v_slam,
    stream_keep_alive, active_camera_listeners,
    camera_idle_kill_at, rest_camera_listeners, preferred_ai_targets,
    robot_posture,
)
from routes.ws_helpers import (
    handle_camera_join,
    handle_camera_release,
    handle_camera_stop,
    handle_toggle_keep_stream,
    handle_camera_leave,
    handle_robot_posture_update,
    handle_demo_mode_toggle,
)

router = APIRouter()


@router.websocket("/ws/app")
async def websocket_app(websocket: WebSocket, token: Optional[str] = Query(None)):
    if token != API_TOKEN:
        await websocket.accept()
        await websocket.close(code=4003)
        return

    await manager.connect(websocket, "app")
    for cam_id in [1, 2]:
        is_active = stream_active[cam_id]
        await websocket.send_json({"type": "stream_status", "camera": cam_id, "active": is_active})
        await websocket.send_json({"type": "keep_stream_status", "camera": cam_id, "keep": stream_keep_alive[cam_id]})
        ws_count = len(active_camera_listeners[cam_id])
        rest_count = len(rest_camera_listeners[cam_id])
        kill_at = camera_idle_kill_at[cam_id]
        idle_kill_ms = int(max(0.0, kill_at - _time()) * 1000) if kill_at > 0 else 0
        await websocket.send_json({
            "type": "stream_state_sync",
            "camera": cam_id,
            "running": is_active,
            "viewers": ws_count + rest_count,
            "ws_viewers": ws_count,
            "rest_viewers": rest_count,
            "keep_alive": stream_keep_alive[cam_id],
            "v_slam": stream_v_slam[cam_id],
            "idle_kill_ms": idle_kill_ms,
        })
    # Send current AI state to newly connected dashboard
    await websocket.send_json({
        "type": "ai_state_update",
        "ai_state": dict(preferred_ai_targets)
    })
    # Send current robot posture so the UI is immediately in sync
    await websocket.send_json({
        "type": "robot_posture_sync",
        "robot_posture": dict(robot_posture),
    })
    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg_json = json.loads(data)
                msg_type = msg_json.get("type")
                if msg_type == "request_camera":
                    cam_id = msg_json.get("camera", 1)
                    v_slam = msg_json.get("v_slam", False)
                    await handle_camera_join(websocket, cam_id, v_slam, manager)
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    await handle_camera_release(websocket, cam_id, manager)
                elif msg_type == "stop_camera":
                    cam_id = msg_json.get("camera", 1)
                    await handle_camera_stop(cam_id, manager)
                    continue
                elif msg_type == "toggle_keep_stream":
                    cam_id = msg_json.get("camera", 1)
                    keep = msg_json.get("keep", False)
                    await handle_toggle_keep_stream(cam_id, keep, manager)
                elif msg_type == "join_stream":
                    cam_id = msg_json.get("camera", 1)
                    v_slam = msg_json.get("v_slam", False)
                    await handle_camera_join(websocket, cam_id, v_slam, manager)
                elif msg_type == "leave_stream":
                    cam_id = msg_json.get("camera", 1)
                    await handle_camera_leave(websocket, cam_id, manager)
                elif msg_type == "ai_control":
                    feature = msg_json.get("feature")
                    target = msg_json.get("target")
                    if feature in preferred_ai_targets:
                        preferred_ai_targets[feature] = target
                        node_connected = len(manager.active_connections.get("node", [])) > 0
                        active_target = target
                        if target == "node" and not node_connected:
                            # Node pas dispo → desactiver plutot que de tomber
                            # silencieusement sur "robot" sans prevenir l'UI
                            active_target = "disabled"
                        # Send effective target to robot/node
                        robot_msg = json.dumps({"type": "ai_control", "feature": feature, "target": active_target})
                        await manager.broadcast(robot_msg, "robot")
                        await manager.broadcast(robot_msg, "node")
                        # Build EFFECTIVE state so the UI reflects what is
                        # actually running, not the stored preference.
                        effective_state = {}
                        for f, t in preferred_ai_targets.items():
                            effective_state[f] = "disabled" if (t == "node" and not node_connected) else t
                        await manager.broadcast(json.dumps({
                            "type": "ai_state_update",
                            "ai_state": effective_state,
                        }), "app")
                    continue
                elif msg_type == "arduino_cmd":
                    await manager.broadcast(data, "robot")
                elif msg_type == "query_camera_resolutions":
                    await manager.broadcast(data, "robot")
                elif msg_type == "robot_posture_update":
                    key = msg_json.get("key")
                    value = msg_json.get("value")
                    if key:
                        await handle_robot_posture_update(key, value, manager)
                    continue
                elif msg_type == "demo_mode":
                    enabled = msg_json.get("enabled", False)
                    await handle_demo_mode_toggle(enabled, manager)
                    continue
            except json.JSONDecodeError:
                # Client sent malformed JSON — skip this message silently
                pass
            except AttributeError:
                # Client sent a JSON value that is not a dict (e.g. an array)
                pass

            await manager.broadcast(data, "robot")
            await manager.broadcast(data, "node")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "app")
