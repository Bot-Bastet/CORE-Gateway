"""WebSocket handler for node (PC) clients."""
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from config import API_TOKEN, manager, state
from routes.ws_helpers import (
    handle_node_connection_change,
    handle_camera_join,
    handle_camera_release,
    handle_camera_stop,
    handle_toggle_keep_stream,
    handle_camera_leave,
)

router = APIRouter()


async def _check_vslam_calibration(cam_id: int) -> dict | None:
    """Node-only V-SLAM calibration gate.

    Returns a ``vslam_blocked`` payload if the camera is not calibrated,
    or ``None`` if the stream can proceed.
    """
    diags = state.snapshot_diagnostics()  # fix: was .get_diagnostics() before refactor
    cal_status = diags.get("sensors", {}).get("calibration_status", {})
    cam_cal = cal_status.get(str(cam_id), cal_status.get(cam_id, {}))
    if cam_cal and not cam_cal.get("calibrated", False):
        return {
            "type": "vslam_blocked",
            "camera": cam_id,
            "reason": "Calibration requise avant V-SLAM.",
        }
    return None


@router.websocket("/ws/node")
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

            try:
                msg_json = json.loads(data)
                msg_type = msg_json.get("type")
                if msg_type == "request_camera":
                    cam_id = msg_json.get("camera", 1)
                    v_slam = msg_json.get("v_slam", False)
                    await handle_camera_join(
                        websocket, cam_id, v_slam, manager,
                        check_vslam_calibration=_check_vslam_calibration,
                    )
                    continue
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
                    await handle_camera_join(
                        websocket, cam_id, v_slam, manager,
                        check_vslam_calibration=_check_vslam_calibration,
                    )
                    continue
                elif msg_type == "leave_stream":
                    cam_id = msg_json.get("camera", 1)
                    await handle_camera_leave(websocket, cam_id, manager)
                elif msg_type == "camera_resolutions":
                    await manager.broadcast(data, "app")
                elif msg_type == "vslam_blocked":
                    await manager.broadcast(data, "app")
                elif msg_type == "chat_response":
                    await manager.broadcast(data, "app")
                    continue
                elif msg_type == "feature_request":
                    feature = msg_json.get("feature")
                    state_val = msg_json.get("state", False)
                    target = "node" if state_val else "robot"
                    from config import preferred_ai_targets
                    if feature in preferred_ai_targets:
                        preferred_ai_targets[feature] = target
                    ai_msg = json.dumps({
                        "type": "ai_control",
                        "feature": feature,
                        "target": target,
                    })
                    await manager.broadcast(ai_msg, "robot")
                    await manager.broadcast(json.dumps({
                        "type": "ai_state_update",
                        "ai_state": dict(preferred_ai_targets),
                    }), "app")
                    ack_msg = json.dumps({
                        "type": "feature_ack",
                        "feature": feature,
                        "state": state_val,
                        "status": "ok",
                    })
                    await websocket.send_text(ack_msg)
                    continue
            except json.JSONDecodeError:
                pass
            except AttributeError:
                pass

            await manager.broadcast(data, "robot")
            await manager.broadcast(data, "app")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "node")
        await handle_node_connection_change(False)
