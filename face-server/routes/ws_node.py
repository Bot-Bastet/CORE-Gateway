"""WebSocket handler for node (PC) clients."""
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from config import (
    API_TOKEN, manager, stream_active, stream_v_slam,
    stream_keep_alive, active_camera_listeners, camera_stop_timers,
    camera_idle_kill_at, stop_camera_delayed, should_schedule_idle_kill,
    state,
)
from routes.ws_helpers import emit_stream_state_sync, handle_node_connection_change

router = APIRouter()


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
                    active_camera_listeners[cam_id].add(websocket)
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                        camera_idle_kill_at[cam_id] = 0.0
                    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
                    stream_v_slam[cam_id] = v_slam
                    if not stream_active[cam_id] or v_slam_changed:
                        stream_active[cam_id] = True
                        if v_slam:
                            diags = await state.get_diagnostics()
                            cal_status = diags.get("sensors", {}).get("calibration_status", {})
                            cam_cal = cal_status.get(str(cam_id), cal_status.get(cam_id, {}))
                            if cam_cal and not cam_cal.get("calibrated", False):
                                await websocket.send_json({
                                    "type": "vslam_blocked",
                                    "camera": cam_id,
                                    "reason": "Calibration requise avant V-SLAM."
                                })
                                stream_active[cam_id] = False
                                await emit_stream_state_sync(cam_id, manager)
                                continue
                            else:
                                await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                                await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        else:
                            await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                            await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    if not stream_active[cam_id]:
                        continue
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if should_schedule_idle_kill(cam_id):
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                                camera_idle_kill_at[cam_id] = 0.0
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                    await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "stop_camera":
                    cam_id = msg_json.get("camera", 1)
                    if not stream_active[cam_id]:
                        continue
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                        camera_idle_kill_at[cam_id] = 0.0
                    stream_active[cam_id] = False
                    await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
                    await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": False}), "app")
                    await emit_stream_state_sync(cam_id, manager)
                    continue
                elif msg_type == "toggle_keep_stream":
                    cam_id = msg_json.get("camera", 1)
                    keep = msg_json.get("keep", False)
                    stream_keep_alive[cam_id] = keep
                    if keep:
                        stream_active[cam_id] = True
                        if camera_stop_timers[cam_id] is not None:
                            camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = None
                            camera_idle_kill_at[cam_id] = 0.0
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    else:
                        if should_schedule_idle_kill(cam_id):
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                                camera_idle_kill_at[cam_id] = 0.0
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                    await manager.broadcast(json.dumps({"type": "keep_stream_status", "camera": cam_id, "keep": keep}), "app")
                    await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "join_stream":
                    cam_id = msg_json.get("camera", 1)
                    v_slam = msg_json.get("v_slam", False)
                    active_camera_listeners[cam_id].add(websocket)
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                        camera_idle_kill_at[cam_id] = 0.0
                    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
                    stream_v_slam[cam_id] = v_slam
                    if not stream_active[cam_id] or v_slam_changed:
                        stream_active[cam_id] = True
                        if v_slam:
                            diags = await state.get_diagnostics()
                            cal_status = diags.get("sensors", {}).get("calibration_status", {})
                            cam_cal = cal_status.get(str(cam_id), cal_status.get(cam_id, {}))
                            if cam_cal and not cam_cal.get("calibrated", False):
                                await websocket.send_json({
                                    "type": "vslam_blocked",
                                    "camera": cam_id,
                                    "reason": "Calibration requise avant V-SLAM."
                                })
                                stream_active[cam_id] = False
                                await emit_stream_state_sync(cam_id, manager)
                                continue
                            else:
                                await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                                await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        else:
                            await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                            await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "leave_stream":
                    cam_id = msg_json.get("camera", 1)
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if should_schedule_idle_kill(cam_id):
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                                camera_idle_kill_at[cam_id] = 0.0
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                    await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "camera_resolutions":
                    await manager.broadcast(data, "app")
                elif msg_type == "vslam_blocked":
                    await manager.broadcast(data, "app")
            except Exception:
                pass

            await manager.broadcast(data, "robot")
            await manager.broadcast(data, "app")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "node")
        await handle_node_connection_change(False)
