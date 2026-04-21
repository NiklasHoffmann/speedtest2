# Speedtest Logger

Speedtest Logger is a small Python utility that runs recurring internet speed tests, records each result to a daily log file, and writes hourly and daily summary lines for later inspection. As a portfolio artifact, the repository shows a focused automation script built around operational monitoring rather than a one-off CLI command.

## What This Repository Demonstrates

- Long-running local automation with scheduled execution inside an infinite polling loop.
- Combining multiple data sources in one measurement cycle: throughput from `speedtest-cli`, public IP and ISP lookup from `ipinfo.io`, and packet-loss estimation from the system `ping` command.
- Simple observability design through append-only daily logs, derived hourly and daily averages, and explicit error logging.
- Basic configuration hygiene via `.env` and `.gitignore` for local paths.
- Practical threshold checking against expected download and upload contract values.

## Tech Stack

- Python
- `speedtest-cli`
- `requests`
- `python-dotenv`
- Windows `ping` command for packet-loss parsing

## Architecture Overview

The repository is intentionally compact. All runtime behavior lives in [speedtest2.py](speedtest2.py).

Core responsibilities:

- Load the log directory from `LOG_DIR` in `.env`, with a fallback path if the variable is missing.
- Run a speed test every 10 minutes.
- Enrich each measurement with server metadata, public IP, ISP, ping, and packet-loss information.
- Append raw measurements to a day-scoped text log.
- Accumulate in-memory results and emit hourly and daily average lines.
- Flag runs that fall below hard-coded minimum download and upload targets.

## Why It Is Technically Interesting

This is not a large application, but it does capture a real operational use case: turning intermittent network complaints into a time-series style local evidence trail. The code is opinionated in useful ways, especially around continuous execution, failure tolerance at the loop level, and post-run summarization rather than raw measurement only.

It also shows the tradeoffs of a pragmatic utility script. The implementation is optimized for a local Windows environment, relies on mutable in-memory state for aggregation, and keeps persistence deliberately simple with plain text logs instead of a database or dashboard.

## Local Development

### Prerequisites

- Python 3
- Internet access for the speed test and ISP lookup
- A Windows environment if you want packet-loss logging to work as implemented

### Install dependencies

```bash
pip install speedtest-cli requests python-dotenv
```

### Configure environment

Use [.env.example](.env.example) as the starting point for a local `.env` file:

```env
LOG_DIR=C:/Users/YourName/Desktop/SpeedtestLogs
MIN_DOWNLOAD=600
MIN_UPLOAD=10
INTERVAL_SECONDS=600
PING_HOST=8.8.8.8
PING_COUNT=10
PING_TIMEOUT_SECONDS=10
```

The script creates the directory if it does not already exist.

### Run the script

```bash
python speedtest2.py
```

The process runs continuously until interrupted.

## Configuration

### Environment variables

`LOG_DIR`

- Required for a meaningful local setup.
- Controls where daily log files are written.
- Falls back to `C:/StandardOrdner/Fallback` when unset.

`MIN_DOWNLOAD` and `MIN_UPLOAD`

- Optional throughput thresholds in Mbit/s.
- Used to mark measurements that fall below the expected contract baseline.

`INTERVAL_SECONDS`

- Optional polling interval for recurring test runs.
- Defaults to 600 seconds.

`PING_HOST`, `PING_COUNT`, and `PING_TIMEOUT_SECONDS`

- Optional controls for packet-loss measurement.
- Default to `8.8.8.8`, `10`, and `10`.

### Hard-coded runtime settings

These defaults are defined in [speedtest2.py](speedtest2.py) and can be overridden through environment variables.

## Logging Behavior

Each day gets a text log file at:

```text
<LOG_DIR>/log_speedtest_YYYY-MM-DD.txt
```

Entries include:

- Timestamp
- Selected speed test server
- Public IP and ISP
- Download speed
- Upload speed
- Ping
- Packet loss
- Threshold status

The script also appends derived summary rows for completed hourly and daily buckets.

Example output shape:

```text
2025-03-07 12:48:37 | Server: Frankfurt am Main (Germany, DE) [speedtest1.synlinq.de:8080] | IP: 95.90.21.104 | ISP: Vodafone GmbH | Download: 506.77 Mbit/s | Upload: 28.85 Mbit/s | Ping: 28.94 ms | Packet loss: 0% | BELOW MINIMUM THRESHOLD
2025-03-07 12:59:59 | HOURLY AVERAGE | Download: 510.50 Mbit/s | Upload: 29.10 Mbit/s | Ping: 27.80 ms | Packet loss: 1.20%
```

## Operational Notes

- Packet-loss parsing relies on the local `ping` command and supports common Windows and Unix-style loss output formats.
- Public IP and ISP metadata are fetched from `https://ipinfo.io/json` on every run.
- Exceptions during a measurement are printed and appended to the current day's log file; the outer loop continues running.
- Hourly and daily averages are written only when a period is completed, which avoids mixing the first measurement of a new hour or day into the previous bucket.

## Repository Scope

This repository is a focused single-script utility, not a packaged Python library or service. There is no test suite, dependency lock file, or deployment pipeline in the current codebase. The value here is in the implementation of a practical monitoring workflow, not in framework breadth.
