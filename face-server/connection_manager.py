"""WebSocket ConnectionManager for Bastet Gateway."""
import json
import asyncio
from typing import Optional

from fastapi import WebSocket


# Delai unifie (en secondes) avant arret auto quand plus personne ne regarde.
STOP_DELAY_SECONDS = 60.0


def total_consumers(cam_id: int) -> int:
    """Compteur unifie WS + REST pour la camera 'cam_id'."""
    from config import active_camera_listeners, rest_camera_listeners
    return len(active_camera_listeners[cam_id]) + len(rest_camera_listeners[cam_id])


def should_schedule_idle_kill(cam_id: int) -> bool:
    """Planifie une arret auto si zero consumer (WS+REST) ET keep_alive OFF."""
    from config import stream_keep_alive
    return total_consumers(cam_id) == 0 and not stream_keep_alive[cam_id]


async def stop_camera_delayed(cam_id: int, manager, delay_seconds: float = STOP_DELAY_SECONDS):
    """Arret differe : ne coupe le flux que si aucun consommateur (WS+REST) ET keep_alive OFF."""
    import time as _time
    from config import camera_idle_kill_at, stream_keep_alive, stream_active
    camera_idle_kill_at[cam_id] = _time.time() + delay_seconds
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        camera_idle_kill_at[cam_id] = 0.0
        raise
    camera_idle_kill_at[cam_id] = 0.0
    if stream_keep_alive[cam_id]:
        return
    if total_consumers(cam_id) > 0:
        return
    stream_active[cam_id] = False
    await manager.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
    await manager.broadcast(json.dumps({"type": "stream_status", "camera": cam_id, "active": False}), "app")


def cleanup_camera_listeners(websocket: WebSocket, manager):
    """Nettoie les listeners quand un websocket se deconnecte."""
    from time import time as _time
    from config import active_camera_listeners, camera_stop_timers
    for cam_id in [1, 2]:
        if websocket in active_camera_listeners[cam_id]:
            active_camera_listeners[cam_id].remove(websocket)
            if should_schedule_idle_kill(cam_id):
                if camera_stop_timers[cam_id] is not None:
                    camera_stop_timers[cam_id].cancel()
                camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, manager))


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {
            "robot": [], "node": [], "app": []
        }

    async def connect(self, websocket: WebSocket, client_type: str):
        await websocket.accept()
        if client_type in self.active_connections:
            self.active_connections[client_type].append(websocket)

    def disconnect(self, websocket: WebSocket, client_type: str):
        if client_type in self.active_connections and websocket in self.active_connections[client_type]:
            self.active_connections[client_type].remove(websocket)
        cleanup_camera_listeners(websocket, self)

    async def broadcast(self, message: str, target_client_type: str):
        for connection in self.active_connections.get(target_client_type, []):
            try:
                await connection.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()
