"""Command line entry point for the speedtest logger.

Without arguments this keeps measuring every INTERVAL_SECONDS, which is what the
scheduled background task runs. The desktop window lives in gui.py.
"""

import argparse
import logging
import sys

from speedtest_logger.autostart import clear_pid_file, write_pid_file
from speedtest_logger.config import configure_logging, load_settings
from speedtest_logger.runner import run_forever, run_once

logger = logging.getLogger("speedtest2")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="speedtest2",
        description="Run recurring internet speed tests and append them to a daily log.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single measurement and exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="suppress console output; for the scheduled background task",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    settings = load_settings()
    configure_logging(settings, console=not arguments.headless)

    logger.info("Log directory: %s", settings.log_dir)
    logger.info(
        "Interval: %ss | Server countries: %s | Thresholds: %s/%s Mbit/s",
        settings.interval,
        settings.allowed_countries_label,
        settings.min_download,
        settings.min_upload,
    )

    if arguments.once:
        return 0 if run_once(settings) is not None else 1

    logger.info("Starting recurring speed tests. Press Ctrl+C to stop.")
    write_pid_file(settings)
    try:
        run_forever(settings)
    except KeyboardInterrupt:
        logger.info("Stopping speed tests.")
    finally:
        clear_pid_file(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
