"""Local event counts and a presence-session gate; no image or identity data.

The caller supplies a monotonic clock to EventSessionGate and an explicit JSON
file path to StatsStore.  No application-data directory is accessed implicitly.
"""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Any


MILESTONES = (
    (1, "首次回望"),
    (10, "后排常客"),
    (50, "固定观众"),
    (100, "年度观众"),
)


class StatsDataError(ValueError):
    """The saved statistics are malformed or use an unsupported schema."""


class EventSessionGate:
    """Acquire across short detection dropouts, then emit once per visit.

    Acquisition needs both min_hits positive observations and threshold elapsed
    seconds. A dropout no longer than acquire_dropout_seconds preserves the
    candidate; a longer one resets it. Events fire only on positive frames.
    Once triggered, absence_seconds of missing observations rearms the gate.
    The caller supplies one observation per newly processed camera frame.
    Call reset when pausing, changing the ROI, or replacing the camera.
    """

    def __init__(
        self, threshold: float = 0.5, absence_seconds: float = 2.0,
        acquire_dropout_seconds: float = 0.4, min_hits: int = 3,
    ):
        self.threshold = float(threshold)
        self.absence_seconds = float(absence_seconds)
        self.acquire_dropout_seconds = float(acquire_dropout_seconds)
        self.min_hits = min_hits
        if not math.isfinite(self.threshold) or self.threshold < 0:
            raise ValueError("threshold must be finite and non-negative")
        if not math.isfinite(self.absence_seconds) or self.absence_seconds <= 0:
            raise ValueError("absence_seconds must be finite and positive")
        if not math.isfinite(self.acquire_dropout_seconds) or self.acquire_dropout_seconds < 0:
            raise ValueError("acquire_dropout_seconds must be finite and non-negative")
        if type(self.min_hits) is not int or self.min_hits < 1:
            raise ValueError("min_hits must be a positive integer")
        self.reset()

    def reset(self) -> None:
        self._visible_since: float | None = None
        self._absent_since: float | None = None
        self._triggered = False
        self._last_now: float | None = None
        self._hits = 0

    def _clear_candidate(self) -> None:
        self._visible_since = None
        self._hits = 0

    def observe(self, now: float, present: bool) -> bool:
        now = float(now)
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if self._last_now is not None and now < self._last_now:
            raise ValueError("now must use a non-decreasing monotonic clock")
        self._last_now = now

        if not present:
            if self._absent_since is None:
                self._absent_since = now
            absent_for = now - self._absent_since
            if absent_for >= self.absence_seconds:
                self._triggered = False
                self._clear_candidate()
            elif not self._triggered and absent_for > self.acquire_dropout_seconds:
                self._clear_candidate()
            return False

        if self._absent_since is not None:
            absent_for = now - self._absent_since
            if absent_for >= self.absence_seconds:
                self._triggered = False
                self._clear_candidate()
            elif not self._triggered and absent_for > self.acquire_dropout_seconds:
                self._clear_candidate()
            self._absent_since = None
        if self._triggered:
            return False
        if self._visible_since is None:
            self._visible_since = now
        self._hits += 1
        if self._hits >= self.min_hits and now - self._visible_since >= self.threshold:
            self._triggered = True
            self._clear_candidate()
            return True
        return False


def _empty_data() -> dict[str, Any]:
    return {
        "version": 1,
        "total": 0,
        "daily": {},
        "last_event_iso": None,
        "milestones": {},
    }


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _check_iso(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise StatsDataError(f"{field} must be an ISO datetime string")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise StatsDataError(f"{field} is not a valid ISO datetime") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StatsDataError(f"Duplicate statistics field: {key}")
        result[key] = value
    return result


def _validate_data(data: Any) -> dict[str, Any]:
    required = {"version", "total", "daily", "last_event_iso"}
    allowed = required | {"milestones"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise StatsDataError("Statistics are missing required fields")
    if set(data) - allowed:
        raise StatsDataError("Statistics contain unsupported fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise StatsDataError("Unsupported statistics version")
    if not _is_count(data["total"]) or not isinstance(data["daily"], dict):
        raise StatsDataError("Statistics counts must be non-negative integers")
    for day, count in data["daily"].items():
        try:
            valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid_day = False
        if not valid_day or not _is_count(count):
            raise StatsDataError("Daily statistics require YYYY-MM-DD dates and integer counts")
    if sum(data["daily"].values()) != data["total"]:
        raise StatsDataError("Total does not match the saved daily counts")
    if data["total"]:
        _check_iso(data["last_event_iso"], "last_event_iso")
    elif data["last_event_iso"] is not None:
        raise StatsDataError("Empty statistics must not have a last event")

    unlocks = data.get("milestones", {})
    if not isinstance(unlocks, dict):
        raise StatsDataError("Milestones must be an object")
    valid_thresholds = {str(threshold) for threshold, _ in MILESTONES}
    for threshold, when in unlocks.items():
        if threshold not in valid_thresholds or int(threshold) > data["total"]:
            raise StatsDataError("Milestone threshold does not match the event count")
        _check_iso(when, f"milestones.{threshold}")
    result = deepcopy(data)
    result["milestones"] = deepcopy(unlocks)
    return result


class StatsStore:
    """Persist local event counts atomically, with transactional memory updates.

    ``path`` is the complete JSON file path. Construction reads an existing
    file, but does not create a file for an empty store. Malformed saved data
    raises StatsDataError rather than silently resetting the user's counts.
    Save failures propagate to the caller and leave in-memory counts unchanged.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream, object_pairs_hook=_unique_object)
        except FileNotFoundError:
            self._data = _empty_data()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StatsDataError("Saved statistics are not valid UTF-8 JSON") from exc
        else:
            self._data = _validate_data(data)

    @property
    def total(self) -> int:
        with self._lock:
            return self._data["total"]

    def today_count(self, day: date) -> int:
        if isinstance(day, datetime) or not isinstance(day, date):
            raise TypeError("day must be a date")
        with self._lock:
            return self._data["daily"].get(day.isoformat(), 0)

    def milestones(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "threshold": threshold,
                    "title": title,
                    "unlocked": self._data["total"] >= threshold,
                    "unlocked_at": self._data["milestones"].get(str(threshold)),
                }
                for threshold, title in MILESTONES
            ]

    def summary(self, day: date) -> dict[str, Any]:
        with self._lock:
            return {
                "total": self._data["total"],
                "today": self.today_count(day),
                "last_event_iso": self._data["last_event_iso"],
                "milestones": self.milestones(),
            }

    def record_event(self, when: datetime) -> dict[str, Any]:
        if not isinstance(when, datetime):
            raise TypeError("when must be a datetime")
        with self._lock:
            candidate = deepcopy(self._data)
            previous_total = candidate["total"]
            candidate["total"] += 1
            day = when.date().isoformat()
            candidate["daily"][day] = candidate["daily"].get(day, 0) + 1
            candidate["last_event_iso"] = when.isoformat()
            for threshold, _ in MILESTONES:
                if previous_total < threshold <= candidate["total"]:
                    candidate["milestones"][str(threshold)] = when.isoformat()
            self._save(candidate)
            self._data = candidate
            return self.summary(when.date())

    def clear(self) -> None:
        with self._lock:
            candidate = _empty_data()
            self._save(candidate)
            self._data = candidate

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=f".{self.path.name}.", suffix=".tmp",
                dir=self.path.parent, delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
