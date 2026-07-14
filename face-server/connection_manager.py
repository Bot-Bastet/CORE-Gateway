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


# ─── Réconciliation des streams (orphelins + heartbeat REST) ────────────────
# Les viewers REST (app mobile) doivent re-POST /api/streams/{cam}/join au
# moins toutes les REST_VIEWER_TTL_SECONDS secondes (heartbeat). Sans
# heartbeat, un client mobile crashé resterait compté à vie et le flux ne
# s'arrêterait jamais.
REST_VIEWER_TTL_SECONDS = 75.0
RECONCILE_INTERVAL_SECONDS = 15.0
# Grâce au démarrage : laisse le temps aux navigateurs de se reconnecter
# après un restart du gateway avant de couper un flux robot encore actif.
STARTUP_GRACE_SECONDS = 90.0

# cam_id -> {client_id: last_seen_epoch}
rest_viewer_seen: dict = {1: {}, 2: {}}


def touch_rest_viewer(cam_id: int, client_id: str):
    import time as _time
    rest_viewer_seen[cam_id][client_id] = _time.time()


def drop_rest_viewer(cam_id: int, client_id: str):
    rest_viewer_seen[cam_id].pop(client_id, None)


def _prune_stale_rest_viewers() -> list:
    """Retire les viewers REST sans heartbeat depuis plus de REST_VIEWER_TTL_SECONDS."""
    import time as _time
    from config import rest_camera_listeners
    now = _time.time()
    pruned = []
    for cam_id in (1, 2):
        known = rest_camera_listeners[cam_id]
        seen = rest_viewer_seen[cam_id]
        for client_id in list(known):
            last = seen.get(client_id, 0.0)
            if now - last > REST_VIEWER_TTL_SECONDS:
                known.discard(client_id)
                seen.pop(client_id, None)
                pruned.append((cam_id, client_id))
    return pruned


async def stream_reconciler(mgr):
    """Boucle de fond : gère les flux orphelins.

    1. Purge les viewers REST sans heartbeat (mobile crashé, réseau coupé).
    2. Arme le kill différé 60 s si un flux tourne sans plus aucun consommateur
       (couvre : timers perdus au restart du gateway, exceptions, etc.).
    3. Au démarrage (après une grâce de 90 s) : si aucun consommateur suivi et
       keep_alive OFF, envoie un stop_camera préventif au robot — couvre le cas
       où le robot streame encore alors que le gateway a perdu son état
       (restart du conteneur, démarrage caméra hors gateway).
    """
    import time as _time
    from config import stream_active, stream_keep_alive, camera_stop_timers
    started_at = _time.time()
    boot_sweep_done = False
    while True:
        try:
            await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)

            pruned = _prune_stale_rest_viewers()
            for cam_id, client_id in pruned:
                print(f"[Streams] Viewer REST '{client_id}' expiré (cam{cam_id}, pas de heartbeat)")

            for cam_id in (1, 2):
                timer = camera_stop_timers[cam_id]
                timer_pending = timer is not None and not timer.done()
                if (stream_active.get(cam_id, False)
                        and should_schedule_idle_kill(cam_id)
                        and not timer_pending):
                    print(f"[Streams] Flux cam{cam_id} sans consommateur — kill différé {STOP_DELAY_SECONDS:.0f}s armé (réconciliation)")
                    camera_stop_timers[cam_id] = asyncio.create_task(stop_camera_delayed(cam_id, mgr))

            if not boot_sweep_done and _time.time() - started_at > STARTUP_GRACE_SECONDS:
                boot_sweep_done = True
                for cam_id in (1, 2):
                    if not stream_active.get(cam_id, False) and should_schedule_idle_kill(cam_id):
                        # Le gateway ne croit pas streamer et personne ne regarde :
                        # tout flux robot restant est orphelin → stop idempotent.
                        await mgr.broadcast(json.dumps({"type": "stop_camera", "camera": cam_id}), "robot")
                print("[Streams] Balayage post-démarrage : stop_camera préventif envoyé pour les flux orphelins")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Streams] Erreur boucle de réconciliation: {e}")
