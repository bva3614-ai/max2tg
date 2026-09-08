"""Throttle state for connection notices, kept on disk.

The escalating throttle (1h, then 3h, then daily) only works if it outlives the
process. It did not: the counters were locals in a closure, so every restart
started a fresh series. Over 12 days the bot restarted 26 times and sent 4927
connection notices — 30% of everything in the log, multiplied by every route.

Two rules live here:

1. The throttle counter survives restarts, because it is written to disk.
2. "Connection restored" is sent only if "connection lost" was actually sent.
   Previously the restore notice had no throttle at all, which is why there
   were 3826 of them against 1101 losses: every reconnect announced itself
   even when the matching loss had been suppressed.

A corrupt or unreadable state file is treated as "no state yet". Losing the
throttle history costs one extra notice; refusing to start costs every message.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# nth notice -> how long to stay quiet before the next one is allowed
_ESCALATION = {1: timedelta(hours=1), 2: timedelta(hours=3)}
_ESCALATION_MAX = timedelta(days=1)


def _quiet_period(count: int) -> timedelta:
    return _ESCALATION.get(count, _ESCALATION_MAX)


class NotifyState:
    """Disconnect/auth notice bookkeeping backed by a JSON file."""

    def __init__(self, path: str | None):
        self.path = path
        self.count = 0
        self.last_notice: datetime | None = None
        self.last_auth_alert: datetime | None = None
        self.loss_announced = False
        self._load()

    # ---- persistence ----

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.count = int(data.get("count", 0))
            self.last_notice = _parse_time(data.get("last_notice"))
            self.last_auth_alert = _parse_time(data.get("last_auth_alert"))
            self.loss_announced = bool(data.get("loss_announced", False))
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Notify state unreadable (%s) — starting a fresh series", exc)

    def _save(self) -> None:
        if not self.path:
            return
        data = {
            "count": self.count,
            "last_notice": _format_time(self.last_notice),
            "last_auth_alert": _format_time(self.last_auth_alert),
            "loss_announced": self.loss_announced,
        }
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            # Write through a temp file so a crash mid-write cannot leave the
            # bot with a half-written state it will refuse to parse next start.
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".notify-state-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(tmp, self.path)
            except OSError:
                os.unlink(tmp)
                raise
        except OSError as exc:
            # State is an optimisation, not a requirement to forward messages.
            log.warning("Could not persist notify state: %s", exc)

    # ---- disconnect notices ----

    def should_announce_loss(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.last_notice is None:
            return True
        return now - self.last_notice >= _quiet_period(self.count)

    def record_loss(self, now: datetime | None = None) -> None:
        self.count += 1
        self.last_notice = now or datetime.now()
        self.loss_announced = True
        self._save()

    def should_announce_restore(self) -> bool:
        """Only worth saying if the matching loss was announced."""
        return self.loss_announced

    def record_restore(self) -> None:
        self.loss_announced = False
        self._save()

    # ---- auth alerts ----

    def should_alert_auth(self, interval_sec: int, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.last_auth_alert is None:
            return True
        return (now - self.last_auth_alert).total_seconds() >= interval_sec

    def record_auth_alert(self, now: datetime | None = None) -> None:
        self.last_auth_alert = now or datetime.now()
        self._save()


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _format_time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
