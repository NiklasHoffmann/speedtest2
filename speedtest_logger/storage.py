"""Writing and reading the log trail, plus aggregation that outlives a restart."""

import json
import logging
import math
import os
import re
import statistics
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Iterator, Sequence

from speedtest_logger.config import Settings
from speedtest_logger.core import Measurement

logger = logging.getLogger(__name__)

STATE_FILENAME = "aggregation-state.json"
LOCK_FILENAME = "measurement.lock"
STALE_LOCK_AFTER = timedelta(minutes=15)

HOURLY_LABEL = "HOURLY AVERAGE"
DAILY_LABEL = "DAILY AVERAGE"


def ensure_log_dir(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)


def get_text_log_path(settings: Settings, log_date: date | None = None) -> Path:
    target_date = log_date or datetime.now().date()
    return settings.log_dir / f"log_speedtest_{target_date:%Y-%m-%d}.txt"


def append_log_entry(settings: Settings, message: str, log_date: date | None = None) -> None:
    ensure_log_dir(settings)
    log_path = get_text_log_path(settings, log_date)
    with log_path.open("a", encoding="utf-8", errors="ignore") as file:
        file.write(f"{message}\n")


def format_measurement(measurement: Measurement) -> str:
    return (
        f"{measurement.taken_at:%Y-%m-%d %H:%M:%S} | "
        f"Server: {measurement.server_name} "
        f"({measurement.server_country}, {measurement.server_cc}) "
        f"[{measurement.server_host}, {measurement.server_distance_km:.0f} km] | "
        f"IP: {measurement.ip_address} | ISP: {measurement.isp} | "
        f"Download: {measurement.download_mbit:.2f} Mbit/s | "
        f"Upload: {measurement.upload_mbit:.2f} Mbit/s | "
        f"Ping: {measurement.ping_ms:.2f} ms | "
        f"Packet loss: {measurement.packet_loss_pct}% | {measurement.status}"
    )


@dataclass(frozen=True)
class Stats:
    """Distribution of one metric over a period."""

    count: int
    mean: float
    median: float
    p05: float
    maximum: float


def percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile, matching the common numpy default."""
    if not values:
        return 0.0

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarise(values: Sequence[float]) -> Stats:
    if not values:
        return Stats(0, 0.0, 0.0, 0.0, 0.0)

    return Stats(
        count=len(values),
        mean=sum(values) / len(values),
        median=statistics.median(values),
        p05=percentile(values, 0.05),
        maximum=max(values),
    )


def format_average_entry(
    timestamp: datetime, label: str, rows: Sequence[Sequence[float]]
) -> str:
    """Summarise a period.

    The mean alone is easy to attack, because a single bad measurement drags it
    down and a single good one props it up. The median says what the connection
    usually does and the 5th percentile says how bad its bad moments are, which
    is the pair that actually carries an argument. With few samples in a period
    the 5th percentile converges on the minimum, which is the honest answer
    rather than a defect.
    """
    download = summarise([row[0] for row in rows])
    upload = summarise([row[1] for row in rows])
    ping = summarise([row[2] for row in rows])
    loss = summarise([row[3] for row in rows])

    return (
        f"{timestamp:%Y-%m-%d %H:%M:%S} | {label} | n={download.count} | "
        f"Download: avg {download.mean:.2f}, median {download.median:.2f}, "
        f"p05 {download.p05:.2f} Mbit/s | "
        f"Upload: avg {upload.mean:.2f}, median {upload.median:.2f}, "
        f"p05 {upload.p05:.2f} Mbit/s | "
        f"Ping: avg {ping.mean:.2f}, median {ping.median:.2f} ms | "
        f"Packet loss: avg {loss.mean:.2f}, max {loss.maximum:.2f}%"
    )


def format_error(moment: datetime, message: str) -> str:
    return f"{moment:%Y-%m-%d %H:%M:%S} | ERROR | {message}"


class LockBusy(RuntimeError):
    """Raised when another process is already measuring."""


@contextmanager
def measurement_lock(settings: Settings) -> Iterator[None]:
    """Serialise measurements across processes.

    Two concurrent measurements would both saturate the line and both record a
    wrong number, so the window and the background logger take turns.
    """
    ensure_log_dir(settings)
    lock_path = settings.log_dir / LOCK_FILENAME

    def claim() -> int:
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(handle, f"{os.getpid()} {datetime.now().isoformat()}\n".encode())
        return handle

    try:
        handle = claim()
    except FileExistsError:
        try:
            age = datetime.now() - datetime.fromtimestamp(lock_path.stat().st_mtime)
        except OSError:
            age = timedelta(0)

        if age < STALE_LOCK_AFTER:
            raise LockBusy(
                f"Another measurement has been running for {int(age.total_seconds())}s."
            ) from None

        logger.warning("Removing stale lock file after %s.", age)
        try:
            lock_path.unlink()
            handle = claim()
        except (OSError, FileExistsError) as error:
            raise LockBusy("Lock file could not be claimed.") from error

    try:
        yield
    finally:
        os.close(handle)
        try:
            lock_path.unlink()
        except OSError:
            logger.warning("Lock file %s could not be removed.", lock_path)


class Aggregator:
    """Hourly and daily averages, persisted so a restart does not lose a bucket."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._hourly: list[list[float]] = []
        self._daily: list[list[float]] = []
        self._hour_bucket: datetime | None = None
        self._day_bucket: date | None = None
        self._load()

    @property
    def _state_path(self) -> Path:
        return self._settings.log_dir / STATE_FILENAME

    def _reset(self) -> None:
        self._hourly = []
        self._daily = []
        self._hour_bucket = None
        self._day_bucket = None

    def _load(self) -> None:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        if not isinstance(raw, dict):
            return

        try:
            hour_bucket = raw.get("hour_bucket")
            day_bucket = raw.get("day_bucket")
            self._hour_bucket = datetime.fromisoformat(hour_bucket) if hour_bucket else None
            self._day_bucket = date.fromisoformat(day_bucket) if day_bucket else None
            self._hourly = [list(map(float, row)) for row in raw.get("hourly", [])]
            self._daily = [list(map(float, row)) for row in raw.get("daily", [])]
        except (TypeError, ValueError):
            logger.warning("Aggregation state unreadable, starting from empty buckets.")
            self._reset()

    def _save(self) -> None:
        ensure_log_dir(self._settings)
        payload = {
            "hour_bucket": self._hour_bucket.isoformat() if self._hour_bucket else None,
            "day_bucket": self._day_bucket.isoformat() if self._day_bucket else None,
            "hourly": self._hourly,
            "daily": self._daily,
        }
        try:
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Aggregation state could not be written to %s.", self._state_path)

    def flush_completed(self, reference_time: datetime) -> list[str]:
        """Close every bucket that ended before reference_time, return what was written."""
        written: list[str] = []
        hour_bucket = reference_time.replace(minute=0, second=0, microsecond=0)
        day_bucket = reference_time.date()

        if self._hour_bucket is not None and self._hour_bucket != hour_bucket and self._hourly:
            summary_time = self._hour_bucket + timedelta(hours=1) - timedelta(seconds=1)
            entry = format_average_entry(summary_time, HOURLY_LABEL, self._hourly)
            append_log_entry(self._settings, entry, log_date=self._hour_bucket.date())
            written.append(entry)
            self._hourly = []

        if self._day_bucket is not None and self._day_bucket != day_bucket and self._daily:
            summary_time = datetime.combine(self._day_bucket, dt_time(23, 59, 59))
            entry = format_average_entry(summary_time, DAILY_LABEL, self._daily)
            append_log_entry(self._settings, entry, log_date=self._day_bucket)
            written.append(entry)
            self._daily = []

        if written:
            self._save()
        return written

    def record(self, measurement: Measurement) -> list[str]:
        """Add a measurement, return any summary lines its arrival completed."""
        written = self.flush_completed(measurement.taken_at)

        self._hour_bucket = measurement.taken_at.replace(minute=0, second=0, microsecond=0)
        self._day_bucket = measurement.taken_at.date()

        row = [
            measurement.download_mbit,
            measurement.upload_mbit,
            measurement.ping_ms,
            float(measurement.packet_loss_pct),
        ]
        self._hourly.append(row)
        self._daily.append(row)
        self._save()
        return written


@dataclass(frozen=True)
class LogEntry:
    """A parsed line from a log file, for display."""

    raw: str
    taken_at: datetime | None
    kind: str
    label: str
    server: str
    download_mbit: float | None
    upload_mbit: float | None
    ping_ms: float | None
    packet_loss_pct: float | None
    status: str
    sample_count: int | None = None
    download_median: float | None = None
    download_p05: float | None = None


_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_SERVER = re.compile(r"Server:\s*(.+?)\s*\|")
# "avg " is optional so that log files written before the summary lines gained
# median and percentile columns still parse.
_NUMBERS = {
    "download_mbit": re.compile(r"Download:\s*(?:avg\s+)?(-?[\d.]+)"),
    "upload_mbit": re.compile(r"Upload:\s*(?:avg\s+)?(-?[\d.]+)"),
    "ping_ms": re.compile(r"Ping:\s*(?:avg\s+)?(-?[\d.]+)"),
    "packet_loss_pct": re.compile(r"Packet loss:\s*(?:avg\s+)?(-?[\d.]+)"),
}
_MEDIAN = re.compile(r"Download:[^|]*median\s+(-?[\d.]+)")
_P05 = re.compile(r"Download:[^|]*p05\s+(-?[\d.]+)")
_SAMPLE_COUNT = re.compile(r"\|\s*n=(\d+)\s*\|")


def parse_log_line(line: str) -> LogEntry | None:
    """Parse one log line. Returns None for anything unrecognised."""
    text = line.strip()
    if not text:
        return None

    stamp_match = _TIMESTAMP.match(text)
    taken_at = None
    if stamp_match:
        try:
            taken_at = datetime.strptime(stamp_match.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            taken_at = None

    if "| ERROR |" in text:
        kind, label = "error", "ERROR"
    elif HOURLY_LABEL in text:
        kind, label = "average", HOURLY_LABEL
    elif DAILY_LABEL in text:
        kind, label = "average", DAILY_LABEL
    elif "Download:" in text:
        kind, label = "measurement", ""
    else:
        return None

    values: dict[str, float | None] = {}
    for field_name, pattern in _NUMBERS.items():
        match = pattern.search(text)
        try:
            values[field_name] = float(match.group(1)) if match else None
        except ValueError:
            values[field_name] = None

    server_match = _SERVER.search(text)
    status = text.rsplit("|", 1)[-1].strip() if kind == "measurement" else ""

    def optional_float(pattern: re.Pattern[str]) -> float | None:
        match = pattern.search(text)
        try:
            return float(match.group(1)) if match else None
        except ValueError:
            return None

    count_match = _SAMPLE_COUNT.search(text)

    return LogEntry(
        raw=text,
        taken_at=taken_at,
        kind=kind,
        label=label,
        server=server_match.group(1).strip() if server_match else "",
        download_mbit=values["download_mbit"],
        upload_mbit=values["upload_mbit"],
        ping_ms=values["ping_ms"],
        packet_loss_pct=values["packet_loss_pct"],
        status=status,
        sample_count=int(count_match.group(1)) if count_match else None,
        download_median=optional_float(_MEDIAN),
        download_p05=optional_float(_P05),
    )


def read_log_entries(
    settings: Settings, log_date: date | None = None, limit: int = 200
) -> list[LogEntry]:
    """Return the most recent parsed entries of one day log file, oldest first."""
    path = get_text_log_path(settings, log_date)
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    return [parsed for line in lines[-limit:] if (parsed := parse_log_line(line))]
