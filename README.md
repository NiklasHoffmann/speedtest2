# Speedtest Logger

Speedtest Logger is a small Python utility that runs recurring internet speed tests, records each result to a daily log file, and writes hourly and daily summary lines for later inspection. As a portfolio artifact, the repository shows a focused automation script built around operational monitoring rather than a one-off CLI command.

## What This Repository Demonstrates

- Long-running local automation with scheduled execution inside an infinite polling loop.
- Working around a decayed upstream dependency: `speedtest-cli` still reads Ookla's legacy XML server lists, which now return a rotating handful of servers thousands of kilometres away, so the candidate servers are fetched from the current JSON endpoint and passed to the library explicitly.
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
- Fetch nearby test servers from the current speedtest.net JSON endpoint and restrict them to the countries listed in `SERVER_COUNTRIES`.
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
SERVER_COUNTRIES=DE
SERVER_LIST_LIMIT=10
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

`SERVER_COUNTRIES`

- Optional comma-separated allowlist of country codes for test server selection.
- Defaults to `DE`. An empty value disables the filter and accepts any server the endpoint returns.

`SERVER_LIST_LIMIT`

- Optional number of candidate servers requested from the endpoint before filtering.
- Defaults to `10`. A narrow `SERVER_COUNTRIES` filter may need a higher limit to leave usable candidates.

### Hard-coded runtime settings

These defaults are defined in [speedtest2.py](speedtest2.py) and can be overridden through environment variables.

## Server Selection

`speedtest-cli` 2.1.3 resolves servers through Ookla's legacy XML endpoints (`speedtest-servers-static.php` and its siblings). Those endpoints are effectively retired: they still answer, but with a rotating remnant of roughly ten servers worldwide. The library's own `get_best_server()` then works correctly on unusable input and picks, for example, a server in Uzbekistan for a client in Germany. The resulting numbers describe the route to Central Asia, not the local line.

To avoid that, [speedtest2.py](speedtest2.py) fetches candidates from the current JSON endpoint instead:

```text
https://www.speedtest.net/api/js/servers?engine=js&limit=<SERVER_LIST_LIMIT>
```

The request needs a browser `User-Agent` and a `Referer: https://www.speedtest.net/` header; without them the endpoint answers `403`. Results are filtered to `SERVER_COUNTRIES`, sorted by distance, and handed to `get_best_server(servers=...)`, which skips the legacy list entirely. The selected server's distance is recorded in each log line so an implausible pick is visible in the log itself.

If the endpoint fails or no server matches the filter, the measurement is skipped and an `ERROR` line is logged instead. A visible gap is preferable to a plausible-looking but route-limited number in a log intended as evidence.

## Logging Behavior

Each day gets a text log file at:

```text
<LOG_DIR>/log_speedtest_YYYY-MM-DD.txt
```

Entries include:

- Timestamp
- Selected speed test server and its distance
- Public IP and ISP
- Download speed
- Upload speed
- Ping
- Packet loss
- Threshold status

The script also appends derived summary rows for completed hourly and daily buckets.

Example output shape:

```text
2025-03-07 12:48:37 | Server: Frankfurt am Main (Germany, DE) [speedtest1.synlinq.de:8080, 48 km] | IP: 95.90.21.104 | ISP: Vodafone GmbH | Download: 506.77 Mbit/s | Upload: 28.85 Mbit/s | Ping: 28.94 ms | Packet loss: 0% | BELOW MINIMUM THRESHOLD
2025-03-07 12:59:59 | HOURLY AVERAGE | Download: 510.50 Mbit/s | Upload: 29.10 Mbit/s | Ping: 27.80 ms | Packet loss: 1.20%
```

## Operational Notes

- Packet-loss parsing relies on the local `ping` command and supports common Windows and Unix-style loss output formats.
- Public IP and ISP metadata are fetched from `https://ipinfo.io/json` on every run.
- The candidate server list is fetched on every run, so a changing location or a server going offline is picked up without a restart.
- Exceptions during a measurement are printed and appended to the current day's log file; the outer loop continues running.
- Hourly and daily averages are written only when a period is completed, which avoids mixing the first measurement of a new hour or day into the previous bucket.

## Repository Scope

This repository is a focused single-script utility, not a packaged Python library or service. There is no test suite, dependency lock file, or deployment pipeline in the current codebase. The value here is in the implementation of a practical monitoring workflow, not in framework breadth.
