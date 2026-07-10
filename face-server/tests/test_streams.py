"""Tests for REST camera stream endpoints (join/leave/stop).

Note: manager.broadcast is mocked as AsyncMock in conftest,
so routes that call await manager.broadcast(...) won't fail.
"""
import json
import pytest


# ─── Helpers ───────────────────────────────────────────────────────────────

def _seed_camera_connected(client, auth_headers, cam1=True, cam2=False):
    """Seed latest_diagnostics so _camera_manifest() reports cameras as connected.

    We bypass POST /core/state because that route stores into _last_robot_state,
    NOT into latest_diagnostics (which is what _camera_manifest() reads).
    """
    import config

    devices = []
    if cam1:
        devices.append("/dev/video0")
    if cam2:
        devices.append("/dev/video2")

    diag = {
        "type": "telemetry_diagnostics",
        "sensors": {
            "available_video_devices": devices,
            "cam1_connected": cam1,
            "cam2_connected": cam2,
        },
        "robot_status": "online",
    }
    config.state.latest_diagnostics.update(diag)
    assert config.state.latest_diagnostics.get("sensors", {}).get(
        "cam1_connected"
    ) is cam1, "Seed failed: cam1_connected mismatch"
    assert config.state.latest_diagnostics.get("sensors", {}).get(
        "cam2_connected"
    ) is cam2, "Seed failed: cam2_connected mismatch"


def _assert_stream_state(resp, expected_status=200, expected_viewers=None):
    """Helper: assert response status and optionally viewer count."""
    assert resp.status_code == expected_status, f"Unexpected status: {resp.text}"
    data = resp.json()
    if expected_viewers is not None:
        assert data["viewers"] == expected_viewers, f"Expected {expected_viewers} viewers, got {data}"
    return data


class TestStreamsRead:
    """GET /api/cameras, GET /api/streams, GET /api/streams/{cam}."""

    def test_get_cameras_requires_token(self, client, bad_auth_headers):
        resp = client.get("/api/cameras", headers=bad_auth_headers)
        assert resp.status_code == 403

    def test_get_cameras_no_devices(self, client, auth_headers):
        resp = client.get("/api/cameras", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # JSON serialises int dict keys as strings
        assert data["cameras"]["1"]["connected"] is False
        assert data["cameras"]["2"]["connected"] is False

    def test_get_cameras_one_device(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers, cam1=True, cam2=False)
        resp = client.get("/api/cameras", headers=auth_headers)
        data = resp.json()
        assert data["cameras"]["1"]["connected"] is True
        assert data["cameras"]["2"]["connected"] is False

    def test_get_streams_empty(self, client, auth_headers):
        resp = client.get("/api/streams", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # JSON serialises int keys as strings
        for cam_key in ("1", "2"):
            s = data["streams"][cam_key]
            assert s["running"] is False
            assert s["viewers"] == 0

    def test_get_stream_invalid_cam(self, client, auth_headers):
        resp = client.get("/api/streams/3", headers=auth_headers)
        assert resp.status_code == 404


class TestStreamJoinLeave:
    """POST /api/streams/{cam}/join and DELETE /api/streams/{cam}/leave."""

    def test_join_invalid_cam(self, client, auth_headers):
        resp = client.post("/api/streams/3/join", json={}, headers=auth_headers)
        assert resp.status_code == 404

    def test_join_disconnected_camera(self, client, auth_headers):
        resp = client.post("/api/streams/1/join", json={}, headers=auth_headers)
        assert resp.status_code == 409

    def test_join_success(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        resp = client.post("/api/streams/1/join", json={
            "client_id": "mobile-001",
        }, headers=auth_headers)
        data = _assert_stream_state(resp, 200)
        assert data["client_id"] == "mobile-001"
        assert data["status"] in ("starting", "joined")

    def test_join_idempotent(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        r1 = client.post("/api/streams/1/join", json={
            "client_id": "app-abc",
        }, headers=auth_headers)
        assert r1.status_code == 200
        r2 = client.post("/api/streams/1/join", json={
            "client_id": "app-abc",
        }, headers=auth_headers)
        assert r2.status_code == 200
        assert r2.json()["status"] == "already"

    def test_join_auto_client_id(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        resp = client.post("/api/streams/1/join", json={}, headers=auth_headers)
        assert resp.status_code == 200
        cid = resp.json()["client_id"]
        assert cid.startswith("rest-")

    def test_leave_without_client_id(self, client, auth_headers):
        # TestClient.delete() doesn't accept json=/content= on this Starlette version.
        # Use client.request("DELETE", ...) which delegates directly to httpx.
        resp = client.request("DELETE", "/api/streams/1/leave", json={}, headers=auth_headers)
        assert resp.status_code == 400

    def test_leave_success(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        client.post("/api/streams/1/join", json={
            "client_id": "mobile-002",
        }, headers=auth_headers)
        resp = client.request(
            "DELETE", "/api/streams/1/leave",
            json={"client_id": "mobile-002"}, headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "left"

    def test_leave_invalid_cam(self, client, auth_headers):
        resp = client.request("DELETE", "/api/streams/3/leave", json={"client_id": "x"}, headers=auth_headers)
        assert resp.status_code == 404

    def test_multiple_viewers(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        r1 = client.post("/api/streams/1/join", json={
            "client_id": "a",
        }, headers=auth_headers)
        r2 = client.post("/api/streams/1/join", json={
            "client_id": "b",
        }, headers=auth_headers)
        assert r1.status_code == 200
        assert r2.status_code == 200
        state_resp = client.get("/api/streams/1", headers=auth_headers)
        assert state_resp.json()["viewers"] == 2


class TestStreamStop:
    """POST /api/streams/{cam}/stop — anti-griefing hard-stop."""

    def test_stop_invalid_cam(self, client, auth_headers):
        resp = client.post("/api/streams/3/stop", headers=auth_headers)
        assert resp.status_code == 404

    def test_stop_single_viewer(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        client.post("/api/streams/1/join", json={
            "client_id": "only-me",
        }, headers=auth_headers)
        resp = client.post("/api/streams/1/stop", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_stop_multiple_viewers_anti_griefing(self, client, auth_headers):
        _seed_camera_connected(client, auth_headers)
        client.post("/api/streams/1/join", json={
            "client_id": "viewer-1",
        }, headers=auth_headers)
        client.post("/api/streams/1/join", json={
            "client_id": "viewer-2",
        }, headers=auth_headers)
        resp = client.post("/api/streams/1/stop", headers=auth_headers)
        assert resp.status_code == 409
        assert "Anti-griefing" in resp.json()["detail"]
