"""Tests for app/notify_state.py — the throttle that has to survive restarts."""

import json
from datetime import datetime, timedelta

import pytest

from app.notify_state import NotifyState


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "notify-state.json")


class TestSurvivesRestart:
    """The whole point: a restart must not hand out a fresh quota of notices."""

    def test_throttle_outlives_the_process(self, state_path):
        t0 = datetime(2026, 4, 5, 10, 0, 0)
        first = NotifyState(state_path)
        assert first.should_announce_loss(t0) is True
        first.record_loss(t0)

        # Process dies and comes back five minutes later.
        restarted = NotifyState(state_path)
        assert restarted.count == 1
        assert restarted.should_announce_loss(t0 + timedelta(minutes=5)) is False

    def test_escalation_continues_across_restarts(self, state_path):
        t0 = datetime(2026, 4, 5, 10, 0, 0)
        s = NotifyState(state_path)
        s.record_loss(t0)

        # After the 1st notice the quiet period is 1h.
        s = NotifyState(state_path)
        assert s.should_announce_loss(t0 + timedelta(minutes=59)) is False
        assert s.should_announce_loss(t0 + timedelta(hours=1, seconds=1)) is True
        s.record_loss(t0 + timedelta(hours=1, seconds=1))

        # After the 2nd it is 3h, still counted correctly in a new process.
        s = NotifyState(state_path)
        assert s.count == 2
        assert s.should_announce_loss(t0 + timedelta(hours=3)) is False
        assert s.should_announce_loss(t0 + timedelta(hours=4, seconds=2)) is True
        s.record_loss(t0 + timedelta(hours=4, seconds=2))

        # From the 3rd on it is daily.
        s = NotifyState(state_path)
        assert s.count == 3
        assert s.should_announce_loss(t0 + timedelta(hours=20)) is False
        assert s.should_announce_loss(t0 + timedelta(days=1, hours=5)) is True

    def test_pairing_survives_restart(self, state_path):
        s = NotifyState(state_path)
        s.record_loss(datetime(2026, 4, 5, 10, 0, 0))
        assert NotifyState(state_path).should_announce_restore() is True

        NotifyState(state_path).record_restore()
        assert NotifyState(state_path).should_announce_restore() is False


class TestRestorePairing:
    def test_restore_not_announced_before_any_loss(self, state_path):
        assert NotifyState(state_path).should_announce_restore() is False

    def test_restore_clears_the_flag(self, state_path):
        s = NotifyState(state_path)
        s.record_loss()
        assert s.should_announce_restore() is True
        s.record_restore()
        assert s.should_announce_restore() is False


class TestAuthAlerts:
    def test_first_alert_always_allowed(self, state_path):
        assert NotifyState(state_path).should_alert_auth(3600) is True

    def test_alert_throttled_then_allowed(self, state_path):
        t0 = datetime(2026, 4, 5, 10, 0, 0)
        s = NotifyState(state_path)
        s.record_auth_alert(t0)

        s = NotifyState(state_path)
        assert s.should_alert_auth(3600, t0 + timedelta(minutes=30)) is False
        assert s.should_alert_auth(3600, t0 + timedelta(hours=1, seconds=1)) is True


class TestDurability:
    def test_corrupt_file_starts_a_fresh_series(self, state_path):
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        s = NotifyState(state_path)
        assert s.count == 0
        assert s.should_announce_loss() is True

    def test_partial_file_is_tolerated(self, state_path):
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"count": 2, "last_notice": "not-a-timestamp"}, fh)
        s = NotifyState(state_path)
        assert s.count == 2
        assert s.last_notice is None
        assert s.should_announce_loss() is True

    def test_no_path_keeps_state_in_memory(self):
        s = NotifyState(None)
        s.record_loss(datetime(2026, 4, 5, 10, 0, 0))
        assert s.count == 1
        assert s.should_announce_loss(datetime(2026, 4, 5, 10, 5, 0)) is False

    def test_unwritable_path_does_not_raise(self, tmp_path):
        # A directory where the file should be: writing must fail quietly,
        # because forwarding messages matters more than throttle bookkeeping.
        blocked = tmp_path / "state-is-a-dir"
        blocked.mkdir()
        s = NotifyState(str(blocked))
        s.record_loss()
        assert s.count == 1

    def test_written_file_is_valid_json(self, state_path):
        s = NotifyState(state_path)
        s.record_loss(datetime(2026, 4, 5, 10, 0, 0))
        with open(state_path, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["count"] == 1
        assert data["loss_announced"] is True
        assert data["last_notice"].startswith("2026-04-05T10:00:00")

    def test_no_temp_files_left_behind(self, tmp_path, state_path):
        s = NotifyState(state_path)
        s.record_loss()
        s.record_restore()
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".notify-state-")]
        assert leftovers == []
