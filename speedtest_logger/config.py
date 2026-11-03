"""Runtime settings, read once from the environment.

Both front ends (the headless logger and the desktop window) share this module
so that a setting means the same thing in either one.
"""

import logging
import os
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

import dotenv

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = "C:/StandardOrdner/Fallback"


def base_dir() -> Path:
    """Return the directory that holds .env, for both source and frozen runs.

    A PyInstaller build is started by double-clicking it, so the working
    directory is whatever the shell happened to pick. Anchoring on the
    executable keeps the configuration next to the program.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Validated runtime configuration."""

    log_dir: Path
    min_download: int
    min_upload: int
    interval: int
    ping_host: str
    ping_count: int
    ping_timeout: int
    server_countries: tuple[str, ...]
    server_id: int | None
    ookla_cli_path: Path | None
    measurement_timeout: int

    @property
    def allowed_countries_label(self) -> str:
        return ",".join(self.server_countries) or "any"


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid value for %s: %r. Using default %s.", name, raw_value, default)
        return default

    if value < minimum:
        logger.warning("Value for %s must be >= %s. Using default %s.", name, minimum, default)
        return default

    return value


def _env_optional_int(name: str, minimum: int = 0) -> int | None:
    """Read an optional integer. Absent and invalid both mean not set."""
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return None

    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid value for %s: %r. Ignoring it.", name, raw_value)
        return None

    if value < minimum:
        logger.warning("Value for %s must be >= %s. Ignoring it.", name, minimum)
        return None

    return value


def _env_optional_path(name: str) -> Path | None:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return None
    return Path(raw_value.strip()).expanduser()


def configure_logging(settings: Settings, console: bool = True) -> None:
    """Send diagnostics to a rotating file, and to the console when there is one.

    A windowed build has no stdout, so print() would write into nothing. The
    diagnostics file is separate from the measurement log on purpose: one is the
    evidence trail, the other is for debugging the program.
    """
    handlers: list[logging.Handler] = []

    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                settings.log_dir / "speedtest-logger.log",
                maxBytes=1_000_000,
                backupCount=2,
                encoding="utf-8",
            )
        )
    except OSError:
        pass

    if console and sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def env_file() -> Path | None:
    """Return the .env that applies, preferring the one next to the program."""
    for candidate in (base_dir() / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_settings() -> Settings:
    """Read .env from next to the program and return the resulting settings."""
    found = env_file()
    if found is not None:
        dotenv.load_dotenv(found)
    else:
        logger.warning("No .env found; falling back to defaults and process environment.")

    return Settings(
        log_dir=Path(os.getenv("LOG_DIR", DEFAULT_LOG_DIR)).expanduser(),
        min_download=_env_int("MIN_DOWNLOAD", 600, minimum=0),
        min_upload=_env_int("MIN_UPLOAD", 10, minimum=0),
        interval=_env_int("INTERVAL_SECONDS", 600, minimum=1),
        ping_host=os.getenv("PING_HOST", "8.8.8.8").strip() or "8.8.8.8",
        ping_count=_env_int("PING_COUNT", 10, minimum=1),
        ping_timeout=_env_int("PING_TIMEOUT_SECONDS", 10, minimum=1),
        server_countries=tuple(
            code.strip().upper()
            for code in os.getenv("SERVER_COUNTRIES", "DE").split(",")
            if code.strip()
        ),
        server_id=_env_optional_int("SERVER_ID", minimum=1),
        ookla_cli_path=_env_optional_path("OOKLA_CLI_PATH"),
        measurement_timeout=_env_int("MEASUREMENT_TIMEOUT_SECONDS", 180, minimum=30),
    )
