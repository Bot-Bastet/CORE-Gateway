"""WebSocket handler for robot clients."""
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from config import (
    API_TOKEN, manager, MYGES_FILE, stream_active, stream_v_slam,
    latest_diagnostics, load_json, normalize_camera_manifest,
    state,
)
from myges_api import MyGesAPI
from routes.ws_helpers import emit_stream_state_sync

router = APIRouter()


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
                    if isinstance(msg_json.get("sensors"), dict):
                        normalize_camera_manifest(msg_json["sensors"])
                    await state.set_diagnostics(msg_json)
                    data = json.dumps(msg_json, ensure_ascii=False)
            except Exception:
                pass

            await manager.broadcast(data, "node")
            await manager.broadcast(data, "app")
    except WebSocketDisconnect:
        manager.disconnect(websocket, "robot")
