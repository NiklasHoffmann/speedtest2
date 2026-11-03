"""The measurement loop, driven from a console, a window, or a scheduled task."""

import logging
import threading
from datetime import datetime
from typing import Callable

from speedtest_logger.config import Settings
from speedtest_logger.core import Measurement, measure
from speedtest_logger.storage import (
    Aggregator,
    LockBusy,
    append_log_entry,
    format_error,
    format_measurement,
    measurement_lock,
)

logger = logging.getLogger(__name__)

ResultCallback = Callable[[Measurement, list[str]], None]
MessageCallback = Callable[[str], None]


def run_once(
    settings: Settings,
    aggregator: Aggregator | None = None,
    on_result: ResultCallback | None = None,
    on_error: MessageCallback | None = None,
) -> Measurement | None:
    """Measure once and append the result. Returns None when the cycle was skipped."""
    aggregator = aggregator if aggregator is not None else Aggregator(settings)

    try:
        with measurement_lock(settings):
            measurement = measure(settings)

            # Summaries are written first so that they sit above the measurement
            # that closed their bucket.
            summaries = aggregator.record(measurement)
            for summary in summaries:
                logger.info(summary)

            entry = format_measurement(measurement)
            append_log_entry(settings, entry, log_date=measurement.taken_at.date())
            logger.info(entry)
    except LockBusy as error:
        # A skipped cycle is not a measurement failure, so it stays out of the
        # evidence log and only shows up in diagnostics.
        logger.warning("Measurement skipped: %s", error)
        if on_error is not None:
            on_error(str(error))
        return None
    except Exception as error:  # noqa: BLE001 - the loop must survive any failure
        moment = datetime.now()
        entry = format_error(moment, str(error))
        logger.error(entry)
        try:
            append_log_entry(settings, entry, log_date=moment.date())
        except OSError:
            logger.error("Error entry could not be written to the log file.")
        if on_error is not None:
            on_error(str(error))
        return None

    if on_result is not None:
        on_result(measurement, summaries)
    return measurement


def run_forever(
    settings: Settings,
    stop_event: threading.Event | None = None,
    on_result: ResultCallback | None = None,
    on_error: MessageCallback | None = None,
) -> None:
    """Measure every INTERVAL_SECONDS until stop_event is set."""
    stop = stop_event if stop_event is not None else threading.Event()
    aggregator = Aggregator(settings)

    # A bucket left open by the previous run would otherwise never be summarised.
    for summary in aggregator.flush_completed(datetime.now()):
        logger.info(summary)

    while not stop.is_set():
        run_once(settings, aggregator, on_result, on_error)
        if stop.wait(settings.interval):
            break
