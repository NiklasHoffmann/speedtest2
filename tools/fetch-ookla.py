"""Fetch the official Ookla speedtest client into this directory.

The binary is deliberately not committed. Ookla distributes it under its own
licence, and vendoring a third-party executable into a public repository is the
wrong default. This script downloads a pinned version, verifies its checksum,
and extracts it as ookla-speedtest.exe next to itself.

Run it from the repository root:

    python tools/fetch-ookla.py
"""

import hashlib
import io
import platform
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.2.0"
TARGET_NAME = "ookla-speedtest.exe"

# Pinned so that an unexpected payload fails loudly instead of being executed.
RELEASES = {
    "win64": {
        "url": f"https://install.speedtest.net/app/cli/ookla-speedtest-{VERSION}-win64.zip",
        "sha256": "13e3d888b845d301a556419e31f14ab9bff57e3f06089ef2fd3bdc9ba6841efa",
        "member": "speedtest.exe",
    },
}


def platform_key() -> str:
    if sys.platform != "win32":
        raise SystemExit(
            f"Only win64 is pinned here; {sys.platform} needs its own entry in RELEASES. "
            "The client is available for Linux and macOS from install.speedtest.net."
        )
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit(f"Unsupported architecture: {platform.machine()}")
    return "win64"


def main() -> int:
    release = RELEASES[platform_key()]
    destination = Path(__file__).resolve().parent / TARGET_NAME

    print(f"Downloading {release['url']}")
    try:
        with urllib.request.urlopen(release["url"], timeout=60) as response:
            payload = response.read()
    except (urllib.error.URLError, OSError) as error:
        raise SystemExit(f"Download failed: {error}") from error

    digest = hashlib.sha256(payload).hexdigest()
    if digest != release["sha256"]:
        raise SystemExit(
            "Checksum mismatch, refusing to extract.\n"
            f"  expected {release['sha256']}\n"
            f"  received {digest}\n"
            "Either the pinned version was replaced upstream or the download was "
            "tampered with. Verify against install.speedtest.net before updating "
            "the pin in this script."
        )
    print(f"Checksum verified: {digest}")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        with archive.open(release["member"]) as source:
            destination.write_bytes(source.read())

    print(f"Wrote {destination} ({destination.stat().st_size} bytes)")
    print("The client needs --accept-license and --accept-gdpr, which the logger passes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
