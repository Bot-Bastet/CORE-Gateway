"""Shared WebSocket helpers for Bastet Gateway."""
import json

from time import time as _time
from config import (
    manager, stream_active, stream_v_slam,
    stream_keep_alive, active_camera_listeners, camera_idle_kill_at,
    rest_camera_listeners, preferred_ai_targets,
)


async def emit_stream_state_sync(cam_id: int, manager):
    """Émet l'état partagé du flux caméra vers /ws/app et /ws/node.

    Payload :
        type                "stream_state_sync"
        camera              1 | 2
        running             bool   - flux côté robot actif ?
        viewers             int    - compteurs unifiés (WS + REST)
        ws_viewers          int    - nombre de WebSocket consommateurs
        rest_viewers        int    - nombre de clients REST (mobile APP, scripts)
        keep_alive          bool   - mode économie d'énergie désactivé
        v_slam              bool   - mode V-SLAM activé sur ce flux
        idle_kill_ms        int    - ms avant fermeture timer auto (0 si pas planifié)
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
    encoded = json.dumps(payload)
    await manager.broadcast(encoded, "app")
    await manager.broadcast(encoded, "node")


async def handle_node_connection_change(connected: bool):
    """Bascule automatiquement les services si le PC Node se déconnecte/reconnecte."""
    for feature, target in preferred_ai_targets.items():
        if target == "node" or (not connected and preferred_ai_targets[feature] == "node"):
            active_target = "node" if connected else "disabled"
            if not connected:
                preferred_ai_targets[feature] = "disabled"
            controlled_msg = json.dumps({
                "type": "ai_control",
                "feature": feature,
                "target": active_target
            })
            await manager.broadcast(controlled_msg, "robot")
            await manager.broadcast(controlled_msg, "app")
