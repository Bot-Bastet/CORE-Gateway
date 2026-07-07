"""Bastet Gateway — Main Application Factory.

Architecture:
    main.py          App factory, lifespan, middleware (~100 lines)
    config.py        Config, state, helpers, WebSocket manager
    models.py        Pydantic models
    auth.py          Password & token verification
    routes/
        accounts.py  Account CRUD, auth endpoints
        faces.py     Face gallery management
        system.py    Core state, MyGES, calibration, updates, health
        websocket.py WebSocket handlers (robot, node, app)
        dashboard.py Dashboard HTML, logo
    static/
        css/dashboard.css   All dashboard CSS (36 KB)
        js/dashboard.js     All dashboard JavaScript (252 KB)
    templates/
        dashboard.html      Pure HTML, references static CSS/JS
"""
import os
import time
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from config import (
    DATA_DIR, FACES_DIR, META_FILE, STATE_FILE,
    load_json, save_json, cleanup_duplicates, gateway_telemetry,
)

# ─── Lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    def _hourly_check():
        time.sleep(120)
        try:
            from updater import get_latest_release, get_current_version, _version_tuple
            current = get_current_version()
            release = get_latest_release()
            if release:
                latest = release.get("tag_name", "v0.0.0")
                if _version_tuple(latest) > _version_tuple(current):
                    print(f"[AutoUpdater] Mise a jour disponible: {latest} (actuelle: {current})")
        except Exception as e:
            print(f"[AutoUpdater] Erreur verification passive: {e}")
        while True:
            time.sleep(3600)
            try:
                state = load_json(STATE_FILE, default={"robot_status": "offline"})
                if state.get("robot_status", "offline") != "online":
                    try:
                        from updater import get_latest_release, get_current_version, _version_tuple
                        current = get_current_version()
                        release = get_latest_release()
                        if release:
                            latest = release.get("tag_name", "v0.0.0")
                            if _version_tuple(latest) > _version_tuple(current):
                                print(f"[AutoUpdater] Mise a jour disponible: {latest}")
                    except Exception as e:
                        print(f"[AutoUpdater] Erreur verification horaire: {e}")
            except Exception as e:
                print(f"[AutoUpdater] Erreur : {e}")

    def _gateway_telemetry_collector():
        prev_idle = prev_total = 0
        while True:
            try:
                with open("/proc/stat") as f:
                    parts = f.readline().split()
                    idle, total = int(parts[4]), sum(int(x) for x in parts[1:])
                d_idle, d_total = idle - prev_idle, total - prev_total
                prev_idle, prev_total = idle, total
                gateway_telemetry["cpu_percent"] = round((1 - d_idle / max(d_total, 1)) * 100, 1)
                with open("/proc/meminfo") as f:
                    mem = {}
                    for line in f:
                        k, *v = line.split(":")
                        mem[k.strip()] = int(v[0].strip().split()[0]) if v else 0
                gateway_telemetry["ram_percent"] = round((1 - mem.get("MemAvailable", 0) / max(mem.get("MemTotal", 1), 1)) * 100, 1)
                st = os.statvfs("/")
                gateway_telemetry["disk_percent"] = round((1 - st.f_bavail / max(st.f_blocks, 1)) * 100, 1)
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp") as f:
                        gateway_telemetry["temp_c"] = round(int(f.read().strip()) / 1000, 1)
                except Exception:
                    gateway_telemetry["temp_c"] = 0
                try:
                    with open("/proc/uptime") as f:
                        gateway_telemetry["uptime_s"] = float(f.read().split()[0])
                except Exception:
                    gateway_telemetry["uptime_s"] = 0
            except Exception:
                pass
            time.sleep(3)

    def _run_update():
        try:
            from updater import check_and_apply_update
            if check_and_apply_update():
                import signal
                os.kill(os.getpid(), signal.SIGTERM)
        except Exception as e:
            print(f"[AutoUpdater] Erreur : {e}")

    threading.Thread(target=_hourly_check, daemon=True).start()
    threading.Thread(target=_gateway_telemetry_collector, daemon=True).start()
    yield


# ─── App Creation ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Bastet Gateway API",
    description="API Gateway pour le robot Bastet (Faces, MyGES, Core State).",
    version="2.1.0",
    lifespan=lifespan,
)


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    # Force re-fetch of JS/HTML/CSS so deploys are visible without hard-reload.
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith('/static/') or path.startswith('/templates/') or path == '/' \
           or path.endswith(('.html', '.js', '.css')):
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response


app.add_middleware(NoCacheStaticMiddleware)

# Static files (CSS, JS)
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # nosemgrep: python.lang.security.audit.use-wildcard-origin — intentional: dashboard served on local network only
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Startup ───────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup_event():
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_duplicates()

# ─── Routers ───────────────────────────────────────────────────────────────

from routes.dashboard import router as dashboard_router
from routes.accounts import router as accounts_router
from routes.faces import router as faces_router
from routes.system import router as system_router
from routes.ws_robot import router as ws_robot_router
from routes.ws_app import router as ws_app_router
from routes.ws_node import router as ws_node_router

app.include_router(dashboard_router)
app.include_router(accounts_router)
app.include_router(faces_router)
app.include_router(system_router)
app.include_router(ws_robot_router)
app.include_router(ws_app_router)
app.include_router(ws_node_router)
