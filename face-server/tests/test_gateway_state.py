"""Unit tests for GatewayState (config.py)."""
import asyncio
import pytest

from config import GatewayState, state


class TestGatewayState:
    """Tests for the GatewayState singleton and its methods."""

    def test_default_values(self):
        """GatewayState initialises with sane defaults."""
        gs = GatewayState()
        assert gs.latest_diagnostics == {}
        assert gs.gateway_telemetry["cpu_percent"] == 0
        assert gs.last_robot_state is None
        assert gs.last_robot_state_time == 0.0
        assert gs.stream_active == {1: False, 2: False}
        assert gs.preferred_ai_targets["chat"] == "disabled"

    def test_module_level_alias_is_same_object(self):
        """Module-level 'state' is a GatewayState instance."""
        assert isinstance(state, GatewayState)

    @pytest.mark.asyncio
    async def test_set_and_snapshot_diagnostics(self):
        """set_diagnostics + snapshot_diagnostics round-trip."""
        gs = GatewayState()
        diag = {"sensors": {"temp_c": 42.5}, "robot_status": "online"}
        await gs.set_diagnostics(diag)
        snap = gs.snapshot_diagnostics()
        assert snap == diag
        # Mutating the snapshot doesn't mutate the original
        snap["extra"] = "should-not-leak"
        assert "extra" not in gs.latest_diagnostics

    @pytest.mark.asyncio
    async def test_set_diagnostics_overwrites(self):
        """Second set_diagnostics replaces previous data entirely."""
        gs = GatewayState()
        await gs.set_diagnostics({"a": 1})
        await gs.set_diagnostics({"b": 2})
        snap = gs.snapshot_diagnostics()
        assert snap == {"b": 2}
        assert "a" not in snap

    @pytest.mark.asyncio
    async def test_concurrent_set_diagnostics(self):
        """Concurrent writes via the async lock don't corrupt state."""
        gs = GatewayState()

        async def writer(key: str):
            for i in range(50):
                await gs.set_diagnostics({key: i})

        await asyncio.gather(writer("x"), writer("y"), writer("z"))
        snap = gs.snapshot_diagnostics()
        # Only one key survives (last writer wins), but no corruption
        assert len(snap) == 1
        key, val = next(iter(snap.items()))
        assert key in ("x", "y", "z")
        assert isinstance(val, int)

    def test_lazy_lock_creation(self):
        """The _lock property creates an asyncio.Lock on first access."""
        gs = GatewayState()
        assert "_lock_obj" not in gs.__dict__
        lock = gs._lock
        assert isinstance(lock, asyncio.Lock)
        # Second access returns the same lock
        assert gs._lock is lock
        assert "_lock_obj" in gs.__dict__

    def test_preferred_ai_targets_defaults(self):
        """All AI targets default to 'disabled'."""
        gs = GatewayState()
        assert gs.preferred_ai_targets == {
            "tts": "disabled",
            "stt": "disabled",
            "chat": "disabled",
            "yolo": "disabled",
            "face_rec": "disabled",
        }
