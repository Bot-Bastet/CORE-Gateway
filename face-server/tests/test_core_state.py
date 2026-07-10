"""Tests for GET/POST /core/state."""
import time
import pytest


class TestCoreStatePost:
    """POST /core/state."""

    def test_post_state_requires_token(self, client, bad_auth_headers):
        resp = client.post("/core/state", json={
            "robot_status": "online",
            "sensors": {"temp_c": 42.0},
        }, headers=bad_auth_headers)
        assert resp.status_code == 403

    def test_post_state_online(self, client, auth_headers):
        payload = {
            "robot_status": "online",
            "seen_person": "Alice",
            "robot_version": "v0.2.19",
            "arduino_version": "v0.2.19",
            "sensors": {
                "temp_c": 38.5,
                "available_video_devices": ["/dev/video0", "/dev/video2"],
            },
            "ai_state": {"chat": "active"},
        }
        resp = client.post("/core/state", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_post_state_preserves_online_on_hibernating(self, client, auth_headers):
        """robot_status=hibernating does NOT overwrite 'online'."""
        import config

        # Seed: set state to online first
        r1 = client.post("/core/state", json={
            "robot_status": "online",
            "sensors": {"temp_c": 40.0},
        }, headers=auth_headers)
        assert r1.status_code == 200, f"Seed POST failed: {r1.text}"

        # Now post hibernating — should be ignored, status stays online
        r2 = client.post("/core/state", json={
            "robot_status": "hibernating",
        }, headers=auth_headers)
        assert r2.status_code == 200, f"Hibernating POST failed: {r2.text}"
        # POST /core/state writes to _last_robot_state (module var), not state.last_robot_state
        assert config._last_robot_state["robot_status"] == "online"

    def test_post_normalizes_camera_manifest(self, client, auth_headers):
        """Posting sensors with available_video_devices sets cam1/2_connected."""
        import config

        resp = client.post("/core/state", json={
            "robot_status": "online",
            "sensors": {
                "available_video_devices": ["/dev/video0", "/dev/video2"],
            },
        }, headers=auth_headers)
        assert resp.status_code == 200, f"POST failed: {resp.text}"
        sensors = config._last_robot_state["sensors"]
        assert sensors["cam1_connected"] is True
        assert sensors["cam2_connected"] is True

    def test_post_single_camera(self, client, auth_headers):
        """Single device → cam1 connected, cam2 not."""
        import config

        resp = client.post("/core/state", json={
            "robot_status": "online",
            "sensors": {
                "available_video_devices": ["/dev/video0"],
            },
        }, headers=auth_headers)
        assert resp.status_code == 200, f"POST failed: {resp.text}"
        sensors = config._last_robot_state["sensors"]
        assert sensors["cam1_connected"] is True
        assert sensors["cam2_connected"] is False


class TestCoreStateGet:
    """GET /core/state."""

    def test_get_state_requires_token(self, client, bad_auth_headers):
        resp = client.get("/core/state", headers=bad_auth_headers)
        assert resp.status_code == 403

    def test_get_state_defaults_to_offline(self, client, auth_headers):
        resp = client.get("/core/state", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["robot_status"] == "offline"

    def test_get_state_returns_posted_data(self, client, auth_headers):
        client.post("/core/state", json={
            "robot_status": "online",
            "seen_person": "Bob",
            "sensors": {"temp_c": 36.6},
        }, headers=auth_headers)
        resp = client.get("/core/state", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["robot_status"] == "online"
        assert data["seen_person"] == "Bob"

    def test_get_state_includes_active_streams(self, client, auth_headers):
        client.post("/core/state", json={
            "robot_status": "online",
        }, headers=auth_headers)
        resp = client.get("/core/state", headers=auth_headers)
        data = resp.json()
        assert "active_streams" in data
        assert data["active_streams"] == {"1": False, "2": False}

    def test_get_state_goes_offline_after_timeout(self, client, auth_headers, monkeypatch):
        """After 25 seconds without an update, status becomes 'offline'."""
        import config

        # Seed a recent state
        r = client.post("/core/state", json={
            "robot_status": "online",
        }, headers=auth_headers)
        assert r.status_code == 200

        # Verify it's online right after posting
        r_get = client.get("/core/state", headers=auth_headers)
        assert r_get.json()["robot_status"] == "online"

        # Advance time past the 25s threshold.
        # POST /core/state writes _last_robot_state_time, not state.last_robot_state_time.
        monkeypatch.setattr("time.time", lambda: config._last_robot_state_time + 30)
        resp = client.get("/core/state", headers=auth_headers)
        assert resp.json()["robot_status"] == "offline"

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
