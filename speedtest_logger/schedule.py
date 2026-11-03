"""Installing the background logger as a Windows scheduled task.

The task runs at logon in the current user context, which needs neither
administrator rights nor stored credentials. To cover a machine that is powered
on but has nobody logged in, change ``/SC ONLOGON`` to ``/SC ONSTART`` and run
the command from an elevated prompt.
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from speedtest_logger.config import base_dir

logger = logging.getLogger(__name__)

TASK_NAME = "SpeedtestLogger"
HEADLESS_EXE_NAME = "speedtest-logger.exe"

_CREATE_NO_WINDOW = 0x08000000


class ScheduleError(RuntimeError):
    """Raised when schtasks refuses an operation."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def message(self) -> str:
        text = self.stderr.strip() or self.stdout.strip()
        return text.splitlines()[0] if text else ""


def _run(arguments: list[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=30,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ScheduleError(str(error)) from error

    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def logger_command() -> str:
    """Return the command line that the scheduled task should run.

    A frozen build runs the separate console-less executable; from source it runs
    pythonw so that no console window appears at logon.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve().parent / HEADLESS_EXE_NAME}"'

    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless

    return f'"{interpreter}" "{base_dir() / "speedtest2.py"}" --headless'


def task_installed(task_name: str = TASK_NAME) -> bool:
    """Whether the scheduled task exists. Uses the exit code, not localised output."""
    try:
        return _run(["schtasks", "/Query", "/TN", task_name]).ok
    except ScheduleError:
        return False


def install_task(task_name: str = TASK_NAME, command: str | None = None) -> None:
    """Create or replace the logon task."""
    result = _run(
        [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/TR",
            command if command is not None else logger_command(),
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
        ]
    )
    if not result.ok:
        raise ScheduleError(result.message or "schtasks /Create failed.")


def remove_task(task_name: str = TASK_NAME) -> None:
    result = _run(["schtasks", "/Delete", "/TN", task_name, "/F"])
    if not result.ok:
        raise ScheduleError(result.message or "schtasks /Delete failed.")


def start_task(task_name: str = TASK_NAME) -> None:
    result = _run(["schtasks", "/Run", "/TN", task_name])
    if not result.ok:
        raise ScheduleError(result.message or "schtasks /Run failed.")


def stop_task(task_name: str = TASK_NAME) -> None:
    result = _run(["schtasks", "/End", "/TN", task_name])
    if not result.ok:
        raise ScheduleError(result.message or "schtasks /End failed.")


def installed_command(task_name: str = TASK_NAME) -> str:
    """Return the command the installed task runs, read from its XML definition."""
    try:
        result = _run(["schtasks", "/Query", "/TN", task_name, "/XML"])
    except ScheduleError:
        return ""

    if not result.ok:
        return ""

    text = result.stdout
    start = text.find("<Command>")
    end = text.find("</Command>")
    if start == -1 or end == -1:
        return ""

    command = text[start + len("<Command>") : end]
    arg_start = text.find("<Arguments>")
    arg_end = text.find("</Arguments>")
    if arg_start != -1 and arg_end != -1:
        command = f"{command} {text[arg_start + len('<Arguments>') : arg_end]}"
    return command.strip()
