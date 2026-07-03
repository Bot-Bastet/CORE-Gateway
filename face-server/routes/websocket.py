"""WebSocket handlers for robot, node, and app clients."""
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from config import (
    API_TOKEN, manager, MYGES_FILE, stream_active, stream_v_slam,
    stream_keep_alive, active_camera_listeners, camera_stop_timers,
    preferred_ai_targets, latest_diagnostics, load_json, stop_camera_delayed,
)
from myges_api import MyGesAPI

router = APIRouter()


async def handle_node_connection_change(connected: bool):
    """Bascule automatiquement les services si le PC Node se déconnecte/reconnecte."""
    for feature, target in preferred_ai_targets.items():
        if target == "node":
            active_target = "node" if connected else "robot"
            controlled_msg = json.dumps({
                "type": "ai_control",
                "feature": feature,
                "target": active_target
            })
            await manager.broadcast(controlled_msg, "robot")
            await manager.broadcast(controlled_msg, "app")


@router.websocket("/ws/robot")
async def websocket_robot(websocket: WebSocket, token: Optional[str] = Query(None)):
    if token != API_TOKEN:
        await websocket.accept()
        await websocket.close(code=4003)
        return

    await manager.connect(websocket, "robot")
    for cam_id in [1, 2]:
        if stream_active[cam_id]:
            try:
                await websocket.send_json({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]})
            except Exception:
                pass
    try:
        while True:
            data = await websocket.receive_text()

            # Injection contextuelle (Emploi du temps MyGES)
            try:
                msg_json = json.loads(data)
                if msg_json.get("type") == "chat":
                    comptes = load_json(MYGES_FILE, default={})
                    if comptes:
                        user_name = list(comptes.keys())[0]
                        creds = comptes[user_name]
                        api = MyGesAPI(creds["username"], creds["password"])
                        agenda_text = api.get_upcoming_agenda_text(days=7)
                        msg_json["context"] = f"[CONTEXTE CACHÉ - Agenda de {user_name} pour les 7 prochains jours] : \n{agenda_text}"
                        data = json.dumps(msg_json)
            except Exception as e:
                print(f"Erreur injection contexte : {e}")

            try:
                msg_json = json.loads(data)
                if msg_json.get("type") == "telemetry_diagnostics":
                    latest_diagnostics.clear()
                    latest_diagnostics.update(msg_json)
            except Exception:
                pass

            await manager.broadcast(data, "node")
            await manager.broadcast(data, "app")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "robot")


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
                    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
                    stream_v_slam[cam_id] = v_slam
                    if not stream_active[cam_id] or v_slam_changed:
                        stream_active[cam_id] = True
                        # V-SLAM gatekeeping: check calibration status before sending to robot
                        if v_slam:
                            cal_status = latest_diagnostics.get("sensors", {}).get("calibration_status", {})
                            cam_cal = cal_status.get(str(cam_id), cal_status.get(cam_id, {}))
                            if cam_cal and not cam_cal.get("calibrated", False):
                                await websocket.send_json({
                                    "type": "vslam_blocked",
                                    "camera": cam_id,
                                    "reason": "Calibration requise avant V-SLAM."
                                })
                                stream_active[cam_id] = False
                            else:
                                await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                                await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        else:
                            await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                            await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    # Idempotent : si deja arrete explicitement par stop_camera, no-op
                    # (evite aussi un delayed-stop redondant quand toggleStream envoie stop_camera + release_camera).
                    if not stream_active[cam_id]:
                        continue
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                elif msg_type == "stop_camera":
                    # FIX: Bouton "Couper Camera" doit couper IMMEDIATEMENT cote robot.
                    # Idempotent : stream_active[cam_id] False = deja arrete, no-op
                    # (le robot re-diffuse via catch-all -> echoes casses par ce garde).
                    # stream_keep_alive ignore ici : fermeture explicite prime.
                    cam_id = msg_json.get("camera", 1)
                    if not stream_active[cam_id]:
                        continue
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                    stream_active[cam_id] = False
                    await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
                    await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": False}), "app")
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
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    else:
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            from config import stop_camera_delayed
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                    await manager.broadcast(json.dumps({"type": "keep_stream_status", "camera": cam_id, "keep": keep}), "app")
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
                    v_slam_changed = (stream_v_slam[cam_id] != v_slam)
                    stream_v_slam[cam_id] = v_slam
                    if not stream_active[cam_id] or v_slam_changed:
                        stream_active[cam_id] = True
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": v_slam}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                elif msg_type == "release_camera":
                    cam_id = msg_json.get("camera", 1)
                    # Idempotent : si deja arrete explicitement par stop_camera, no-op
                    # (evite aussi un delayed-stop redondant quand toggleStream envoie stop_camera + release_camera).
                    if not stream_active[cam_id]:
                        continue
                    if websocket in active_camera_listeners[cam_id]:
                        active_camera_listeners[cam_id].remove(websocket)
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                elif msg_type == "stop_camera":
                    # FIX: Bouton "Couper Camera" doit couper IMMEDIATEMENT cote robot.
                    # Idempotent : stream_active[cam_id] False = deja arrete, no-op
                    # (le robot re-diffuse via catch-all -> echoes casses par ce garde).
                    # stream_keep_alive ignore ici : fermeture explicite prime.
                    cam_id = msg_json.get("camera", 1)
                    if not stream_active[cam_id]:
                        continue
                    if camera_stop_timers[cam_id] is not None:
                        camera_stop_timers[cam_id].cancel()
                        camera_stop_timers[cam_id] = None
                    stream_active[cam_id] = False
                    await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
                    await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": False}), "app")
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
                        await manager.broadcast(json.dumps({"type": "start_camera", "camera": cam_id, "v_slam": stream_v_slam[cam_id]}), "robot")
                        await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": True}), "app")
                    else:
                        if len(active_camera_listeners[cam_id]) == 0:
                            if camera_stop_timers[cam_id] is not None:
                                camera_stop_timers[cam_id].cancel()
                            from config import stop_camera_delayed
                            camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))
                    await manager.broadcast(json.dumps({"type": "keep_stream_status", "camera": cam_id, "keep": keep}), "app")
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
