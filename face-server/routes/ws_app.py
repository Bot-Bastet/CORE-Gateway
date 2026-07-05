"""WebSocket handler for app (browser dashboard) clients."""
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from time import time as _time
from config import (
    API_TOKEN, manager, stream_active, stream_v_slam,
    stream_keep_alive, active_camera_listeners, camera_stop_timers,
    camera_idle_kill_at, rest_camera_listeners,
    stop_camera_delayed, should_schedule_idle_kill, preferred_ai_targets,
)
from routes.ws_helpers import emit_stream_state_sync

router = APIRouter()


@router.websocket("/ws/app")
async def websocket_app(websocket: WebSocket, token: Optional[str] = Query(None)):
    if token != API_TOKEN:
        await websocket.accept()
        await websocket.close(code=4003)
        return

    await manager.connect(websocket, "app")
    for cam_id in [1, 2]:
        is_active = len(active_camera_listeners[cam_id]) > 0 or stream_active[cam_id]
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
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
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
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "leave_stream":
                    cam_id = msg_json.get("camera", 1)
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0 and not stream_keep_alive[cam_id]:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                    await emit_stream_state_sync(cam_id, manager)
                elif msg_type == "ai_control":
                    feature = msg_json.get("feature")
                    target = msg_json.get("target")
                    if feature in preferred_ai_targets:
                        preferred_ai_targets[feature] = target
                        node_connected = len(manager.active_connections.get("node", [])) > 0
                        active_target = target
                        if target == "node" and not node_connected:
                            active_target = "robot"
                        msg_json["target"] = active_target
                        data = json.dumps(msg_json)
                elif msg_type == "arduino_cmd":
                    await manager.broadcast(data, "robot")
                elif msg_type == "query_camera_resolutions":
                    await manager.broadcast(data, "robot")
            except Exception:
                pass

            await manager.broadcast(data, "robot")
            await manager.broadcast(data, "node")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "app")
