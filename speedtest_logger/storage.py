"""Writing and reading the log trail, plus aggregation that outlives a restart."""

import json
import logging
import os
import re
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


def format_average_entry(
    timestamp: datetime, label: str, averages: tuple[float, float, float, float]
) -> str:
    avg_download, avg_upload, avg_ping, avg_loss = averages
    return (
        f"{timestamp:%Y-%m-%d %H:%M:%S} | {label} | "
        f"Download: {avg_download:.2f} Mbit/s | Upload: {avg_upload:.2f} Mbit/s | "
        f"Ping: {avg_ping:.2f} ms | Packet loss: {avg_loss:.2f}%"
    )


def format_error(moment: datetime, message: str) -> str:
    return f"{moment:%Y-%m-%d %H:%M:%S} | ERROR | {message}"


def calculate_average(
    results: Sequence[Sequence[float]],
) -> tuple[float, float, float, float]:
    if not results:
        return 0.0, 0.0, 0.0, 0.0

    count = len(results)
    return (
        sum(row[0] for row in results) / count,
        sum(row[1] for row in results) / count,
        sum(row[2] for row in results) / count,
        sum(row[3] for row in results) / count,
    )


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
            entry = format_average_entry(
                summary_time, HOURLY_LABEL, calculate_average(self._hourly)
            )
            append_log_entry(self._settings, entry, log_date=self._hour_bucket.date())
            written.append(entry)
            self._hourly = []

        if self._day_bucket is not None and self._day_bucket != day_bucket and self._daily:
            summary_time = datetime.combine(self._day_bucket, dt_time(23, 59, 59))
            entry = format_average_entry(
                summary_time, DAILY_LABEL, calculate_average(self._daily)
            )
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


_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_SERVER = re.compile(r"Server:\s*(.+?)\s*\|")
_NUMBERS = {
    "download_mbit": re.compile(r"Download:\s*(-?[\d.]+)"),
    "upload_mbit": re.compile(r"Upload:\s*(-?[\d.]+)"),
    "ping_ms": re.compile(r"Ping:\s*(-?[\d.]+)"),
    "packet_loss_pct": re.compile(r"Packet loss:\s*(-?[\d.]+)"),
}


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
