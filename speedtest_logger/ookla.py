"""The official Ookla speedtest CLI as the measurement engine.

The Python speedtest-cli library this project started with under-reports
throughput badly on a fast line: against the same server in Mainz it measured
between 345 and 584 Mbit/s where the official client measured 909.91. For a log
intended as evidence that is the dangerous direction, because the provider runs
the official client, gets a much higher number, and the whole series loses its
credibility.

The binary is not vendored into the repository. It is fetched by
tools/fetch-ookla.py and found at runtime through OOKLA_CLI_PATH, a tools
directory next to the program, or PATH.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from speedtest_logger.config import Settings, base_dir

logger = logging.getLogger(__name__)

BINARY_NAME = "ookla-speedtest.exe" if os.name == "nt" else "ookla-speedtest"
TOOLS_DIRNAME = "tools"

# The client refuses to run unattended without these acknowledgements.
_CONSENT = ("--accept-license", "--accept-gdpr")
_CREATE_NO_WINDOW = 0x08000000


class OoklaUnavailable(RuntimeError):
    """Raised when the official client cannot be found or run."""


def candidate_paths(settings: Settings) -> list[Path]:
    candidates: list[Path] = []

    configured = settings.ookla_cli_path
    if configured is not None:
        candidates.append(configured)

    candidates.append(base_dir() / TOOLS_DIRNAME / BINARY_NAME)
    candidates.append(base_dir() / BINARY_NAME)

    on_path = shutil.which(BINARY_NAME) or shutil.which("speedtest")
    if on_path:
        candidates.append(Path(on_path))

    return candidates


def find_binary(settings: Settings) -> Path:
    """Locate the official client, or explain where it was looked for.

    A configured OOKLA_CLI_PATH that does not exist is a misconfiguration worth
    saying out loud, even though the remaining candidates are still tried.
    """
    configured = settings.ookla_cli_path
    if configured is not None and not configured.is_file():
        logger.warning("OOKLA_CLI_PATH points at %s, which is not a file.", configured)

    for candidate in candidate_paths(settings):
        if candidate.is_file():
            return candidate

    looked = ", ".join(str(path) for path in candidate_paths(settings))
    raise OoklaUnavailable(
        "The official Ookla speedtest client was not found. Run "
        f"tools/fetch-ookla.py or set OOKLA_CLI_PATH. Looked in: {looked}"
    )


def _invoke(binary: Path, arguments: list[str], timeout: int) -> dict:
    command = [str(binary), *_CONSENT, "--format=json", *arguments]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise OoklaUnavailable(f"The measurement timed out after {timeout}s.") from error
    except (OSError, subprocess.SubprocessError) as error:
        raise OoklaUnavailable(str(error)) from error

    payload = _last_json_object(completed.stdout or "")
    if payload is None:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise OoklaUnavailable(
            detail[-1] if detail else f"The client exited with code {completed.returncode}."
        )

    if payload.get("type") == "log" or "error" in payload:
        raise OoklaUnavailable(str(payload.get("error") or payload.get("message")))

    return payload


def _last_json_object(text: str) -> dict | None:
    """Return the last JSON object in the output.

    The client prints progress objects before the result when it feels like it,
    so the last complete object is the one that matters.
    """
    found: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            found = parsed
    return found


def list_servers(settings: Settings, binary: Path | None = None) -> list[dict]:
    """Return the servers the client considers usable from here."""
    target = binary if binary is not None else find_binary(settings)
    payload = _invoke(target, ["--servers"], timeout=60)
    servers = payload.get("servers")
    return [entry for entry in servers if isinstance(entry, dict)] if isinstance(servers, list) else []


def measure(
    settings: Settings, server_id: int | None = None, binary: Path | None = None
) -> dict:
    """Run one measurement and return the parsed result object."""
    target = binary if binary is not None else find_binary(settings)
    arguments = ["--server-id", str(server_id)] if server_id is not None else []
    payload = _invoke(target, arguments, timeout=settings.measurement_timeout)

    if "download" not in payload or "upload" not in payload:
        raise OoklaUnavailable("The client returned no throughput values.")

    return payload
