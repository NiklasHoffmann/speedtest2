"""Running the background logger at logon, and controlling it while it runs.

Windows starts whatever shortcut sits in the per-user Startup folder when the
user logs on. That needs no administrator rights and no stored credentials,
which is why it is the default here. The scheduled-task route in schedule.py is
the alternative for a machine that should measure with nobody logged in; that
one does need elevation.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from speedtest_logger.config import Settings, base_dir

logger = logging.getLogger(__name__)

SHORTCUT_NAME = "SpeedtestLogger.lnk"
HEADLESS_EXE_NAME = "speedtest-logger.exe"
PID_FILENAME = "logger.pid"

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008


class AutostartError(RuntimeError):
    """Raised when the autostart entry could not be changed."""


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=30,
        creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _ps_quote(value: str) -> str:
    """Quote a value for a PowerShell single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise AutostartError("APPDATA is not set, cannot locate the Startup folder.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def logger_target() -> tuple[str, str]:
    """Return (executable, arguments) that start the headless logger.

    A frozen build starts the separate console-less executable; from source it
    starts pythonw so that no console window appears at logon.
    """
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent / HEADLESS_EXE_NAME), ""

    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless

    return str(interpreter), f'"{base_dir() / "speedtest2.py"}" --headless'


def autostart_enabled() -> bool:
    try:
        return shortcut_path().exists()
    except AutostartError:
        return False


def enable_autostart() -> None:
    """Create the Startup shortcut, replacing any existing one."""
    target, arguments = logger_target()
    link = shortcut_path()

    try:
        link.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise AutostartError(str(error)) from error

    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({_ps_quote(str(link))}); "
        f"$link.TargetPath = {_ps_quote(target)}; "
        f"$link.Arguments = {_ps_quote(arguments)}; "
        f"$link.WorkingDirectory = {_ps_quote(str(base_dir()))}; "
        "$link.Description = 'Speedtest Logger background measurement'; "
        "$link.Save()"
    )

    try:
        result = _run_powershell(script)
    except (OSError, subprocess.SubprocessError) as error:
        raise AutostartError(str(error)) from error

    if result.returncode != 0 or not link.exists():
        message = (result.stderr or result.stdout).strip().splitlines()
        raise AutostartError(message[0] if message else "Shortcut could not be created.")


def disable_autostart() -> None:
    try:
        shortcut_path().unlink(missing_ok=True)
    except OSError as error:
        raise AutostartError(str(error)) from error


def read_autostart_target() -> str:
    """Return the command the Startup shortcut points at, for display."""
    link = shortcut_path()
    if not link.exists():
        return ""

    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({_ps_quote(str(link))}); "
        "Write-Output ($link.TargetPath + ' ' + $link.Arguments)"
    )
    try:
        result = _run_powershell(script)
    except (OSError, subprocess.SubprocessError):
        return ""

    return result.stdout.strip() if result.returncode == 0 else ""


# ------------------------------------------------------------------- process


def pid_path(settings: Settings) -> Path:
    return settings.log_dir / PID_FILENAME


def write_pid_file(settings: Settings) -> None:
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        pid_path(settings).write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logger.warning("PID file could not be written.")


def clear_pid_file(settings: Settings) -> None:
    try:
        pid_path(settings).unlink(missing_ok=True)
    except OSError:
        logger.warning("PID file could not be removed.")


def _pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return f'"{pid}"' in (result.stdout or "")


def background_pid(settings: Settings) -> int | None:
    """Return the PID of a running background logger, or None."""
    try:
        raw = pid_path(settings).read_text(encoding="utf-8").strip()
    except OSError:
        return None

    try:
        pid = int(raw)
    except ValueError:
        return None

    return pid if _pid_alive(pid) else None


def start_background(settings: Settings) -> None:
    """Start the headless logger as a detached process."""
    if background_pid(settings) is not None:
        return

    target, arguments = logger_target()
    command = [target, *_split_arguments(arguments)]

    try:
        subprocess.Popen(  # noqa: S603 - fixed command built from our own paths
            command,
            cwd=str(base_dir()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(_DETACHED_PROCESS | _CREATE_NO_WINDOW) if os.name == "nt" else 0,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AutostartError(str(error)) from error


def stop_background(settings: Settings) -> None:
    """Terminate a running background logger, if there is one."""
    pid = background_pid(settings)
    if pid is None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                timeout=15,
                creationflags=_CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError) as error:
        raise AutostartError(str(error)) from error

    clear_pid_file(settings)


def _split_arguments(arguments: str) -> list[str]:
    """Split the argument string built by logger_target, honouring its quoting."""
    if not arguments:
        return []

    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for character in arguments:
        if character == '"':
            in_quotes = not in_quotes
        elif character == " " and not in_quotes:
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(character)
    if current:
        parts.append("".join(current))
    return parts
