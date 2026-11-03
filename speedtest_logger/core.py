"""One measurement cycle: server choice, throughput, latency, packet loss, ISP."""

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime

import requests
import speedtest

from speedtest_logger.config import (
    BROWSER_USER_AGENT,
    IPINFO_URL,
    SERVER_API_URL,
    Settings,
)

logger = logging.getLogger(__name__)

STATUS_OK = "OK"
STATUS_BELOW = "BELOW MINIMUM THRESHOLD"

# Without this flag every ping call opens its own console window once the
# program is packaged with --windowed.
_CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Measurement:
    """A single completed measurement."""

    taken_at: datetime
    server_name: str
    server_country: str
    server_cc: str
    server_host: str
    server_distance_km: float
    ip_address: str
    isp: str
    download_mbit: float
    upload_mbit: float
    ping_ms: float
    packet_loss_pct: int
    status: str

    @property
    def meets_thresholds(self) -> bool:
        return self.status == STATUS_OK


class NoServerAvailable(RuntimeError):
    """Raised when no suitable test server could be determined."""


def fetch_candidate_servers(settings: Settings) -> list[dict]:
    """Return nearby test servers from the current Ookla endpoint.

    speedtest-cli still reads the legacy XML server lists, which today return a
    rotating handful of servers thousands of kilometres away. Measuring against
    those describes the route, not the line, so candidates are fetched here and
    handed to get_best_server() explicitly.
    """
    response = requests.get(
        SERVER_API_URL,
        params={"engine": "js", "limit": settings.server_list_limit},
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Referer": "https://www.speedtest.net/",
            "Accept": "application/json",
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list):
        raise NoServerAvailable("Unexpected server list format from speedtest.net.")

    candidates = []
    for entry in payload:
        if not isinstance(entry, dict) or not entry.get("url"):
            continue

        country_code = str(entry.get("cc", "")).upper()
        if settings.server_countries and country_code not in settings.server_countries:
            continue

        server = dict(entry)
        server["cc"] = country_code
        try:
            server["d"] = float(entry.get("distance", 0))
        except (TypeError, ValueError):
            server["d"] = 0.0
        candidates.append(server)

    candidates.sort(key=lambda server: server["d"])
    return candidates


def get_public_ip() -> tuple[str, str]:
    try:
        response = requests.get(IPINFO_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("ip", "Unknown"), data.get("org", "Unknown")
    except requests.RequestException:
        return "Unavailable", "Unavailable"


def extract_packet_loss(output: str) -> int:
    patterns = [
        r"\((\d+)%\s*(?:loss|verlust)\)",
        r"(\d+(?:\.\d+)?)%\s*packet loss",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match:
            return int(round(float(match.group(1))))
    return -1


def get_ping_loss(settings: Settings) -> int:
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
        return -1

    return extract_packet_loss(f"{result.stdout}\n{result.stderr}")


def get_status(settings: Settings, download_mbit: float, upload_mbit: float) -> str:
    if download_mbit >= settings.min_download and upload_mbit >= settings.min_upload:
        return STATUS_OK
    return STATUS_BELOW


def measure(settings: Settings) -> Measurement:
    """Run one full measurement cycle. Raises on failure; never returns partials."""
    taken_at = datetime.now()

    client = speedtest.Speedtest()
    candidates = fetch_candidate_servers(settings)
    if not candidates:
        raise NoServerAvailable(
            "No speedtest server available for SERVER_COUNTRIES="
            f"{settings.allowed_countries_label}."
        )

    server = client.get_best_server(servers=candidates)
    logger.debug("Selected server %s (%.0f km)", server.get("host"), server.get("d", 0.0))

    download_mbit = client.download() / 1_000_000
    upload_mbit = client.upload() / 1_000_000
    ip_address, isp = get_public_ip()

    return Measurement(
        taken_at=taken_at,
        server_name=str(server.get("name", "Unknown")),
        server_country=str(server.get("country", "Unknown")),
        server_cc=str(server.get("cc", "??")),
        server_host=str(server.get("host", "Unknown")),
        server_distance_km=float(server.get("d", 0.0)),
        ip_address=ip_address,
        isp=isp,
        download_mbit=download_mbit,
        upload_mbit=upload_mbit,
        ping_ms=float(client.results.ping),
        packet_loss_pct=get_ping_loss(settings),
        status=get_status(settings, download_mbit, upload_mbit),
    )
