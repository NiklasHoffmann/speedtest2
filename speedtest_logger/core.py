"""One measurement cycle, measured by the official Ookla client."""

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

from speedtest_logger import ookla
from speedtest_logger.config import Settings

logger = logging.getLogger(__name__)

STATUS_OK = "OK"
STATUS_BELOW = "BELOW MINIMUM THRESHOLD"

UNKNOWN_LOSS = -1.0

# Without this flag every ping call opens its own console window once the
# program is packaged with --windowed.
_CREATE_NO_WINDOW = 0x08000000

# The client reports countries by name, while SERVER_COUNTRIES is written in
# codes. Only the neighbours worth filtering on are listed; a full country name
# in SERVER_COUNTRIES works regardless of this table.
_COUNTRY_CODES = {
    "AT": "AUSTRIA",
    "BE": "BELGIUM",
    "CH": "SWITZERLAND",
    "CZ": "CZECHIA",
    "DE": "GERMANY",
    "DK": "DENMARK",
    "FR": "FRANCE",
    "GB": "UNITED KINGDOM",
    "IT": "ITALY",
    "LU": "LUXEMBOURG",
    "NL": "NETHERLANDS",
    "PL": "POLAND",
    "US": "UNITED STATES",
}


@dataclass(frozen=True)
class Measurement:
    """A single completed measurement."""

    taken_at: datetime
    server_name: str
    server_location: str
    server_country: str
    server_host: str
    server_id: int | None
    ip_address: str
    isp: str
    download_mbit: float
    upload_mbit: float
    ping_ms: float
    jitter_ms: float
    packet_loss_pct: float
    result_url: str
    status: str

    @property
    def meets_thresholds(self) -> bool:
        return self.status == STATUS_OK


class NoServerAvailable(RuntimeError):
    """Raised when no server matches the configured filter."""


def country_matches(allowed: tuple[str, ...], country: str) -> bool:
    if not allowed:
        return True

    name = country.strip().upper()
    return any(token == name or _COUNTRY_CODES.get(token) == name for token in allowed)


def select_server_id(settings: Settings) -> int | None:
    """Return the server to measure against, or None to let the client decide.

    A pinned SERVER_ID is the strongest option for a comparable series, because
    it holds the server constant and leaves the line as the only variable.
    """
    if settings.server_id is not None:
        return settings.server_id

    if not settings.server_countries:
        return None

    servers = ookla.list_servers(settings)
    matching = [
        server
        for server in servers
        if country_matches(settings.server_countries, str(server.get("country", "")))
    ]

    if not matching:
        raise NoServerAvailable(
            "No server matched SERVER_COUNTRIES="
            f"{settings.allowed_countries_label} among {len(servers)} offered."
        )

    try:
        return int(matching[0]["id"])
    except (KeyError, TypeError, ValueError):
        return None


def extract_packet_loss(output: str) -> float:
    patterns = [
        r"\((\d+)%\s*(?:loss|verlust)\)",
        r"(\d+(?:\.\d+)?)%\s*packet loss",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return UNKNOWN_LOSS


def get_ping_loss(settings: Settings) -> float:
    """Estimate packet loss with the system ping.

    Only used when the chosen server does not report packet loss itself.
    """
    ping_flag = "-n" if os.name == "nt" else "-c"
    try:
        result = subprocess.run(
            ["ping", ping_flag, str(settings.ping_count), settings.ping_host],
            capture_output=True,
            text=True,
            timeout=settings.ping_timeout,
            encoding="utf-8",
            errors="ignore",
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_LOSS

    return extract_packet_loss(f"{result.stdout}\n{result.stderr}")


def get_status(settings: Settings, download_mbit: float, upload_mbit: float) -> str:
    if download_mbit >= settings.min_download and upload_mbit >= settings.min_upload:
        return STATUS_OK
    return STATUS_BELOW


def _bandwidth_mbit(section: object) -> float:
    """Convert the bandwidth field, which the client reports in bytes per second."""
    if not isinstance(section, dict):
        return 0.0
    try:
        return float(section.get("bandwidth", 0)) * 8 / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def _number(section: object, key: str, default: float = 0.0) -> float:
    if not isinstance(section, dict):
        return default
    try:
        return float(section.get(key, default))
    except (TypeError, ValueError):
        return default


def measure(settings: Settings) -> Measurement:
    """Run one full measurement cycle. Raises on failure; never returns partials."""
    taken_at = datetime.now()

    payload = ookla.measure(settings, server_id=select_server_id(settings))

    download_mbit = _bandwidth_mbit(payload.get("download"))
    upload_mbit = _bandwidth_mbit(payload.get("upload"))

    raw_loss = payload.get("packetLoss")
    if isinstance(raw_loss, (int, float)):
        packet_loss = float(raw_loss)
    else:
        # Not every server reports it, so fall back to the ping estimate.
        packet_loss = get_ping_loss(settings)

    server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
    interface = payload.get("interface") if isinstance(payload.get("interface"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}

    try:
        server_id = int(server.get("id")) if server.get("id") is not None else None
    except (TypeError, ValueError):
        server_id = None

    return Measurement(
        taken_at=taken_at,
        server_name=str(server.get("name", "Unknown")),
        server_location=str(server.get("location", "Unknown")),
        server_country=str(server.get("country", "Unknown")),
        server_host=str(server.get("host", "Unknown")),
        server_id=server_id,
        ip_address=str(interface.get("externalIp", "Unavailable")),
        isp=str(payload.get("isp", "Unavailable")),
        download_mbit=download_mbit,
        upload_mbit=upload_mbit,
        ping_ms=_number(payload.get("ping"), "latency"),
        jitter_ms=_number(payload.get("ping"), "jitter"),
        packet_loss_pct=packet_loss,
        result_url=str(result.get("url", "")),
        status=get_status(settings, download_mbit, upload_mbit),
    )
