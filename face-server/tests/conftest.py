"""Shared fixtures for CORE-Gateway tests.

Strategy:
  - Temp dirs are created BEFORE importing config so FACES_DIR points to a
    temp location via FACES_DIR env var.
  - After config is imported, ALL Path constants (DATA_DIR, USERS_FILE,
    STATE_FILE, etc.) are redirected to temp dirs via monkeypatch.setattr.
  - manager.broadcast is mocked as an AsyncMock so REST routes that call
    it don't need real WebSocket connections.
  - GatewayState is reset between tests for isolation.
"""
import os
import tempfile
from pathlib import Path

import pytest
from unittest.mock import AsyncMock


# ─── Temp directories (created BEFORE any config import) ──────────────────

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="bastet_test_"))
_TEST_DATA = _TEST_ROOT / "data"
_TEST_FACES = _TEST_ROOT / "faces"
_TEST_DATA.mkdir(parents=True, exist_ok=True)
_TEST_FACES.mkdir(parents=True, exist_ok=True)

os.environ["FACES_DIR"] = str(_TEST_FACES)
os.environ["API_TOKEN"] = "test-token"


# ─── Path redirection fixture ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _patch_paths(monkeypatch):
    """Redirect all config data paths to temp dirs and reset GatewayState."""
    import config

    # Redirect Path constants
    monkeypatch.setattr(config, "DATA_DIR", _TEST_DATA)
    monkeypatch.setattr(config, "MYGES_FILE", _TEST_DATA / "myges.json")
    monkeypatch.setattr(config, "STATE_FILE", _TEST_DATA / "core_state.json")
    monkeypatch.setattr(config, "USERS_FILE", _TEST_DATA / "users.json")
    monkeypatch.setattr(config, "CALIBRATION_FILE", _TEST_DATA / "calibration.json")
    monkeypatch.setattr(config, "GATEWAY_UPDATE_FILE", _TEST_DATA / "gateway_update_state.json")
    monkeypatch.setattr(config, "ROBOT_UPDATE_FILE", _TEST_DATA / "robot_update_state.json")
    monkeypatch.setattr(config, "ARDUINO_UPDATE_FILE", _TEST_DATA / "arduino_update_state.json")
    monkeypatch.setattr(config, "CAMERA_CALIB_1_FILE", _TEST_DATA / "camera_calib_1.json")
    monkeypatch.setattr(config, "CAMERA_CALIB_2_FILE", _TEST_DATA / "camera_calib_2.json")
    monkeypatch.setattr(config, "STEREO_CALIB_FILE", _TEST_DATA / "camera_calib_stereo.json")

    # Mock manager.broadcast so REST routes that use it don't need real WS
    monkeypatch.setattr(config.manager, "broadcast", AsyncMock())

    # ── Reset mutable GatewayState for test isolation ──────────────────
    config.state.last_robot_state = None
    config.state.last_robot_state_time = 0.0
    # _last_robot_state is a module-level variable that POST /core/state
    # REBINDS (not mutates). monkeypatch.setattr doesn't survive rebinding,
    # so we reset it directly here.  It starts as state.last_robot_state
    # (None) but gets overwritten to a dict by each POST.
    config._last_robot_state = None
    config._last_robot_state_time = 0.0
    config.state.latest_diagnostics.clear()
    config.state.stream_active.update({1: False, 2: False})
    config.state.stream_v_slam.update({1: False, 2: False})
    config.state.stream_keep_alive.update({1: False, 2: False})
    config.state.active_camera_listeners.update({1: set(), 2: set()})
    config.state.rest_camera_listeners.update({1: set(), 2: set()})
    config.state.camera_stop_timers.update({1: None, 2: None})
    config.state.camera_idle_kill_at.update({1: 0.0, 2: 0.0})
    config.state.preferred_ai_targets.update({
        "tts": "disabled", "stt": "disabled", "chat": "disabled",
        "yolo": "disabled", "face_rec": "disabled",
    })
    # Clean up on-disk state leaked from previous tests so every test
    # starts with a blank slate.  Tests that need persistence (register
    # then login) write their own data inside the same test.
    for _f in (
        config.STATE_FILE, config.USERS_FILE, config.MYGES_FILE,
        config.CALIBRATION_FILE, config.CAMERA_CALIB_1_FILE,
        config.CAMERA_CALIB_2_FILE, config.STEREO_CALIB_FILE,
    ):
        if _f.exists():
            _f.unlink()

    yield


# ─── TestClient fixture ───────────────────────────────────────────────────

@pytest.fixture
def client():
    """FastAPI TestClient with patched paths and mocked broadcast."""
    from main import app
    from fastapi.testclient import TestClient
    return TestClient(app)


# ─── Auth headers ─────────────────────────────────────────────────────────

@pytest.fixture
def auth_headers():
    return {"X-API-Token": "test-token"}


@pytest.fixture
def bad_auth_headers():
    return {"X-API-Token": "wrong-token"}
